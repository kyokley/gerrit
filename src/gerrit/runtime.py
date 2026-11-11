"""Task lifecycle, bounded event storage, and restart-safe operation journal."""

from __future__ import annotations

import copy
import fcntl
import json
import os
import stat
import threading
import time
import uuid
from collections import deque
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


TERMINAL = {"completed", "failed", "cancelled", "reconciliation_required"}
RECLAIMABLE_TERMINAL = {"completed", "failed", "cancelled"}
UNRESOLVED_OPERATION_STATES = {"uploading", "unknown", "unknown_outcome", "reconciliation_required"}
EVENT_LIMIT = 200
JOURNAL_LIMIT = 2_000
JOURNAL_BYTES = 16 * 1024 * 1024
PENDING_LIMIT = 200
_JJ_LOCK = threading.Lock()


def _time() -> str:
    return datetime.now(timezone.utc).isoformat()


def _settings_snapshot(settings: Any) -> Any:
    if hasattr(settings, "to_dict"):
        return copy.deepcopy(settings.to_dict())
    if isinstance(settings, dict):
        return copy.deepcopy(settings)
    return copy.deepcopy(vars(settings))


def _reclaimable(task: dict[str, Any]) -> bool:
    if task.get("state") not in RECLAIMABLE_TERMINAL:
        return False
    changes = task.get("changes", [])
    if not isinstance(changes, list):
        return False
    return not any(isinstance(change, dict) and
                   str(change.get("status", "")).lower() in UNRESOLVED_OPERATION_STATES
                   for change in changes)


class _ExecutionLease:
    """A single coding slot, initially reserved by the dispatcher."""

    def __init__(self, supervisor: "Supervisor", *, held: bool = True) -> None:
        self._supervisor = supervisor
        self._held = held

    @property
    def held(self) -> bool:
        with self._supervisor._condition:
            return self._held

    def acquire(self, cancel: threading.Event) -> bool:
        supervisor = self._supervisor
        with supervisor._condition:
            while not self._held:
                if supervisor._closed or cancel.is_set():
                    return False
                if (supervisor._executing < supervisor._max_parallel() and
                        not supervisor._queue):
                    self._held = True
                    supervisor._executing += 1
                    supervisor._condition.notify_all()
                    return True
                supervisor._condition.wait(0.1)
            return True

    def release(self) -> None:
        supervisor = self._supervisor
        with supervisor._condition:
            if not self._held:
                return
            self._held = False
            supervisor._executing = max(0, supervisor._executing - 1)
            supervisor._condition.notify_all()


