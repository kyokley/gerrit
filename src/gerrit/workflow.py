"""Fail-closed per-task orchestration primitives.

Network and credential integrations are resolved lazily so importing the CLI or
using the supervisor with test adapters does not require optional providers.
"""

from __future__ import annotations

import json
import os
import signal
import stat
import subprocess
import tempfile
import threading
import time
from pathlib import Path
from typing import Any, Callable

from .jj import Jujutsu


class WorkflowError(RuntimeError):
    pass


class UnknownWriteOutcome(WorkflowError):
    pass


class Workflow:
    def __init__(self, settings: Any, state_dir: str | os.PathLike[str], *, jj_lock: threading.Lock,
                 publish: Callable[[dict[str, Any]], Any] | None = None, lease: Any = None) -> None:
        self.settings = settings if isinstance(settings, dict) else settings.to_dict()
        self.state_dir = Path(state_dir)
        self.jj_lock = jj_lock
        self.publish = publish or (lambda _patch: None)
        self.lease = lease

    def run(self, task: dict[str, Any], emit: Callable[[str, str], None], cancel: threading.Event,
            *, lease: Any = None) -> dict[str, Any]:
        """Perform review workflow with the concrete in-tree live adapter."""
        from .live import LiveAdapter
        return LiveAdapter(self).run(task, emit, cancel, lease=lease or self.lease)

    def run_agent(
        self,
        workspace: str | os.PathLike[str],
        prompt: str,
        environment: dict[str, str],
        timeout: float,
        emit: Callable[[str, str], None],
        cancel: threading.Event,
    ) -> dict[str, Any]:
        """Run one bounded agent invocation and wait for its process group to exit."""
        workspace = Path(workspace).resolve()
        self.state_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
        result_dir = Path(tempfile.mkdtemp(prefix="result-", dir=self.state_dir))
        result_path = result_dir / "result.json"
        configured = []
        is_default = False
        process = None
        reader = None
        try:
            default_command = ["opencode", "run", "--standalone", "--format", "json", "{prompt}"]
            prior_default = ["opencode", "run", "--format", "json", "{prompt}"]
            configured = list(self.settings.get("agent_command", default_command))
            is_default = configured in (default_command, prior_default)
            if configured == prior_default:
                configured = default_command
            if not configured or any(not isinstance(part, str) or "\x00" in part for part in configured):
                raise WorkflowError("agent_command is invalid")
            if is_default:
                help_result = self._bounded_command(["opencode", "run", "--help"], cwd=workspace,
                                                    env=dict(environment), timeout=30)
                help_text = help_result["stdout"] + help_result["stderr"]
                if (help_result["returncode"] or "--standalone" not in help_text or
                        "--format" not in help_text or "json" not in help_text):
                    raise WorkflowError("installed opencode run help does not confirm standalone JSON format capability")
            if sum(part == "{prompt}" for part in configured) != 1:
                raise WorkflowError("agent_command must contain one exact {prompt} argument")
            command = [prompt if part == "{prompt}" else part for part in configured]
            env = dict(environment)
            env["GERRIT_RESULT_FILE"] = str(result_path)
            emit("agent", "agent invocation started")
            process = subprocess.Popen(
                command, cwd=workspace, env=env, stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                encoding="utf-8", errors="replace", start_new_session=True,
            )
        except OSError as exc:
            try:
                result_path.unlink(missing_ok=True)
                result_dir.rmdir()
            except OSError:
                pass
            raise WorkflowError("agent process could not start") from exc
        except Exception:
            try:
                result_path.unlink(missing_ok=True)
                result_dir.rmdir()
            except OSError:
                pass
            raise

        def drain() -> None:
            assert process.stdout is not None
            def publish(text: str) -> None:
                text = text[:2_000]
                for key, value in environment.items():
                    if any(marker in key.upper() for marker in ("TOKEN", "SECRET", "PRIVATE", "PASSWORD", "SSH_KEY")) and value:
                        text = text.replace(value, "[redacted]")
                emit("agent", text)

            while True:
                chunk = process.stdout.readline(2_048)
                if not chunk:
                    break
                publish(chunk.rstrip("\r\n") + (" [truncated]" if len(chunk) >= 2_048 and not chunk.endswith("\n") else ""))

        reader = threading.Thread(target=drain, name="gerrit-agent-output", daemon=True)
        reader.start()
        started = time.monotonic()
        try:
            while process.poll() is None:
                if cancel.is_set() or time.monotonic() - started > timeout:
                    self._stop_group(process)
                    if cancel.is_set():
                        raise WorkflowError("agent invocation cancelled")
                    raise WorkflowError("agent invocation timed out")
                time.sleep(0.05)
            reader.join(timeout=1)
            if reader.is_alive() or not self._group_quiescent(process.pid):
                self._stop_group(process)
                reader.join(timeout=5)
                raise WorkflowError("agent process group did not become quiescent")
            if process.returncode != 0:
                raise WorkflowError(f"agent exited with status {process.returncode}")
            file_stat = os.lstat(result_path)
            if (not stat.S_ISREG(file_stat.st_mode) or file_stat.st_uid != os.getuid() or
                    file_stat.st_nlink != 1 or file_stat.st_size > 64 * 1024):
                raise WorkflowError("agent result file is missing or too large")
            result_fd = os.open(result_path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0))
            try:
                opened = os.fstat(result_fd)
                if (not stat.S_ISREG(opened.st_mode) or opened.st_uid != os.getuid() or
                        opened.st_nlink != 1 or opened.st_size > 64 * 1024):
                    raise WorkflowError("agent result file is unsafe")
                chunks = bytearray()
                while len(chunks) <= 64 * 1024:
                    part = os.read(result_fd, min(8192, 64 * 1024 + 1 - len(chunks)))
                    if not part:
                        break
                    chunks.extend(part)
                raw = bytes(chunks)
            finally:
                os.close(result_fd)
            if len(raw) > 64 * 1024:
                raise WorkflowError("agent result file is too large")
            result = json.loads(raw)
            if not isinstance(result, dict):
                raise WorkflowError("agent result must be a JSON object")
            done = result.get("done", False)
            if type(done) is not bool:
                raise WorkflowError("agent result done must be a boolean")
            message = result.get("message")
            tests = result.get("tests")
            if message is None and done:
                message = ""
            if not isinstance(message, str) or (not done and not message.strip()) or len(message) > 500:
                raise WorkflowError("agent result message is missing or invalid")
            if tests is None and done:
                tests = []
            if not isinstance(tests, list) or len(tests) > 50:
                raise WorkflowError("agent tests must be a bounded argv list")
            clean_tests: list[list[str]] = []
            for argv in tests:
                if (not isinstance(argv, list) or not argv or not isinstance(argv[0], str) or not argv[0] or
                        len(argv) > 100 or not all(isinstance(arg, str) and len(arg) <= 4_000 and "\x00" not in arg for arg in argv)):
                    raise WorkflowError("test commands must be nonempty argv arrays")
                clean_tests.append(argv)
            replies = result.get("replies", [])
            if not isinstance(replies, list) or len(replies) > 20:
                raise WorkflowError("agent replies must be a bounded list")
            clean_replies = []
            for reply in replies:
                if (not isinstance(reply, dict) or not isinstance(reply.get("change_id"), str) or
                        not isinstance(reply.get("message"), str) or len(reply["message"]) > 2_000):
                    raise WorkflowError("agent reply entry is invalid")
                feedback_id = reply.get("feedback_id")
                if feedback_id is not None and not isinstance(feedback_id, str):
                    raise WorkflowError("agent feedback_id must be text")
                clean_replies.append({"change_id": reply["change_id"], "feedback_id": feedback_id,
                                      "message": reply["message"]})
            return {"done": done, "message": message.strip(), "tests": clean_tests, "replies": clean_replies}
        finally:
            if process is not None and (process.poll() is None or not self._group_quiescent(process.pid)):
                self._stop_group(process)
            if reader is not None:
                reader.join(timeout=5)
            if process is not None and process.stdout is not None:
                process.stdout.close()
            try:
                result_path.unlink(missing_ok=True)
                result_dir.rmdir()
            except OSError:
                pass

    @staticmethod
    def _bounded_command(argv: list[str], *, cwd: Path, env: dict[str, str], timeout: float,
                         limit: int = 1024 * 1024, cancel: threading.Event | None = None) -> dict[str, Any]:
        try:
            process = subprocess.Popen(argv, cwd=cwd, env=env, stdin=subprocess.DEVNULL,
                                       stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True)
        except OSError as exc:
            raise WorkflowError("command could not start") from exc
        output = [bytearray(), bytearray()]
        exceeded = threading.Event()

        def collect(pipe, buffer):
            seen = 0
            while True:
                chunk = os.read(pipe.fileno(), 8192)
                if not chunk:
                    break
                seen += len(chunk)
                if seen <= limit:
                    buffer.extend(chunk[:limit - len(buffer)])
                else:
                    exceeded.set()
                    try:
                        os.killpg(process.pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass

        assert process.stdout is not None and process.stderr is not None
        readers = [threading.Thread(target=collect, args=(process.stdout, output[0]), daemon=True),
                   threading.Thread(target=collect, args=(process.stderr, output[1]), daemon=True)]
        for reader in readers:
            reader.start()
        deadline = time.monotonic() + timeout
        try:
            while process.poll() is None:
                if cancel is not None and cancel.is_set():
                    Workflow._stop_group(process)
                    raise WorkflowError("command cancelled")
                if time.monotonic() >= deadline:
                    Workflow._stop_group(process)
                    raise WorkflowError("command timed out")
                time.sleep(0.05)
            returncode = process.returncode
        except WorkflowError:
            for reader in readers:
                reader.join(timeout=5)
            process.stdout.close(); process.stderr.close()
            raise
        if not Workflow._group_quiescent(process.pid):
            Workflow._stop_group(process)
        for reader in readers:
            reader.join(timeout=5)
        process.stdout.close()
        process.stderr.close()
        if exceeded.is_set() or any(reader.is_alive() for reader in readers) or not Workflow._group_quiescent(process.pid):
            raise WorkflowError("command output exceeded bounds or process group survived")
        return {"returncode": returncode,
                "stdout": output[0].decode("utf-8", "replace"),
                "stderr": output[1].decode("utf-8", "replace")}

    @staticmethod
    def _stop_group(process: subprocess.Popen[str]) -> None:
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except ProcessLookupError:
            try:
                process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                pass
            return
        try:
            process.wait(timeout=2)
        except subprocess.TimeoutExpired:
            pass
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline and not Workflow._group_quiescent(process.pid):
            time.sleep(0.05)
        if not Workflow._group_quiescent(process.pid):
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            pass

    @staticmethod
    def _group_quiescent(pid: int) -> bool:
        try:
            os.killpg(pid, 0)
        except ProcessLookupError:
            return True
        except PermissionError:
            return False
        return False

    @staticmethod
    def run_tests(workspace: str | os.PathLike[str], commands: list[list[str]], *, env: dict[str, str],
                  cancel: threading.Event | None = None, timeout: float = 900) -> list[dict[str, Any]]:
        results = []
        for argv in commands:
            if Path(argv[0]).name.lower() in {"sh", "bash", "dash", "zsh", "fish", "pwsh", "powershell", "cmd", "cmd.exe"}:
                raise WorkflowError("test commands must not invoke a shell")
            result = Workflow._bounded_command(argv, cwd=Path(workspace), env=dict(env), timeout=timeout,
                                               limit=2 * 1024 * 1024, cancel=cancel)
            results.append({"argv": argv, "returncode": result["returncode"]})
            if result["returncode"]:
                raise WorkflowError(f"test command failed: {Path(argv[0]).name} ({result['returncode']})")
        return results

    def prepare_workspace(self, task_id: str, base_revision: str) -> Path:
        repo = Path(self.settings["repository"]).resolve()
        workspace = self.state_dir / "workspaces" / task_id
        with self.jj_lock:
            jj = Jujutsu(repo)
            capability = jj.capability()
            if not capability["workspace"] or not capability["at_operation"]:
                raise WorkflowError("installed jj lacks required isolated workspace options")
            return jj.add_workspace(workspace, f"agent-{task_id[:12]}", base_revision)
