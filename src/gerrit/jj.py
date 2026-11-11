"""Small, argv-only Jujutsu adapter used by the runtime workflow."""

from __future__ import annotations

import os
import re
import signal
import subprocess
import threading
import time
from pathlib import Path
from typing import Sequence


class JujutsuError(RuntimeError):
    pass


class Jujutsu:
    def __init__(self, repository: str | os.PathLike[str], *, executable: str = "jj",
                 git_executable_path: str | os.PathLike[str] | None = None) -> None:
        self.repository = Path(repository).resolve()
        self.executable = executable
        self.git_executable_path = str(Path(git_executable_path).resolve()) if git_executable_path else None

    def run(
        self,
        *args: str,
        cwd: str | os.PathLike[str] | None = None,
        env: dict[str, str] | None = None,
        timeout: float = 60,
        ignore_working_copy: bool = False,
        output_limit: int = 2 * 1024 * 1024,
        explicit_repository: bool = False,
    ) -> subprocess.CompletedProcess[str]:
        command = [self.executable, "--no-pager", "--color=never"]
        if explicit_repository:
            command += ["--repository", str(self.repository)]
        if self.git_executable_path:
            command += ["--config", f"git.executable-path={self.git_executable_path}"]
        if ignore_working_copy:
            command.append("--ignore-working-copy")
        command.extend(map(str, args))
        try:
            proc = subprocess.Popen(command, cwd=Path(cwd).resolve() if cwd is not None else self.repository,
                                    env=env, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                    stderr=subprocess.PIPE, start_new_session=True)
            captured = [bytearray(), bytearray()]
            overflow = threading.Event()

            def drain(pipe, data):
                size = 0
                while True:
                    chunk = os.read(pipe.fileno(), 8192)
                    if not chunk:
                        break
                    size += len(chunk)
                    if size <= output_limit:
                        data.extend(chunk[:output_limit - len(data)])
                    else:
                        overflow.set()
                        try:
                            os.killpg(proc.pid, signal.SIGKILL)
                        except ProcessLookupError:
                            pass

            assert proc.stdout is not None and proc.stderr is not None
            readers = [threading.Thread(target=drain, args=(proc.stdout, captured[0]), daemon=True),
                       threading.Thread(target=drain, args=(proc.stderr, captured[1]), daemon=True)]
            for reader in readers:
                reader.start()
            try:
                returncode = proc.wait(timeout=timeout)
            except subprocess.TimeoutExpired as exc:
                self._stop_group(proc)
                for reader in readers:
                    reader.join(timeout=5)
                proc.stdout.close(); proc.stderr.close()
                raise JujutsuError(f"jj command timed out: {args[0] if args else 'version'}") from exc
            group_alive = not self._group_quiescent(proc.pid)
            if group_alive:
                self._stop_group(proc)
            for reader in readers:
                reader.join(timeout=5)
            proc.stdout.close(); proc.stderr.close()
            if overflow.is_set() or any(reader.is_alive() for reader in readers) or group_alive:
                try:
                    os.killpg(proc.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                raise JujutsuError("jj command output exceeds size limit or has surviving descendants")
            result = subprocess.CompletedProcess(command, returncode,
                                                 captured[0].decode("utf-8", "replace"),
                                                 captured[1].decode("utf-8", "replace"))
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise JujutsuError(f"jj command could not complete: {args[0] if args else 'version'}") from exc
        if result.returncode:
            raise JujutsuError(f"jj command failed ({result.returncode}): {args[0] if args else 'version'}")
        return result

    @staticmethod
    def _group_quiescent(pid: int) -> bool:
        try:
            os.killpg(pid, 0)
        except ProcessLookupError:
            return True
        except PermissionError:
            return False
        return False

    @classmethod
    def _stop_group(cls, process: subprocess.Popen[str]) -> None:
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        try:
            process.wait(timeout=2)
        except subprocess.TimeoutExpired:
            pass
        deadline = time.monotonic() + 1
        while time.monotonic() < deadline and not cls._group_quiescent(process.pid):
            time.sleep(0.05)
        if not cls._group_quiescent(process.pid):
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            pass

    def capability(self, env: dict[str, str] | None = None) -> dict[str, object]:
        version = self.run("--version", env=env).stdout.strip()
        upload_help = self.run("gerrit", "upload", "--help", env=env).stdout
        workspace_help = self.run("workspace", "add", "--help", env=env).stdout
        required_upload = ("--revision", "--remote-branch", "--remote", "--dry-run")
        required_workspace = ("--revision", "--name", "--no-colocate")
        fetch_help = self.run("git", "fetch", "--help", env=env).stdout
        remote_help = self.run("git", "remote", "add", "--help", env=env).stdout
        global_help = self.run("--help", env=env).stdout
        squash_help = self.run("squash", "--help", env=env).stdout
        update_stale_help = self.run("workspace", "update-stale", "--help", env=env).stdout
        return {
            "version": version,
            "upload": all(option in upload_help for option in required_upload),
            "workspace": all(option in workspace_help for option in required_workspace),
            "fetch": "--remote" in fetch_help and "--branch" in fetch_help,
            "remote": "Usage:" in remote_help,
            "at_operation": "--at-operation" in global_help,
            "ignore_working_copy": "--ignore-working-copy" in global_help,
            "config": "--config" in global_help,
            "squash": all(flag in squash_help for flag in ("--from", "--into", "--use-destination-message")),
            "update_stale": "Usage: jj workspace update-stale" in update_stale_help,
        }

    def add_workspace(self, destination: str | os.PathLike[str], name: str, revision: str,
                      env: dict[str, str] | None = None) -> Path:
        path = Path(destination).resolve()
        if path.exists() and any(path.iterdir()):
            raise JujutsuError("workspace destination is not empty")
        path.parent.mkdir(parents=True, exist_ok=True)
        operation = self.run("op", "log", "-n", "1", "-T", "id", env=env,
                             ignore_working_copy=True).stdout.split()[-1]
        if not operation or any(ch not in "0123456789abcdef" for ch in operation.lower()):
            raise JujutsuError("current jj operation ID could not be resolved safely")
        self.run("--at-operation", operation, "workspace", "add", "--revision", revision,
                 "--name", name, "--no-colocate", str(path), env=env, cwd=path.parent,
                 explicit_repository=True)
        return path

    def workspace_status(self, workspace: str | os.PathLike[str]) -> str:
        return self.run("status", cwd=workspace).stdout

    def describe(self, workspace: str | os.PathLike[str], revision: str = "@") -> str:
        return self.run("log", "-r", revision, "-T", "description", "--no-graph", cwd=workspace).stdout

    def change_id(self, workspace: str | os.PathLike[str], revision: str = "@") -> str:
        return self.run("log", "-r", revision, "-T", "change_id", "--no-graph", cwd=workspace).stdout.strip()

    def commit_id(self, workspace: str | os.PathLike[str], revision: str = "@") -> str:
        return self.run("log", "-r", revision, "-T", "commit_id", "--no-graph", cwd=workspace).stdout.strip()

    def mutable_ancestors(self, workspace: str | os.PathLike[str], revision: str = "@",
                          env: dict[str, str] | None = None) -> list[str]:
        # Resolve the full selected mutable stack explicitly before upload.
        output = self.run("log", "-r", f"mutable()::{revision}", "-T", 'commit_id ++ "\\n"',
                          "--no-graph", cwd=workspace, env=env).stdout
        return [line for line in output.splitlines() if line]

    def upload(self, workspace: str | os.PathLike[str], revision: str, branch: str, remote: str, *, dry_run: bool, env: dict[str, str] | None = None) -> str:
        args: Sequence[str] = ("gerrit", "upload", "--revision", revision, "--remote-branch", branch, "--remote", remote)
        if dry_run:
            args += ("--dry-run",)
        result = self.run(*args, cwd=workspace, env=env, timeout=300)
        return result.stdout + result.stderr

    def resolve_commit_prefix(self, workspace: str | os.PathLike[str], prefix: str,
                              env: dict[str, str] | None = None) -> str:
        if not re.fullmatch(r"[0-9a-fA-F]{4,64}", prefix):
            raise JujutsuError("jj preview contains an invalid immutable commit prefix")
        output = self.run("log", "-r", "all()", "-T", 'commit_id ++ "\\n"', "--no-graph", cwd=workspace,
                          env=env, ignore_working_copy=True, output_limit=16 * 1024 * 1024).stdout
        matches = {line.strip().lower() for line in output.splitlines()
                   if line.strip().lower().startswith(prefix.lower())}
        if len(matches) != 1:
            raise JujutsuError("jj preview immutable commit prefix is missing or ambiguous")
        return matches.pop()

    def verify_preview(self, workspace: str | os.PathLike[str], output: str, commits: list[str],
                       branch: str, remote: str, env: dict[str, str] | None = None) -> list[str]:
        header = re.compile(
            r"^Found ([1-9][0-9]*) heads to push to Gerrit \(remote '([^']+)'\), target branch '([^']+)'\.$"
        )
        heads = re.compile(r"^Dry-run: Would push ([^\s]+) ([0-9a-fA-F]{4,64}) .+$")
        lines = output.splitlines()
        headers = [header.fullmatch(line.strip()) for line in lines]
        headers = [match for match in headers if match]
        parsed_heads = [heads.fullmatch(line.strip()) for line in lines]
        parsed_heads = [match for match in parsed_heads if match]
        if (len(headers) != 1 or headers[0].group(2) != remote or headers[0].group(3) != branch or
                int(headers[0].group(1)) != len(parsed_heads)):
            raise JujutsuError("jj dry-run did not confirm the exact Gerrit remote and branch header")
        resolved = [self.resolve_commit_prefix(workspace, match.group(2), env) for match in parsed_heads]
        expected_heads = [commits[-1]] if commits else []
        if set(resolved) != set(expected_heads) or len(resolved) != len(expected_heads):
            raise JujutsuError("jj dry-run heads do not match the owned immutable stack tip")
        return resolved

    def verify_transport_config(self, workspace: str | os.PathLike[str], wrapper: str,
                                env: dict[str, str]) -> bool:
        if not self.git_executable_path or Path(wrapper).resolve() != Path(self.git_executable_path):
            return False
        try:
            value = self.run("config", "get", "git.executable-path", cwd=workspace, env=env,
                             ignore_working_copy=True).stdout.strip()
        except JujutsuError:
            return False
        return Path(value).resolve() == Path(wrapper).resolve()

    def add_remote(self, workspace: str | os.PathLike[str], name: str, url: str,
                   env: dict[str, str]) -> None:
        self.run("git", "remote", "add", name, url, cwd=workspace, env=env, ignore_working_copy=True)

    def remote_url(self, workspace: str | os.PathLike[str], name: str, env: dict[str, str]) -> str:
        output = self.run("git", "remote", "list", cwd=workspace, env=env,
                          ignore_working_copy=True).stdout
        matches = [line.split(None, 1)[1] for line in output.splitlines()
                   if len(line.split(None, 1)) == 2 and line.split(None, 1)[0] == name]
        if len(matches) != 1:
            raise JujutsuError("configured Gerrit remote is missing or ambiguous")
        return matches[0]

    def fetch_branch(self, workspace: str | os.PathLike[str], remote: str, branch: str,
                     env: dict[str, str]) -> None:
        self.run("git", "fetch", "--remote", remote, "--branch", branch,
                 cwd=workspace, env=env, timeout=300, ignore_working_copy=True)

    def remote_commit_id(self, workspace: str | os.PathLike[str], remote: str,
                         branch: str, env: dict[str, str]) -> str:
        return self.run("log", "-r", f"{branch}@{remote}", "-T", "commit_id", "--no-graph",
                        cwd=workspace, env=env, ignore_working_copy=True).stdout.strip()

    def commit(self, workspace: str | os.PathLike[str], message: str, env: dict[str, str]) -> str:
        self.run("describe", "-m", message, cwd=workspace, env=env)
        self.run("new", cwd=workspace, env=env)
        return self.commit_id(workspace, "@-")

    def diff(self, workspace: str | os.PathLike[str], revision: str, env: dict[str, str]) -> str:
        return self.run("diff", "--git", "-r", revision, cwd=workspace, env=env,
                        output_limit=16 * 1024 * 1024).stdout

    def parents(self, workspace: str | os.PathLike[str], revision: str, env: dict[str, str]) -> list[str]:
        output = self.run("log", "-r", revision, "-T", 'parents.map(|p| p.commit_id()).join("\\n")',
                          "--no-graph", cwd=workspace, env=env).stdout
        return [line for line in output.splitlines() if line]

    def mutable_map(self, workspace: str | os.PathLike[str], env: dict[str, str]) -> dict[str, str]:
        output = self.run("log", "-r", "mutable()", "-T", 'change_id ++ " " ++ commit_id ++ "\\n"',
                          "--no-graph", cwd=workspace, env=env, output_limit=8 * 1024 * 1024).stdout
        result = {}
        for line in output.splitlines():
            parts = line.split()
            if len(parts) == 2:
                result[parts[0]] = parts[1]
        return result

    def is_ancestor(self, workspace: str | os.PathLike[str], ancestor: str, revision: str,
                    env: dict[str, str]) -> bool:
        output = self.run("log", "-r", f"{ancestor}::{revision}", "-T", "commit_id", "--no-graph",
                          cwd=workspace, env=env, ignore_working_copy=True).stdout.strip()
        return bool(output)

    def squash_into(self, workspace: str | os.PathLike[str], source: str, target: str,
                    env: dict[str, str]) -> None:
        self.run("squash", "--from", source, "--into", target, "--use-destination-message",
                 cwd=workspace, env=env)

    def update_stale(self, workspace: str | os.PathLike[str], env: dict[str, str]) -> None:
        help_text = self.run("workspace", "update-stale", "--help", cwd=workspace, env=env).stdout
        if "Usage: jj workspace update-stale" not in help_text:
            raise JujutsuError("installed jj does not support workspace update-stale")
        self.run("workspace", "update-stale", cwd=workspace, env=env)
