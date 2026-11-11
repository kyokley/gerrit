import tempfile
import time
import unittest
from pathlib import Path
import sys
import json
import threading
import os
import subprocess
from unittest.mock import patch

from gerrit.runtime import Supervisor
from gerrit.workflow import UnknownWriteOutcome, Workflow
from gerrit.monitoring import ReviewMonitor


class FakeWorkflow:
    def __init__(self):
        self.started = []

    def run(self, task, emit, cancel_event, *, lease):
        self.started.append(task["title"])
        emit("running", "started " + task["title"])
        if task["title"] != "cancel-me":
            cancel_event.wait(0.02)
            return {"state": "completed", "changes": []}
        cancel_event.wait(3)
        return {"state": "cancelled", "changes": []}


class UnknownWriteWorkflow:
    def run(self, task, emit, cancel_event, *, lease):
        raise UnknownWriteOutcome("transport failed after write")


class MonitorProgressWorkflow:
    def __init__(self):
        self.first_released = threading.Event()
        self.release_first = threading.Event()
        self.first_waiting = threading.Event()
        self.first_acquired = threading.Event()
        self.second_started = threading.Event()
        self.second_release = threading.Event()
        self.polls = 0
        self.poll_lock = threading.Lock()
        self.monitor = None

    class Review:
        def __init__(self, owner):
            self.owner = owner

        def feedback(self, changes):
            with self.owner.poll_lock:
                self.owner.polls += 1
            return []

    def run(self, task, emit, cancel_event, *, lease):
        if task["title"] == "first-monitoring":
            self.monitor = ReviewMonitor(self.Review(self), [], poll_interval=0.02, deadline=None,
                                         publish=lambda _snapshot: None, emit=lambda *_: None)
            self.monitor.start()
            self.first_released.set()
            self.release_first.wait(2)
            lease.release()
            self.first_waiting.set()
            acquired = lease.acquire(cancel_event)
            self.first_acquired.set()
            monitor = self.monitor.snapshot()
            self.monitor.stop("test complete")
            return {"state": "completed" if acquired else "cancelled", "monitor": monitor}
        self.second_started.set()
        self.second_release.wait(5)
        return {"state": "completed", "changes": []}


