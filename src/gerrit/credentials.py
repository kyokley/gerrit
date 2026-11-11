"""Scoped credential loading. SecretSpec values cross a private inherited pipe."""
from __future__ import annotations

import contextlib
import json
import os
import shutil
import select
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from .settings import Settings

_MAX_SECRET_BYTES = 256 * 1024
_MAX_HELP_BYTES = 64 * 1024
_RESERVED_ENV = {"PATH", "HOME", "LANG", "LC_ALL", "TMPDIR", "XDG_CONFIG_HOME", "XDG_DATA_HOME",
                 "XDG_CACHE_HOME", "XDG_RUNTIME_DIR", "PYTHONPATH", "PYTHONHOME",
                 "GERRIT_SECRET_NAMES", "GERRIT_SECRET_FD", "GERRIT_SECRET_FILES", "GERRIT_TOKEN_NAME"}


def _terminate_group(proc):
    if proc is None:
        return
    try:
        os.killpg(proc.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    try:
        proc.wait(timeout=2)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait()


def _run_child(argv, env, write_fd, read_fd, timeout, label):
    proc = None
    try:
        proc = subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                stderr=subprocess.DEVNULL, env=env, pass_fds=(write_fd,),
                                close_fds=True, start_new_session=True)
        os.close(write_fd)
        write_fd = -1
        payload = bytearray()
        deadline = time.monotonic() + timeout
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0 or not select.select([read_fd], [], [], remaining)[0]:
                raise RuntimeError(f"{label} timed out")
            chunk = os.read(read_fd, 4096)
            if not chunk:
                break
            payload.extend(chunk)
            if len(payload) > _MAX_SECRET_BYTES:
                raise RuntimeError(f"{label} response exceeds limit")
        try:
            status = proc.wait(timeout=max(0.1, deadline - time.monotonic()))
        except subprocess.TimeoutExpired:
            raise RuntimeError(f"{label} timed out") from None
        if status:
            raise RuntimeError(f"{label} failed")
        return bytes(payload)
    finally:
        _terminate_group(proc)
        if proc is not None and proc.stdout is not None:
            proc.stdout.close()
        os.close(read_fd)
        if write_fd != -1:
            os.close(write_fd)


def _help(executable, args):
    proc = None
    output = bytearray()
    try:
        proc = subprocess.Popen([executable, *args], stdin=subprocess.DEVNULL,
                                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                env={"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "HOME": os.environ.get("HOME", "")},
                                close_fds=True, start_new_session=True)
        fd = proc.stdout.fileno()
        deadline = time.monotonic() + 10
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0 or not select.select([fd], [], [], remaining)[0]:
                raise RuntimeError("SecretSpec help verification failed")
            chunk = os.read(fd, 4096)
            if not chunk:
                break
            output.extend(chunk)
            if len(output) > _MAX_HELP_BYTES:
                raise RuntimeError("SecretSpec help output exceeds limit")
        if proc.wait(timeout=max(0.1, deadline - time.monotonic())):
            raise RuntimeError("SecretSpec help verification failed")
        return output.decode("utf-8", "replace")
    finally:
        _terminate_group(proc)
        if proc is not None and proc.stdout is not None:
            proc.stdout.close()


def _secretspec_values(settings, names):
    executable = shutil.which("secretspec")
    if not executable:
        raise RuntimeError("SecretSpec is unavailable")
    try:
        global_help = _help(executable, ["--help"])
        run_help = _help(executable, ["run", "--help"])
    except Exception as exc:
        raise RuntimeError("SecretSpec help verification failed") from exc
    if not all(flag in global_help for flag in ("--file", "--reason")) or not all(flag in run_help for flag in ("--profile", "--scope")):
        raise RuntimeError("installed SecretSpec does not support required run options")
    read_fd, write_fd = os.pipe()
    os.set_inheritable(write_fd, True)
    env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "HOME": os.environ.get("HOME", ""),
           "GERRIT_SECRET_NAMES": json.dumps(names), "GERRIT_SECRET_FD": str(write_fd)}
    argv = [executable, "--file", settings.spec_file, "--reason", "Gerrit orchestrator credential access",
            "run", "--profile", settings.profile, "--scope", settings.scope, "--", sys.executable,
            str(Path(__file__).with_name("credential_helper.py"))]
    try:
        payload = _run_child(argv, env, write_fd, read_fd, 30, "SecretSpec credential resolution")
        data = json.loads(payload)
        if not isinstance(data, dict) or any(not isinstance(v, str) for v in data.values()):
            raise RuntimeError("SecretSpec returned invalid credential data")
        return data
    except Exception as exc:
        if isinstance(exc, RuntimeError):
            raise
        raise RuntimeError("SecretSpec credential resolution failed") from exc


