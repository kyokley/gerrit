"""Foreground-owned, fail-closed Gerrit feedback monitoring."""

from __future__ import annotations

import copy
import hashlib
import json
import threading
import time
from datetime import datetime, timezone
from typing import Any, Callable

PENDING_LIMIT = 200
CHECK_LIMIT = 200
ATTEMPT_LIMIT = 2_000
HANDLED_LIMIT = 2_000
_PASS = {"success", "successful", "passed", "pass"}


class MonitorError(RuntimeError):
    """Sanitized monitor operation failure."""


def _iso(timestamp: float) -> str:
    return datetime.fromtimestamp(timestamp, timezone.utc).isoformat()


def _bounded_text(value: Any, limit: int = 500) -> str:
    if not isinstance(value, (str, int, float, bool)):
        return ""
    return str(value).replace("\x00", "")[:limit]


class ReviewMonitor:
    """Poll review feedback without claiming it was handled or authorizing work."""

    def __init__(self, review: Any, changes: list[dict[str, Any]], *, poll_interval: float,
                 deadline: float | None, publish: Callable[[dict[str, Any]], Any],
                 emit: Callable[[str, str], Any], clock: Callable[[], float] = time.time):
        if poll_interval <= 0:
            raise ValueError("poll_interval must be positive")
        if deadline is not None and not isinstance(deadline, (int, float)):
            raise ValueError("deadline must be an absolute Unix timestamp")
        self._review = review
        self._changes = copy.deepcopy(changes)
        self._poll_interval = float(poll_interval)
        self._deadline = float(deadline) if deadline is not None else None
        self._publish = publish
        self._emit = emit
        self._clock = clock
        self._lock = threading.RLock()
        self._poll_lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._started = False
        self._coverage = False
        self._last_poll: str | None = None
        self._last_success_at: float | None = None
        self._last_error: str | None = None
        self._final_sweep = False
        self._pending: dict[str, dict[str, Any]] = {}
        self._checks: dict[str, dict[str, Any]] = {}
        self._attempts: dict[str, int] = {}
        self._handled: set[str] = set()
        self._gaps: list[str] = []
        self._overflow_count = 0
        self._overflow_gap = False
        self._stopped = False

    @staticmethod
    def _key(item: dict[str, Any]) -> str:
        stable = {key: item.get(key) for key in (
            "id", "kind", "change_id", "branch", "file", "text")}
        encoded = json.dumps(stable, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
        return hashlib.sha256(encoded.encode("utf-8")).hexdigest()

    @staticmethod
    def _check_key(item: dict[str, Any]) -> str:
        identity = {key: item.get(key) for key in ("kind", "id", "change_id", "current_revision")}
        return json.dumps(identity, sort_keys=True, separators=(",", ":"))

    def _safe_feedback(self, raw: Any) -> dict[str, Any] | None:
        if not isinstance(raw, dict):
            return None
        kind = raw.get("kind")
        if kind in ("comment", "message"):
            return {key: _bounded_text(raw.get(key), limit) for key, limit in (
                ("id", 500), ("kind", 40), ("change_id", 200), ("branch", 300),
                ("current_revision", 200), ("file", 1_000), ("text", 4_000)) if key in raw}
        if kind in ("check", "checks"):
            item = {"kind": str(kind)[:40], "change_id": _bounded_text(raw.get("change_id"), 200),
                    "current_revision": _bounded_text(raw.get("current_revision"), 200),
                    "supported": raw.get("supported") is True}
            if "id" in raw:
                item["id"] = _bounded_text(raw.get("id"), 500)
            evidence = raw.get("evidence", raw.get("data"))
            item["check_summary"] = self._check_summary(evidence, item["supported"])
            return item
        return None

    @staticmethod
    def _check_summary(evidence: Any, supported: bool) -> list[dict[str, str]]:
        if not supported:
            return [{"status": "unsupported", "name": "Checks endpoint"}]
        if evidence is None:
            return [{"status": "unknown", "name": "Checks evidence unavailable"}]
        if isinstance(evidence, dict):
            if any(field in evidence for field in ("status", "state", "result")):
                values = [evidence]
            else:
                values = list(evidence.values())
        elif isinstance(evidence, list):
            values = evidence
        else:
            values = [evidence]
        result = []
        for value in values[:CHECK_LIMIT]:
            if not isinstance(value, dict):
                result.append({"status": "unknown", "name": "Unrecognized check"})
                continue
            if "conclusion" in value:
                state = value.get("conclusion")
            elif "result" in value:
                state = value.get("result")
            else:
                state = value.get("status", value.get("state", "unknown"))
            if isinstance(state, dict):
                state = state.get("conclusion", state.get("result", state.get("status", state.get("state", "unknown"))))
            name = value.get("name", value.get("checker", value.get("id", "Check")))
            result.append({"status": _bounded_text(state, 80).lower(), "name": _bounded_text(name, 200)})
        if not result:
            return []
        if len(values) > CHECK_LIMIT:
            result.append({"status": "overflow", "name": "Check evidence capacity exceeded"})
        return result

    @staticmethod
    def _blocked(check: dict[str, Any]) -> bool:
        if check.get("supported") is not True:
            return True
        summary = check.get("check_summary", [])
        return any(entry.get("status", "unknown").lower() not in _PASS for entry in summary)

    def _snapshot_locked(self) -> dict[str, Any]:
        checks = list(self._checks.values())[-CHECK_LIMIT:]
        pending = list(self._pending.values())[-PENDING_LIMIT:]
        for check in checks:
            if self._blocked(check):
                pending.append(copy.deepcopy(check))
        if self._overflow_gap:
            pending.append({"id": "pending-overflow", "kind": "overflow", "count": self._overflow_count,
                            "disposition": "reconciliation required"})
        return {
            "coverage": self._coverage,
            "deadline": self._deadline,
            "last_poll": self._last_poll,
            "pending": copy.deepcopy(pending),
            "checks": copy.deepcopy(checks),
            "gaps": list(self._gaps),
            "feedback_attempts": sum(self._attempts.values()),
            "handled_count": len(self._handled),
            "stopped": self._stopped,
        }

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return self._snapshot_locked()

    def _publish_snapshot(self) -> None:
        snapshot = self.snapshot()
        try:
            self._publish(snapshot)
        except Exception as exc:
            with self._lock:
                self._coverage = False
                self._last_error = "publication failed"
                self._add_gap_locked("monitor publication failed")
            raise MonitorError("monitor state could not be published") from exc

    def _emit_safe(self, kind: str, message: str) -> None:
        try:
            self._emit(kind, message)
        except Exception:
            return

    def _add_gap_locked(self, gap: str) -> None:
        if gap not in self._gaps:
            self._gaps.append(gap)
            if len(self._gaps) > PENDING_LIMIT:
                self._gaps = self._gaps[-PENDING_LIMIT:]
                self._overflow_gap = True

    def _record_overflow_locked(self, count: int = 1) -> None:
        self._overflow_count += count
        self._overflow_gap = True
        self._add_gap_locked("monitor pending capacity exceeded; reconciliation required")

    def _poll_once(self) -> None:
        with self._poll_lock:
            missed_interval = False
            with self._lock:
                changes = [copy.deepcopy(change) for change in self._changes
                           if not isinstance(change, dict) or "status" not in change or change.get("status") == "confirmed"]
                if self._stopped:
                    raise MonitorError("monitor is stopped")
                started_at = self._clock()
                if (self._last_success_at is not None and
                        started_at - self._last_success_at > self._poll_interval * 1.5):
                    self._coverage = False
                    self._add_gap_locked("monitor poll interval missed; coverage gap requires handoff")
                    missed_interval = True
            if missed_interval:
                self._publish_snapshot()
            try:
                raw_items = self._review.feedback(changes)
                if not isinstance(raw_items, list):
                    raise ValueError("invalid feedback result")
            except Exception as exc:
                with self._lock:
                    self._coverage = False
                    self._last_error = type(exc).__name__
                    self._add_gap_locked("authenticated feedback poll failed")
                self._publish_snapshot()
                raise MonitorError("authenticated feedback poll failed") from exc

            with self._lock:
                now = self._clock()
                for raw in raw_items:
                    item = self._safe_feedback(raw)
                    if item is None:
                        self._record_overflow_locked()
                        continue
                    if item["kind"] in ("check", "checks"):
                        for previous_key, previous in list(self._checks.items()):
                            if (previous.get("change_id") == item.get("change_id") and
                                    previous.get("current_revision") != item.get("current_revision")):
                                self._checks.pop(previous_key, None)
                        check_key = self._check_key(item)
                        if check_key not in self._checks and len(self._checks) >= CHECK_LIMIT:
                            self._record_overflow_locked()
                            continue
                        if self._checks.get(check_key) != item:
                            self._final_sweep = False
                        self._checks[check_key] = item
                        continue
                    key = self._key(item)
                    if key in self._handled:
                        continue
                    if key in self._pending:
                        existing = self._pending[key]
                        if existing.get("current_revision") != item.get("current_revision"):
                            existing["current_revision"] = item.get("current_revision")
                        continue
                    if len(self._pending) >= PENDING_LIMIT:
                        self._record_overflow_locked()
                        continue
                    self._pending[key] = item
                    self._final_sweep = False
                self._last_success_at = now
                self._last_poll = _iso(now)
                self._last_error = None
                self._coverage = not self._stopped
                if self._deadline is not None and now >= self._deadline:
                    self._final_sweep = True
            try:
                self._publish_snapshot()
            except MonitorError:
                raise
            if self.snapshot()["pending"]:
                self._emit_safe("feedback", "review feedback or checks remain unresolved")

    def start(self) -> None:
        with self._lock:
            if self._stopped:
                raise MonitorError("monitor is stopped")
            if self._started:
                return
            self._started = True
        try:
            self._poll_once()
        except MonitorError:
            with self._lock:
                self._started = False
            raise
        with self._lock:
            if self._stopped:
                return
            self._thread = threading.Thread(target=self._run, name="gerrit-review-monitor", daemon=True)
            self._thread.start()

    def _run(self) -> None:
        while not self._stop.wait(self._poll_interval):
            try:
                self._poll_once()
            except MonitorError:
                # Health remains false and the next scheduled sweep can recover.
                continue

    def set_changes(self, changes: list[dict[str, Any]]) -> None:
        if not isinstance(changes, list):
            raise ValueError("changes must be a list")
        with self._lock:
            self._changes = copy.deepcopy(changes)
            self._final_sweep = False

    def feedback(self, fresh_only: bool = False) -> list[dict[str, Any]]:
        with self._lock:
            values = []
            for key, item in self._pending.items():
                if item.get("kind") not in ("comment", "message"):
                    continue
                if fresh_only and self._attempts.get(key, 0):
                    continue
                values.append(copy.deepcopy(item))
            return values

    def claim_feedback(self, items: list[dict[str, Any]]) -> list[dict[str, Any]]:
        if not isinstance(items, list):
            raise ValueError("feedback items must be a list")
        claimed = []
        with self._lock:
            for item in items:
                if not isinstance(item, dict):
                    continue
                key = self._key(item)
                if key not in self._pending or self._pending[key].get("kind") not in ("comment", "message"):
                    continue
                if key not in self._attempts and len(self._attempts) >= ATTEMPT_LIMIT:
                    self._record_overflow_locked()
                    break
                self._attempts[key] = self._attempts.get(key, 0) + 1
                claimed.append(copy.deepcopy(self._pending[key]))
        self._publish_snapshot()
        return claimed

    def handled(self, items: list[dict[str, Any]], replied_ids: set[str] | list[str] | tuple[str, ...]) -> None:
        ids = {str(value) for value in replied_ids}
        with self._lock:
            for item in items:
                if not isinstance(item, dict) or str(item.get("id", "")) not in ids:
                    continue
                key = self._key(item)
                stored = self._pending.get(key)
                if stored is not None and stored.get("kind") in ("comment", "message"):
                    if key not in self._handled and len(self._handled) >= HANDLED_LIMIT:
                        self._record_overflow_locked()
                        continue
                    self._handled.add(key)
                    self._pending.pop(key, None)
                    self._final_sweep = False
        self._publish_snapshot()

    def check_health(self) -> dict[str, Any]:
        with self._lock:
            snapshot = self._snapshot_locked()
            if self._last_error or not snapshot["coverage"] or self._last_success_at is None:
                raise MonitorError("review monitoring is not healthy")
            return snapshot

    def ready_to_complete(self) -> bool:
        with self._lock:
            if (self._deadline is None or self._clock() < self._deadline or not self._final_sweep or
                    not self._coverage or self._last_error or self._last_success_at is None):
                return False
            if self._pending or any(self._blocked(item) for item in self._checks.values()):
                return False
            if self._gaps or self._overflow_gap:
                return False
            return self._last_success_at >= self._deadline

    def stop(self, reason: str, completed: bool = False) -> None:
        if completed and not self.ready_to_complete():
            raise MonitorError("monitor is not ready for verified completion")
        with self._lock:
            if self._stopped:
                return
            self._stopped = True
            self._coverage = False
            if not completed:
                self._add_gap_locked("monitor stopped; foreground handoff required")
            thread = self._thread
            self._stop.set()
        if thread is not None and thread is not threading.current_thread():
            thread.join()
        elif thread is None:
            with self._poll_lock:
                pass
        self._publish_snapshot()