class RuntimeTests(unittest.TestCase):
    def test_monitoring_releases_coding_slot_and_cancel_unblocks_lease_waiter(self):
        with tempfile.TemporaryDirectory() as state:
            workflow = MonitorProgressWorkflow()
            supervisor = Supervisor({"max_parallel": 1}, state, workflow=workflow)
            try:
                first = supervisor.start("first-monitoring")
                self.assertTrue(workflow.first_released.wait(2))
                second = supervisor.start("second-coding")
                workflow.release_first.set()
                self.assertTrue(workflow.second_started.wait(2), "queued coding work must start while first task monitors")
                deadline = time.monotonic() + 2
                while workflow.polls < 2 and time.monotonic() < deadline:
                    time.sleep(0.005)
                self.assertGreaterEqual(workflow.polls, 2,
                                        f"monitor must poll while the first agent is blocked; {workflow.monitor.snapshot()} thread={workflow.monitor._thread.is_alive()}")
                self.assertEqual(supervisor.snapshot()["executing"], 1)
                supervisor.cancel(first["id"])
                self.assertTrue(workflow.first_acquired.wait(2), "cancel must unblock the released lease waiter")
                self.assertEqual(supervisor.wait(first["id"], 2)["state"], "cancelled")
                workflow.second_release.set()
                self.assertEqual(supervisor.wait(second["id"], 2)["state"], "completed")
            finally:
                workflow.second_release.set()
                supervisor.close()

    def test_journal_oversize_is_preserved_and_never_silently_discarded(self):
        import gerrit.runtime as runtime_module

        with tempfile.TemporaryDirectory() as state:
            path = Path(state) / "journal.json"
            path.write_text("{" + " " * 256)
            path.chmod(0o600)
            original = path.read_bytes()
            with patch.object(runtime_module, "JOURNAL_BYTES", 128):
                with self.assertRaisesRegex(RuntimeError, "preserve for reconciliation"):
                    Supervisor({"max_parallel": 1}, state, workflow=FakeWorkflow())
            self.assertEqual(path.read_bytes(), original)

    def test_reconciliation_records_survive_pruning_and_capacity_overflow_fails_closed(self):
        import gerrit.runtime as runtime_module

        def archived(task_id, state, changes=None):
            return {"id": task_id, "title": task_id, "branch": f"agents/{task_id}", "state": state,
                    "workspace": "", "chain_url": "", "changes": changes or [], "events": [], "error": "",
                    "created_at": "now", "monitor": {"coverage": False, "deadline": None,
                    "last_poll": None, "pending": [], "gaps": []}, "_settings": {"max_parallel": 1}}

        with tempfile.TemporaryDirectory() as state, patch.object(runtime_module, "JOURNAL_LIMIT", 2):
            supervisor = Supervisor({"max_parallel": 1}, state, workflow=FakeWorkflow())
            try:
                uncertain = archived("uncertain", "reconciliation_required",
                                     [{"change_id": "Iunknown", "status": "uploading"}])
                completed = archived("completed-old", "completed")
                with supervisor._lock:
                    supervisor._tasks.update({uncertain["id"]: uncertain, completed["id"]: completed})
                    supervisor._cancel.update({task_id: threading.Event() for task_id in supervisor._tasks})
                    supervisor._save_journal()
                started = supervisor.start("new work")
                self.assertIn("uncertain", [task["id"] for task in supervisor.snapshot()["tasks"]])
                persisted = json.loads(Path(state, "journal.json").read_text())
                self.assertIn("uncertain", [task["id"] for task in persisted["tasks"]])
                supervisor.cancel(started["id"])
            finally:
                supervisor.close()

        with tempfile.TemporaryDirectory() as state, patch.object(runtime_module, "JOURNAL_LIMIT", 2):
            supervisor = Supervisor({"max_parallel": 1}, state, workflow=FakeWorkflow())
            try:
                ids = ("unknown-a", "unknown-b")
                with supervisor._lock:
                    for task_id in ids:
                        task = archived(task_id, "reconciliation_required",
                                        [{"change_id": f"I-{task_id}", "status": "unknown"}])
                        supervisor._tasks[task_id] = task
                        supervisor._cancel[task_id] = threading.Event()
                    supervisor._save_journal()
                before = Path(state, "journal.json").read_bytes()
                with self.assertRaisesRegex(RuntimeError, "history is full"):
                    supervisor.start("must not evict uncertain")
                self.assertEqual(set(supervisor._tasks), set(ids))
                self.assertEqual(Path(state, "journal.json").read_bytes(), before)
                with supervisor._lock:
                    supervisor._tasks["unknown-c"] = archived(
                        "unknown-c", "failed", [{"change_id": "I-unknown-c", "status": "uploading"}])
                    with self.assertRaisesRegex(RuntimeError, "record limit"):
                        supervisor._save_journal()
                    supervisor._tasks.pop("unknown-c")
                self.assertEqual(Path(state, "journal.json").read_bytes(), before)
            finally:
                supervisor.close()

    def test_settings_snapshot_and_lifecycle(self):
        with tempfile.TemporaryDirectory(dir="/tmp/opencode") as state:
            workflow = FakeWorkflow()
            supervisor = Supervisor({"max_parallel": 1, "marker": "old"}, state, workflow=workflow)
            try:
                first = supervisor.start("first")
                supervisor.update_settings({"max_parallel": 1, "marker": "new"})
                second = supervisor.start("second")
                self.assertEqual(supervisor.wait(first["id"], 3)["state"], "completed")
                self.assertEqual(supervisor.wait(second["id"], 3)["state"], "completed")
                self.assertEqual(workflow.started, ["first", "second"])
                self.assertTrue(Path(state, "journal.json").exists())
            finally:
                supervisor.close()
            reopened = Supervisor({"max_parallel": 1}, state, workflow=FakeWorkflow())
            reopened.close()

    def test_settings_update_cannot_move_state_directory_inside_repository(self):
        with tempfile.TemporaryDirectory(dir="/tmp/opencode") as root:
            repo = Path(root) / "repo"; repo.mkdir()
            state = repo / "state"
            supervisor = Supervisor({}, state, workflow=FakeWorkflow())
            try:
                with self.assertRaisesRegex(ValueError, "outside"):
                    supervisor.update_settings({"repository": str(repo)})
            finally:
                supervisor.close()

    def test_restart_marks_running_task_interrupted_without_resuming(self):
        with tempfile.TemporaryDirectory(dir="/tmp/opencode") as state:
            workflow = FakeWorkflow()
            supervisor = Supervisor({"max_parallel": 1}, state, workflow=workflow)
            task = supervisor.start("cancel-me")
            deadline = time.monotonic() + 2
            while time.monotonic() < deadline and not workflow.started:
                time.sleep(0.01)
            supervisor.close()
            journal = Path(state, "journal.json")
            saved = json.loads(journal.read_text())
            saved["tasks"][0]["state"] = "monitoring"
            journal.write_text(json.dumps(saved))
            restarted = Supervisor({"max_parallel": 1}, state, workflow=FakeWorkflow())
            try:
                interrupted = restarted.snapshot()["tasks"][0]
                self.assertEqual(interrupted["id"], task["id"])
                self.assertEqual(interrupted["state"], "failed")
                self.assertIn("restart", interrupted["error"])
                self.assertFalse(interrupted["monitor"]["coverage"])
                self.assertIn("process restart", interrupted["monitor"]["gaps"])
                self.assertFalse(restarted._queue)
            finally:
                restarted.close()

    def test_custom_argv_receives_prompt_as_one_argument_and_result_is_parsed(self):
        with tempfile.TemporaryDirectory(dir="/tmp/opencode") as root:
            root_path = Path(root)
            workspace = root_path / "workspace"
            workspace.mkdir()
            state = root_path / "state"
            state.mkdir()
            source = (
                "import json,os,sys; "
                "open(os.environ['GERRIT_RESULT_FILE'],'w').write(json.dumps({'message':'core: Add behavior','tests':[['python','-m','unittest']],'done':False})); "
                "open(sys.argv[1],'w').write(json.dumps({'argv':sys.argv[2:],'inherited':bool(os.environ.get('PARENT_ONLY_SECRET'))}))"
            )
            argv_file = root_path / "argv.json"
            prompt = "fix {literal} path spaces"
            workflow = Workflow({"agent_command": [sys.executable, "-c", source, str(argv_file), "{prompt}"]}, state, jj_lock=threading.Lock())
            with patch.dict(os.environ, {"PARENT_ONLY_SECRET": "not-for-agent"}):
                result = workflow.run_agent(workspace, prompt, {}, 10, lambda *_: None, threading.Event())
            self.assertEqual(json.loads(argv_file.read_text()), {"argv": [prompt], "inherited": False})
            self.assertEqual(result["message"], "core: Add behavior")
            self.assertEqual(result["tests"], [["python", "-m", "unittest"]])

    def test_done_result_preserves_message_and_tests(self):
        with tempfile.TemporaryDirectory(dir="/tmp/opencode") as root:
            root_path = Path(root); workspace = root_path / "workspace"; workspace.mkdir()
            state = root_path / "state"; state.mkdir()
            code = "import json,os; open(os.environ['GERRIT_RESULT_FILE'],'w').write(json.dumps({'done':True,'message':'core: Finish unit','tests':[['python','-m','unittest']]}))"
            workflow = Workflow({"agent_command": [sys.executable, "-c", code, "{prompt}"]}, state, jj_lock=threading.Lock())
            result = workflow.run_agent(workspace, "prompt", {}, 10, lambda *_: None, threading.Event())
            self.assertTrue(result["done"])
            self.assertEqual(result["message"], "core: Finish unit")
            self.assertEqual(result["tests"], [["python", "-m", "unittest"]])

    def test_agent_exit_kills_surviving_process_group_descendant(self):
        with tempfile.TemporaryDirectory(dir="/tmp/opencode") as root:
            root_path = Path(root); workspace = root_path / "workspace"; workspace.mkdir()
            state = root_path / "state"; state.mkdir(); pidfile = root_path / "pid"
            child_code = "import time; time.sleep(30)"
            source = f"import subprocess,sys; p=subprocess.Popen([sys.executable,'-c',{child_code!r}]); open({str(pidfile)!r},'w').write(str(p.pid))"
            workflow = Workflow({"agent_command": [sys.executable, "-c", source, "{prompt}"]}, state, jj_lock=threading.Lock())
            with self.assertRaisesRegex(Exception, "process group"):
                workflow.run_agent(workspace, "prompt", {"PATH": os.environ["PATH"]}, 10, lambda *_: None, threading.Event())
            child_pid = int(pidfile.read_text())
            with self.assertRaises(ProcessLookupError):
                os.kill(child_pid, 0)

    def test_argv_test_runner_bounds_output_and_rejects_shell(self):
        with tempfile.TemporaryDirectory(dir="/tmp/opencode") as root:
            workspace = Path(root)
            with self.assertRaisesRegex(Exception, "output exceeded bounds"):
                Workflow._bounded_command([sys.executable, "-c", "import os; os.write(1,b'x'*100000)"],
                                          cwd=workspace, env={"PATH": os.environ["PATH"]}, timeout=5, limit=100)
            with self.assertRaisesRegex(Exception, "must not invoke a shell"):
                Workflow.run_tests(workspace, [["sh", "-c", "echo not run"]], env={"PATH": os.environ["PATH"]})

    def test_cancel_records_terminal_state(self):
        with tempfile.TemporaryDirectory(dir="/tmp/opencode") as state:
            workflow = FakeWorkflow()
            supervisor = Supervisor({"max_parallel": 1}, state, workflow=workflow)
            try:
                task = supervisor.start("cancel-me")
                self.assertEqual(supervisor.cancel(task["id"])["state"], "cancelled")
                result = supervisor.wait(task["id"], 3)
                self.assertEqual(result["state"], "cancelled")
                self.assertTrue(result["monitor"]["gaps"])
            finally:
                supervisor.close()

    def test_state_directory_is_single_supervisor_and_duplicate_branch_is_rejected(self):
        with tempfile.TemporaryDirectory(dir="/tmp/opencode") as state:
            supervisor = Supervisor({"max_parallel": 1}, state, workflow=FakeWorkflow())
            try:
                first = supervisor.start("same title")
                self.assertEqual(first["branch"], "agents/same-title-" + first["branch"].rsplit("-", 1)[-1])
                with self.assertRaisesRegex(ValueError, "already owns"):
                    supervisor.start("same title")
                self.assertNotIn("_settings", supervisor.snapshot()["tasks"][0])
                with self.assertRaisesRegex(RuntimeError, "already owned"):
                    Supervisor({"max_parallel": 1}, state, workflow=FakeWorkflow())
            finally:
                supervisor.close()

    def test_cancel_interrupted_task_does_not_require_runtime_event(self):
        with tempfile.TemporaryDirectory(dir="/tmp/opencode") as state:
            first = Supervisor({"max_parallel": 1}, state, workflow=FakeWorkflow())
            task = {"id": "old", "title": "old", "branch": "agents/old", "state": "monitoring",
                    "workspace": "", "chain_url": "", "changes": [], "events": [], "error": "",
                    "created_at": "now", "monitor": {"coverage": True, "deadline": None, "last_poll": None, "pending": [], "gaps": []}}
            first._tasks[task["id"]] = task
            first._save_journal()
            first.close()
            restarted = Supervisor({"max_parallel": 1}, state, workflow=FakeWorkflow())
            try:
                result = restarted.cancel("old")
                self.assertEqual(result["state"], "failed")
            finally:
                restarted.close()

    def test_unknown_write_outcome_requires_reconciliation(self):
        with tempfile.TemporaryDirectory(dir="/tmp/opencode") as state:
            supervisor = Supervisor({"max_parallel": 1}, state, workflow=UnknownWriteWorkflow())
            try:
                task = supervisor.start("write")
                outcome = supervisor.wait(task["id"], 3)
                self.assertEqual(outcome["state"], "reconciliation_required")
                self.assertIn("readback", outcome["error"])
            finally:
                supervisor.close()


if __name__ == "__main__":
    unittest.main()