class Supervisor:
    """Owns queued task workers; all task state is isolated from caller mutation."""

    def __init__(self, settings: Any, state_dir: str | os.PathLike[str], *, workflow: Any = None) -> None:
        self._settings = settings
        self._state_dir = Path(state_dir).expanduser().resolve()
        configured = _settings_snapshot(settings)
        repository = configured.get("repository") if isinstance(configured, dict) else None
        if repository:
            repo_path = Path(repository).expanduser().resolve()
            if self._state_dir == repo_path or repo_path in self._state_dir.parents:
                raise ValueError("state_dir must be outside the configured repository")
        self._state_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
        os.chmod(self._state_dir, 0o700)
        lock_path = self._state_dir / "supervisor.lock"
        lock_fd = os.open(lock_path, os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0), 0o600)
        lock_stat = os.fstat(lock_fd)
        if not stat.S_ISREG(lock_stat.st_mode) or lock_stat.st_uid != os.getuid() or lock_stat.st_nlink != 1:
            os.close(lock_fd)
            raise RuntimeError("unsafe supervisor lock file")
        os.fchmod(lock_fd, 0o600)
        try:
            fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            os.close(lock_fd)
            raise RuntimeError("state directory is already owned by another supervisor") from exc
        self._lock_fd = lock_fd
        self._journal_path = self._state_dir / "journal.json"
        self._workflow = workflow
        self._lock = threading.RLock()
        self._condition = threading.Condition(self._lock)
        self._tasks: dict[str, dict[str, Any]] = {}
        self._cancel: dict[str, threading.Event] = {}
        self._workers: dict[str, threading.Thread] = {}
        self._queue: deque[str] = deque()
        self._leases: dict[str, _ExecutionLease] = {}
        self._executing = 0
        self._closed = False
        self._lock_released = False
        try:
            self._load_journal()
        except Exception:
            fcntl.flock(self._lock_fd, fcntl.LOCK_UN)
            os.close(self._lock_fd)
            self._lock_released = True
            raise
        self._dispatcher = threading.Thread(target=self._dispatch, name="gerrit-supervisor", daemon=True)
        self._dispatcher.start()

    def _load_journal(self) -> None:
        if not self._journal_path.exists():
            return
        try:
            fd = os.open(self._journal_path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
            try:
                file_stat = os.fstat(fd)
                if (not stat.S_ISREG(file_stat.st_mode) or file_stat.st_uid != os.getuid() or
                        file_stat.st_mode & 0o077 or file_stat.st_size > JOURNAL_BYTES):
                    raise RuntimeError("task journal is unsafe or exceeds its size limit; preserve for reconciliation")
                with os.fdopen(fd, "rb", closefd=False) as stream:
                    raw = stream.read(JOURNAL_BYTES + 1)
            finally:
                os.close(fd)
            if len(raw) > JOURNAL_BYTES:
                raise RuntimeError("task journal exceeds its size limit; preserve for reconciliation")
            data = json.loads(raw)
            if not isinstance(data, dict):
                raise RuntimeError("task journal root is invalid; preserve for reconciliation")
            tasks = data.get("tasks")
            if not isinstance(tasks, list) or len(tasks) > JOURNAL_LIMIT:
                raise RuntimeError("task journal exceeds its record limit; preserve for reconciliation")
            for task in tasks:
                if not isinstance(task, dict):
                    raise RuntimeError("task journal record is invalid; preserve for reconciliation")
                if task.get("state") not in TERMINAL:
                    changes = task.get("changes", [])
                    events = task.get("events", [])
                    uncertain = any(isinstance(change, dict) and change.get("status") == "uploading" for change in changes)
                    uncertain = uncertain or any(isinstance(event, dict) and event.get("kind") in {"branch", "reply"} and
                                                 "attempted" in event.get("text", "") for event in events)
                    task["state"] = "reconciliation_required" if uncertain else "failed"
                    task["error"] = ("interrupted during an external write; authenticated reconciliation is required; execution authority was not restored"
                                     if uncertain else "interrupted by process restart; execution authority was not restored")
                    monitor = task.setdefault("monitor", {})
                    if not isinstance(monitor, dict):
                        raise RuntimeError("task monitor journal record is invalid; preserve for reconciliation")
                    monitor["coverage"] = False
                    gaps = monitor.setdefault("gaps", [])
                    if "process restart" not in gaps:
                        gaps.append("process restart")
                    task.setdefault("events", []).append({"time": _time(), "kind": "restart", "text": "interrupted; manual reconciliation required"})
                else:
                    monitor = task.setdefault("monitor", {})
                    if not isinstance(monitor, dict):
                        raise RuntimeError("task monitor journal record is invalid; preserve for reconciliation")
                    monitor["coverage"] = False
                self._tasks[task["id"]] = task
        except (OSError, ValueError, TypeError, KeyError) as exc:
            # Preserve unreadable state for operator reconciliation; never overwrite it.
            raise RuntimeError("task journal cannot be safely loaded; preserve for reconciliation") from exc

    def _save_journal(self) -> None:
        self._state_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
        tasks = list(self._tasks.values())
        while len(tasks) > JOURNAL_LIMIT:
            removable = next((index for index, task in enumerate(tasks) if _reclaimable(task)), None)
            if removable is None:
                raise RuntimeError("task journal record limit reached with unresolved tasks")
            tasks.pop(removable)
        encoded = json.dumps({"version": 1, "tasks": tasks}, ensure_ascii=False,
                             separators=(",", ":")).encode("utf-8")
        while len(encoded) > JOURNAL_BYTES:
            removable = next((index for index, task in enumerate(tasks) if _reclaimable(task)), None)
            if removable is None:
                break
            tasks.pop(removable)
            encoded = json.dumps({"version": 1, "tasks": tasks}, ensure_ascii=False,
                                 separators=(",", ":")).encode("utf-8")
        if len(encoded) > JOURNAL_BYTES:
            raise RuntimeError("task journal exceeds size limit")
        temp = self._journal_path.with_name(f".{self._journal_path.name}.{uuid.uuid4().hex}.tmp")
        fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                stream.write(encoded.decode("utf-8"))
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temp, self._journal_path)
            dir_fd = os.open(self._state_dir, os.O_RDONLY)
            try:
                os.fsync(dir_fd)
            finally:
                os.close(dir_fd)
        finally:
            try:
                temp.unlink()
            except FileNotFoundError:
                pass

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return {"tasks": [self._public(task) for task in self._tasks.values()],
                    "max_parallel": self._max_parallel(), "executing": self._executing}

    def _max_parallel(self) -> int:
        data = _settings_snapshot(self._settings)
        try:
            return max(1, int(data.get("max_parallel", 1)))
        except (AttributeError, TypeError, ValueError):
            return 1

    def start(self, title: str) -> dict[str, Any]:
        title = str(title).strip()
        if not title or len(title) > 500 or "\x00" in title:
            raise ValueError("title must contain 1 to 500 characters")
        with self._condition:
            if self._closed:
                raise RuntimeError("supervisor is closed")
            if len(self._tasks) >= JOURNAL_LIMIT:
                for old_id in list(self._tasks):
                    if _reclaimable(self._tasks[old_id]):
                        self._tasks.pop(old_id, None)
                        self._cancel.pop(old_id, None)
                        if len(self._tasks) < JOURNAL_LIMIT:
                            break
                if len(self._tasks) >= JOURNAL_LIMIT:
                    raise RuntimeError("task history is full; wait for or reconcile active tasks")
            from .review import task_branch
            branch = task_branch(title)
            branch_owners = {"completed", "failed", "cancelled"}
            destination = _settings_snapshot(self._settings)
            destination_fields = ("http_url", "ssh_host", "ssh_port", "username", "project", "base_branch")
            destination_key = tuple(destination.get(key, "") for key in destination_fields)
            conflict = False
            for existing in self._tasks.values():
                prior_settings = existing.get("_settings", {})
                prior_key = tuple(prior_settings.get(key, "") for key in destination_fields)
                if (existing.get("state") not in branch_owners and existing.get("branch") == branch and
                        prior_key == destination_key):
                    conflict = True
                    break
            if conflict:
                raise ValueError("an active task already owns this destination branch")
            settings = _settings_snapshot(self._settings)
            task_id = uuid.uuid4().hex
            task = {
                "id": task_id, "title": title, "branch": branch, "state": "queued", "workspace": "",
                "chain_url": "", "changes": [], "events": [], "error": "", "created_at": _time(),
                "monitor": {"coverage": False, "deadline": None, "last_poll": None, "pending": [], "gaps": []},
                "_settings": settings,
            }
            self._tasks[task_id] = task
            self._cancel[task_id] = threading.Event()
            self._queue.append(task_id)
            try:
                self._save_journal()
            except Exception:
                self._tasks.pop(task_id, None)
                self._cancel.pop(task_id, None)
                self._queue.remove(task_id)
                raise
            self._condition.notify_all()
            return self._public(task)

    def _public(self, task: dict[str, Any]) -> dict[str, Any]:
        return {key: copy.deepcopy(value) for key, value in task.items() if not key.startswith("_")}

    def _dispatch(self) -> None:
        while True:
            with self._condition:
                self._condition.wait_for(lambda: self._closed or (self._queue and self._executing < self._max_parallel()))
                if self._closed and not self._queue:
                    return
                if self._closed:
                    self._queue.clear()
                    return
                task_id = self._queue.popleft()
                lease = _ExecutionLease(self)
                self._leases[task_id] = lease
                self._executing += 1
                worker = threading.Thread(target=self._run_task, args=(task_id,), name=f"gerrit-task-{task_id[:8]}", daemon=True)
                self._workers[task_id] = worker
                try:
                    worker.start()
                except Exception as exc:
                    self._workers.pop(task_id, None)
                    self._leases.pop(task_id, None)
                    lease.release()
                    task = self._tasks[task_id]
                    task["state"] = "failed"
                    task["monitor"]["coverage"] = False
                    task["monitor"]["gaps"].append("worker could not start; monitoring handoff required")
                    task["error"] = f"worker could not start: {type(exc).__name__}"
                    try:
                        self._save_journal()
                    except Exception:
                        pass
                    self._condition.notify_all()

    def _run_task(self, task_id: str) -> None:
        try:
            with self._lock:
                task = self._tasks[task_id]
                settings = copy.deepcopy(task["_settings"])
                cancel = self._cancel[task_id]
                lease = self._leases[task_id]
            workflow = self._workflow
            if workflow is None:
                from .workflow import Workflow
                workflow = Workflow(settings, self._state_dir, jj_lock=_JJ_LOCK,
                                    publish=lambda patch: self._update_task(task_id, patch), lease=lease)
                outcome = workflow.run(self._public(task), lambda kind, text: self._event(task_id, kind, text), cancel)
            else:
                outcome = workflow.run(self._public(task), lambda kind, text: self._event(task_id, kind, text),
                                       cancel, lease=lease)
            state = outcome.get("state", "completed") if isinstance(outcome, dict) else "completed"
            if state not in TERMINAL:
                state = "failed"
            with self._lock:
                task = self._tasks[task_id]
                task.update({k: copy.deepcopy(v) for k, v in (outcome or {}).items() if k in {"branch", "workspace", "chain_url", "changes", "monitor", "error"}})
                task["state"] = state
                monitor = task.setdefault("monitor", {})
                monitor["coverage"] = False
                if state != "completed":
                    gaps = task.setdefault("monitor", {}).setdefault("gaps", [])
                    gap = "cancelled or shutdown; monitoring handoff required" if state == "cancelled" else "monitoring ended before verified completion"
                    if gap not in gaps:
                        gaps.append(gap)
                self._save_journal()
        except Exception as exc:
            self._event(task_id, "error", f"workflow failed: {type(exc).__name__}")
            with self._lock:
                task = self._tasks[task_id]
                if type(exc).__name__ == "UnknownWriteOutcome":
                    task["state"] = "reconciliation_required"
                    task["error"] = "write outcome is unknown; reconcile through authenticated readback before retrying"
                elif self._cancel[task_id].is_set():
                    task["state"] = "cancelled"
                    task["monitor"]["coverage"] = False
                    task["monitor"]["gaps"].append("cancelled or shutdown before monitoring deadline")
                    task["error"] = "cancelled; monitoring coverage ended"
                else:
                    task["state"] = "failed"
                    task["monitor"]["coverage"] = False
                    task["monitor"]["gaps"].append("monitoring ended after workflow failure")
                    task["error"] = f"workflow failed: {type(exc).__name__}"
                monitor = task.setdefault("monitor", {})
                monitor["coverage"] = False
                if task["state"] == "reconciliation_required":
                    gaps = monitor.setdefault("gaps", [])
                    if "uncertain write; monitoring handoff required" not in gaps:
                        gaps.append("uncertain write; monitoring handoff required")
                self._save_journal()
        finally:
            with self._condition:
                lease = self._leases.pop(task_id, None)
                if lease is not None:
                    lease.release()
                self._workers.pop(task_id, None)
                self._condition.notify_all()

    def _event(self, task_id: str, kind: str, text: str) -> None:
        # Bound and sanitize event text; credential values are never accepted from adapters.
        safe = str(text).replace("\x00", "")[:2_000]
        with self._lock:
            task = self._tasks.get(task_id)
            if task is None:
                return
            task["events"] = (task["events"] + [{"time": _time(), "kind": str(kind)[:40], "text": safe}])[-EVENT_LIMIT:]
            self._save_journal()

    def _update_task(self, task_id: str, patch: dict[str, Any]) -> dict[str, Any]:
        allowed = {"state", "branch", "workspace", "chain_url", "changes", "monitor", "error"}
        if not isinstance(patch, dict) or set(patch) - allowed:
            raise ValueError("invalid task update")
        with self._lock:
            task = self._tasks[task_id]
            task.update(copy.deepcopy(patch))
            self._save_journal()
            return self._public(task)

    def cancel(self, task_id: str) -> dict[str, Any]:
        with self._condition:
            task = self._tasks[task_id]
            cancel_event = self._cancel.get(task_id)
            if cancel_event is None:
                return self._public(task)
            cancel_event.set()
            if task["state"] == "queued":
                try:
                    self._queue.remove(task_id)
                except ValueError:
                    pass
                task["state"] = "cancelled"
                task["monitor"]["coverage"] = False
                task["monitor"]["gaps"].append("cancelled before execution")
                task["events"].append({"time": _time(), "kind": "cancel", "text": "cancelled before execution"})
                self._save_journal()
            self._condition.notify_all()
            return self._public(task)

    def wait(self, task_id: str, timeout: float | None = None) -> dict[str, Any]:
        deadline = None if timeout is None else time.monotonic() + timeout
        while True:
            with self._condition:
                task = self._tasks[task_id]
                if task["state"] in TERMINAL and task_id not in self._workers:
                    return self._public(task)
                remaining = None if deadline is None else deadline - time.monotonic()
                if remaining is not None and remaining <= 0:
                    return self._public(task)
                self._condition.wait(remaining)

    def update_settings(self, settings: Any) -> None:
        data = _settings_snapshot(settings)
        repository = data.get("repository") if isinstance(data, dict) else None
        if repository:
            repo_path = Path(repository).expanduser().resolve()
            if self._state_dir == repo_path or repo_path in self._state_dir.parents:
                raise ValueError("state_dir must be outside the configured repository")
        with self._condition:
            self._settings = settings
            self._condition.notify_all()

    def close(self) -> None:
        save_error = None
        with self._condition:
            if self._lock_released:
                return
            self._closed = True
            for event in self._cancel.values():
                event.set()
            for task_id in tuple(self._queue):
                task = self._tasks[task_id]
                task["state"] = "cancelled"
                task["monitor"]["coverage"] = False
                task["monitor"]["gaps"].append("shutdown before execution")
                task["error"] = "cancelled; monitoring coverage ended"
                task["events"].append({"time": _time(), "kind": "shutdown", "text": "queued task cancelled; explicit handoff required"})
            self._queue.clear()
            try:
                self._save_journal()
            except Exception as exc:
                save_error = exc
            finally:
                self._condition.notify_all()
        try:
            self._dispatcher.join()
            for worker in list(self._workers.values()):
                # Return only after workers have observed cancellation and their child
                # process groups are quiescent; callers can then safely validate/exit.
                worker.join()
        finally:
            with self._condition:
                if not self._lock_released:
                    self._lock_released = True
                    try:
                        fcntl.flock(self._lock_fd, fcntl.LOCK_UN)
                    finally:
                        os.close(self._lock_fd)
        if save_error is not None:
            raise save_error
