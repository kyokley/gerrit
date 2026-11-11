"""Task-private, pinned Git/OpenSSH transport and operation evidence."""
from __future__ import annotations

import json
import os
import re
import shutil
import signal
import stat
import subprocess
import sys
import tempfile
import time
import urllib.parse
from pathlib import Path


class TransportError(RuntimeError):
    pass


_MAX_CONFIG = 64 * 1024
_MAX_OUTPUT = 64 * 1024


def _write_private(path, data, mode=0o600):
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, mode)
    os.fchmod(fd, mode)
    with os.fdopen(fd, "wb") as f:
        f.write(data)


def _destination(settings):
    host = settings.ssh_host
    if ":" in host and not host.startswith("["):
        host = f"[{host}]"
    user = urllib.parse.quote(settings.username, safe="")
    project = "/".join(urllib.parse.quote(part, safe="") for part in settings.project.split("/"))
    return f"ssh://{user}@{host}:{settings.ssh_port}/{project}"


class Transport:
    def __init__(self, settings, key_path, session_dir):
        self.settings = settings
        self.key_path = os.path.abspath(key_path)
        self.session_dir = os.path.abspath(session_dir)
        self.proof_path = os.path.join(self.session_dir, "transport-proof.jsonl")
        self.config_path = os.path.join(self.session_dir, "transport.json")
        self.git_wrapper = os.path.join(self.session_dir, "git-wrapper")
        self.ssh_path = shutil.which("ssh")
        self.git_path = shutil.which("git")
        if not self.ssh_path or not self.git_path:
            raise TransportError("Git and OpenSSH executables are required")
        self.ssh_path = os.path.abspath(self.ssh_path)
        self.git_path = os.path.abspath(self.git_path)
        self.known_hosts = self._snapshot_known_hosts(settings.known_hosts)
        self.expected_url = _destination(settings)
        self.remote_name = None
        self.bound_repository = None
        self.git_dir = None
        self._save_config()
        _write_private(self.proof_path, b"")
        helper = str(Path(__file__).with_name("git_helper.py"))
        wrapper = (f"#!{sys.executable}\nimport os,sys\n"
                   f"os.execv(sys.executable,[sys.executable,{helper!r},{self.config_path!r},*sys.argv[1:]])\n").encode()
        _write_private(self.git_wrapper, wrapper, 0o700)

    def _snapshot_known_hosts(self, source):
        if not source or not os.path.isabs(source):
            raise TransportError("trusted known_hosts path is required")
        try:
            fd = os.open(source, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
            try:
                st = os.fstat(fd)
                if not stat.S_ISREG(st.st_mode) or st.st_size > 4 * 1024 * 1024:
                    raise TransportError("trusted known_hosts must be a bounded regular file")
                data = bytearray()
                while len(data) <= 4 * 1024 * 1024:
                    chunk = os.read(fd, min(65536, 4 * 1024 * 1024 + 1 - len(data)))
                    if not chunk:
                        break
                    data.extend(chunk)
            finally:
                os.close(fd)
        except OSError as exc:
            raise TransportError("trusted known_hosts could not be read safely") from exc
        if not data or len(data) > 4 * 1024 * 1024:
            raise TransportError("trusted known_hosts is empty or too large")
        path = os.path.join(self.session_dir, "known_hosts")
        _write_private(path, bytes(data))
        return path

    def _config(self):
        return {"version": 1, "ssh_path": self.ssh_path, "git_path": self.git_path,
                "key_path": self.key_path, "known_hosts": self.known_hosts,
                "proof_path": self.proof_path, "config_path": self.config_path,
                "git_wrapper": self.git_wrapper, "repository": self.bound_repository,
                "git_dir": self.git_dir,
                "host": self.settings.ssh_host, "username": self.settings.username,
                "port": self.settings.ssh_port, "project": self.settings.project,
                "destination": self.expected_url, "remote_name": self.remote_name}

    def _save_config(self):
        payload = json.dumps(self._config(), separators=(",", ":")).encode()
        if len(payload) > _MAX_CONFIG:
            raise TransportError("transport configuration exceeds size limit")
        temp = self.config_path + ".new"
        _write_private(temp, payload)
        os.replace(temp, self.config_path)

    def bind_remote(self, repository, name):
        repo = os.path.realpath(repository)
        if not re.fullmatch(r"[A-Za-z0-9._-]+", name) or not os.path.isdir(repo):
            raise TransportError("invalid repository or remote name")
        if repo != os.path.realpath(self.settings.repository):
            raise TransportError("transport can only bind the configured repository")
        jj = shutil.which("jj")
        if not jj:
            raise TransportError("Jujutsu is required to resolve the bound Git store")
        try:
            result = subprocess.run([os.path.abspath(jj), "--no-pager", "--ignore-working-copy", "git", "root"],
                                    cwd=repo, env={"PATH": os.environ.get("PATH", "/usr/bin:/bin"),
                                                   "HOME": os.environ.get("HOME", "")},
                                    stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                    stderr=subprocess.DEVNULL, timeout=10, check=False)
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise TransportError("could not resolve the repository Git store") from exc
        if result.returncode or len(result.stdout) > 4096:
            raise TransportError("could not resolve the repository Git store")
        try:
            raw_git_dir = result.stdout.decode("utf-8", "strict").strip()
        except UnicodeDecodeError as exc:
            raise TransportError("Jujutsu returned an invalid Git store") from exc
        if not os.path.isabs(raw_git_dir):
            raise TransportError("Jujutsu returned an invalid Git store")
        git_dir = os.path.realpath(raw_git_dir)
        if not os.path.isdir(git_dir):
            raise TransportError("Jujutsu returned an invalid Git store")
        self.bound_repository = repo
        self.git_dir = git_dir
        self.remote_name = name
        self._save_config()
        return self.expected_url

    def environment(self, base_env):
        env = dict(base_env)
        for key in tuple(env):
            if key == "GIT_CONFIG" or key.startswith("GIT_CONFIG_") or key in {
                "GIT_SSH", "GIT_SSH_COMMAND", "GIT_SSH_VARIANT", "SSH_AUTH_SOCK",
                "GIT_ASKPASS", "SSH_ASKPASS", "GIT_PROXY_COMMAND",
                self.settings.token_env, self.settings.ssh_key_env,
            }:
                env.pop(key, None)
        from shlex import join
        command = [sys.executable, str(Path(__file__).with_name("ssh_helper.py")), "--config", self.config_path]
        env["GIT_SSH_COMMAND"] = join(command)
        env["GIT_SSH_VARIANT"] = "ssh"
        env["GIT_CONFIG_NOSYSTEM"] = "1"
        env["GIT_CONFIG_GLOBAL"] = os.devnull
        return env

    def ssh_argv(self, arguments):
        return [sys.executable, str(Path(__file__).with_name("ssh_helper.py")), "--config", self.config_path, *arguments]

    def verify(self):
        host = self.settings.ssh_host
        if ":" in host and not host.startswith("["):
            host = f"[{host}]"
        argv = self.ssh_argv(["-p", str(self.settings.ssh_port), f"{self.settings.username}@{host}", "gerrit version"])
        safe_base = {key: os.environ[key] for key in ("PATH", "HOME", "LANG", "LC_ALL") if key in os.environ}
        try:
            proc = subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                    env=self.environment(safe_base), start_new_session=True, close_fds=True)
        except OSError as exc:
            raise TransportError("pinned Gerrit SSH preflight could not start") from exc
        output = bytearray()
        fd = proc.stdout.fileno()
        deadline = time.monotonic() + 15
        try:
            while True:
                remain = deadline - time.monotonic()
                if remain <= 0 or not __import__("select").select([fd], [], [], remain)[0]:
                    raise TransportError("pinned Gerrit SSH preflight timed out")
                part = os.read(fd, 4096)
                if not part:
                    break
                output.extend(part)
                if len(output) > _MAX_OUTPUT:
                    raise TransportError("pinned Gerrit SSH preflight output exceeds limit")
            if proc.wait(timeout=max(0.1, deadline - time.monotonic())) != 0:
                raise TransportError("pinned Gerrit SSH preflight failed")
            if not re.search(rb"(?i)gerrit version\s+\d", output):
                raise TransportError("SSH endpoint did not confirm Gerrit version")
            if not self.confirmed("verify"):
                raise TransportError("SSH preflight result was not recorded")
            return output.decode("utf-8", "replace").strip()
        except subprocess.TimeoutExpired as exc:
            raise TransportError("pinned Gerrit SSH preflight timed out") from exc
        finally:
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            try:
                proc.wait(timeout=2)
            except subprocess.TimeoutExpired:
                proc.kill(); proc.wait()
            proc.stdout.close()

    def confirmed(self, operation):
        if operation not in {"verify", "fetch", "push"}:
            return False
        try:
            fd = os.open(self.proof_path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
            st = os.fstat(fd)
            if (not stat.S_ISREG(st.st_mode) or st.st_uid != os.getuid() or
                    stat.S_IMODE(st.st_mode) & 0o077 or st.st_size > 1024 * 1024):
                os.close(fd)
                return False
            with os.fdopen(fd, "rb") as f:
                data = f.read(1024 * 1024 + 1)
            if len(data) > 1024 * 1024:
                return False
            for line in data.splitlines():
                if not line:
                    continue
                row = json.loads(line)
                if (isinstance(row, dict) and row.get("operation") == operation and
                        row.get("destination") == self.expected_url and type(row.get("status")) is int and
                        row.get("status") == 0):
                    return True
            return False
        except (OSError, ValueError, json.JSONDecodeError):
            return False