def _file_values(settings, files):
    read_fd, write_fd = os.pipe()
    os.set_inheritable(write_fd, True)
    env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "HOME": os.environ.get("HOME", ""),
           "GERRIT_SECRET_NAMES": "[]", "GERRIT_SECRET_FILES": json.dumps(files),
           "GERRIT_SECRET_FD": str(write_fd), "GERRIT_TOKEN_NAME": settings.token_env}
    try:
        payload = _run_child([sys.executable, str(Path(__file__).with_name("credential_helper.py"))],
                             env, write_fd, read_fd, 10, "credential file resolution")
        values = json.loads(payload)
        if not isinstance(values, dict) or any(not isinstance(v, str) for v in values.values()):
            raise RuntimeError("credential file response is invalid")
        return values
    except Exception as exc:
        if isinstance(exc, RuntimeError):
            raise
        raise RuntimeError("credential file resolution failed") from exc


class CredentialContext:
    def __init__(self, settings, token, key_path, key=None, transport=None):
        self.settings, self.token, self.key_path, self._key = settings, token, key_path, key
        self.transport = transport

    def environment(self):
        env = {k: os.environ[k] for k in ("PATH", "HOME", "LANG", "LC_ALL", "TMPDIR") if k in os.environ}
        env.update({"GERRIT_HTTP_URL": self.settings.http_url, "GERRIT_SSH_HOST": self.settings.ssh_host,
                    "GERRIT_SSH_PORT": str(self.settings.ssh_port),
                    "GERRIT_PROJECT": self.settings.project, "GERRIT_BASE_BRANCH": self.settings.base_branch,
                    "GERRIT_USERNAME": self.settings.username})
        for name in ("XDG_CONFIG_HOME", "XDG_DATA_HOME", "XDG_CACHE_HOME", "XDG_RUNTIME_DIR"):
            if os.environ.get(name):
                env[name] = os.environ[name]
        return {k: v for k, v in env.items() if v}

    def ssh_environment(self):
        if self.transport is None:
            raise ValueError("pinned transport is unavailable")
        return self.transport.environment(self.environment())


@contextlib.contextmanager
def credentials(settings):
    settings.validate(live=False)
    configured_names = {settings.token_env, settings.ssh_key_env, *settings.field_env.values()}
    if configured_names & _RESERVED_ENV or any(name.startswith("GERRIT_SECRET_") for name in configured_names):
        raise ValueError("credential references use a reserved process environment name")
    if settings.repository:
        repository = os.path.realpath(settings.repository)
        for credential_path in (settings.token_file, settings.ssh_key_file):
            if credential_path and os.path.commonpath((repository, os.path.realpath(credential_path))) == repository:
                raise ValueError("credential files must be outside the repository")
    names = [settings.token_env, settings.ssh_key_env, *settings.field_env.values()]
    material = {}
    if settings.credential_mode == "secretspec":
        if not settings.spec_file or not os.path.isfile(settings.spec_file):
            raise RuntimeError("SecretSpec file is unavailable")
        material = _secretspec_values(settings, names)
    elif settings.token_file:
        paths = {settings.token_env: settings.token_file}
        if settings.ssh_key_file:
            paths[settings.ssh_key_env] = settings.ssh_key_file
        material = _file_values(settings, paths)
    token = material.get(settings.token_env, "").strip()
    key = material.get(settings.ssh_key_env)
    if settings.credential_mode == "token-file" and settings.ssh_key_file:
        key = material.get(settings.ssh_key_env)
    if not token or not key:
        raise RuntimeError("required Gerrit credential is missing")
    if not key.startswith(("-----BEGIN ", "openssh-key-v1")):
        raise RuntimeError("SSH key material is invalid")
    resolved = settings.to_dict()
    for field_name, env_name in settings.field_env.items():
        mapped = material.get(env_name)
        if not isinstance(mapped, str) or not mapped:
            raise RuntimeError("required SecretSpec Gerrit field is missing")
        try:
            resolved[field_name] = int(mapped) if field_name == "ssh_port" else mapped
        except ValueError as exc:
            raise RuntimeError("SecretSpec Gerrit field is invalid") from exc
    snapshot = Settings.from_dict(resolved)
    temp_base = "/tmp/opencode" if Path("/tmp/opencode").is_dir() else None
    temp_dir = tempfile.mkdtemp(prefix="gerrit-credential-", dir=temp_base)
    if settings.repository and os.path.commonpath((os.path.realpath(settings.repository), os.path.realpath(temp_dir))) == os.path.realpath(settings.repository):
        os.rmdir(temp_dir)
        raise RuntimeError("temporary credential directory must be outside the repository")
    os.chmod(temp_dir, 0o700)
    key_path = os.path.join(temp_dir, "id_key")
    try:
        fd = os.open(key_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(key)
        from .transport import Transport
        transport = Transport(snapshot, key_path, temp_dir)
        yield CredentialContext(snapshot, token, key_path, key, transport)
    finally:
        shutil.rmtree(temp_dir)
