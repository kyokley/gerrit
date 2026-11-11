import unittest
import base64
import contextlib
import tempfile
import threading
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from gerrit.live import LiveAdapter
from gerrit.settings import Settings
from gerrit.workflow import UnknownWriteOutcome, Workflow
from gerrit.confirmation import ConfirmationError


class LiveAdapterTests(unittest.TestCase):
    def test_wrong_tree_readback_is_unknown_without_upload_retry(self):
        with tempfile.TemporaryDirectory(prefix="readback-live-", dir="/tmp/opencode") as temp:
            root = Path(temp)
            proof = root / "proof"
            proof.write_text("stale")
            workflow = SimpleNamespace(settings={"project": "team/project"}, state_dir=root,
                                      jj_lock=threading.RLock(), publish=lambda *_: None)
            adapter = LiveAdapter(workflow)
            description = "core: Add content\n\nChange-Id: Iabc"
            change = {"project": "team/project", "branch": "agents/task", "change_id": "Iabc",
                      "current_revision": "server-revision", "_number": 101,
                      "owner": {"username": "alice"},
                      "revisions": {"server-revision": {"_number": 2,
                                      "commit": {"message": description}}}}

            class JJ:
                uploads = 0

                def upload(self, *args, **kwargs):
                    self.uploads += 1

                def parents(self, workspace, commit, env):
                    return ["local-parent"]

            class Review:
                settings = SimpleNamespace(project="team/project", username="alice", ssh_host="host",
                                           ssh_port=29418)

                def __init__(self):
                    self.reads = 0

                def read_change(self, change_id, branch):
                    self.reads += 1
                    return change

            class Transport:
                proof_path = str(proof)
                git_wrapper = "/unused"

                def confirmed(self, operation):
                    return True

            jj = JJ()
            review = Review()
            with patch("gerrit.confirmation.verify_server_revision",
                       side_effect=ConfirmationError("tree mismatch")):
                with self.assertRaises(UnknownWriteOutcome):
                    adapter._upload_and_readback(jj, root, "local-commit", "agents/task", "remote",
                                                 {"PATH": "/usr/bin:/bin"}, Transport(), review, "Iabc",
                                                 description, None, None, "alice", "server-parent")
            self.assertEqual(jj.uploads, 1)
            self.assertEqual(review.reads, 1)

    def test_default_workflow_confirms_each_unit_before_next_agent(self):
        outcome, events, lease, monitor_state = self._run_default_fake_workflow()
        self.assertEqual(outcome["state"], "cancelled")
        expected = ["upload:probe", "readback:probe", "agent1", "upload:feature1", "readback:feature1",
                    "agent2", "upload:feature2", "readback:feature2"]
        actual = [event for event in events if event in set(expected)]
        self.assertEqual(actual[:8], expected)
        chain_index = next(i for i, event in enumerate(events) if event.startswith("event:chain:"))
        self.assertLess(chain_index, events.index("agent2"))
        self.assertLess(events.index("readback:probe"), events.index("agent2"))
        self.assertLess(events.index("readback:feature1"), events.index("agent2"))
        records = {record["_role"]: record for record in self._last_review_records}
        self.assertEqual(records["probe"]["_server_parent"], "server-base")
        self.assertEqual(records["feature1"]["_server_parent"], "server-base")
        self.assertEqual(records["feature2"]["_server_parent"], records["feature1"]["current_revision"])
        self.assertIn("release", lease.calls)
        self.assertTrue(outcome["monitor"]["stopped"])
        self.assertIn("remote:ssh://resolved-user@ssh.resolved.invalid:29418/resolved/project", events)
        self.assertEqual(len(monitor_state["agent_environments"]), 2)
        self.assertTrue(all(environment.get("GERRIT_TASK_BRANCH", "").startswith("agents/")
                            for environment in monitor_state["agent_environments"]))
        pushes = [proof for operation, proof in monitor_state["proof_snapshots"] if operation == "push"]
        self.assertEqual(len(pushes), 3)
        self.assertTrue(all(proof.count("push") == 1 for proof in pushes))

    def test_blocked_monitor_stays_owned_past_deadline_until_cancel(self):
        outcome, events, lease, monitor_state = self._run_default_fake_workflow(blocked_at_deadline=True)
        self.assertEqual(outcome["state"], "cancelled")
        self.assertEqual(monitor_state["wait_calls"], 1)
        self.assertGreater(monitor_state["ready_checks"], 0)
        self.assertFalse(outcome.get("error"))
        self.assertTrue(outcome["monitor"]["stopped"])
        self.assertFalse(outcome["monitor"]["coverage"])
        self.assertIn("release", lease.calls)

    def _run_default_fake_workflow(self, blocked_at_deadline=False):
        with tempfile.TemporaryDirectory(prefix="workflow-live-", dir="/tmp/opencode") as temp:
            root = Path(temp)
            repo = root / "repo"
            repo.mkdir()
            state_dir = root / "state"
            state_dir.mkdir()
            events = []
            class FakeCancel:
                def __init__(self):
                    self.cancelled = False
                    self.wait_calls = 0

                def is_set(self):
                    return self.cancelled

                def set(self):
                    self.cancelled = True

                def wait(self, interval):
                    self.wait_calls += 1
                    self.set()
                    return True

            cancel = FakeCancel()
            monitor_state = {"wait_calls": 0, "ready_checks": 0}
            proof_path = root / "transport-proof"
            proof_path.write_text("fetch\npush\n")
            proof_snapshots = []
            base_local = "local-base"
            base_server = "server-base"

            class FakeTransport:
                git_wrapper = "/fake/git-wrapper"

                def __init__(self):
                    self.proof_path = str(proof_path)

                def environment(self, environment):
                    return dict(environment)

                def verify(self):
                    return True

                def bind_remote(self, repository, name):
                    events.append("bind")

                def confirmed(self, operation):
                    proof = Path(self.proof_path).read_text().splitlines()
                    proof_snapshots.append((operation, proof))
                    return operation in proof

            class FakeContext:
                def __init__(self, resolved_settings):
                    self.settings = resolved_settings
                    self.base = resolved_settings.http_url.rstrip("/")
                    self.transport = FakeTransport()
                    self.key_path = "/fake/key"
                    self.token = "fake-token"

                def environment(self):
                    return {"PATH": "/usr/bin:/bin"}

            class FakeJJ:
                review = None

                def __init__(self, repository, **kwargs):
                    self.commits = {}
                    self.workspaces = {}
                    self.stable_counter = 0
                    self.commit_counter = 0
                    self.review = FakeJJ.review

                def capability(self, env):
                    return {key: True for key in ("upload", "workspace", "fetch", "remote", "at_operation",
                                                   "ignore_working_copy", "config", "squash")}

                def verify_transport_config(self, repo, wrapper, env):
                    return True

                def add_remote(self, workspace, name, url, env):
                    self.remote = (name, url)
                    events.append(f"remote:{url}")

                def remote_url(self, workspace, name, env):
                    return self.remote[1]

                def fetch_branch(self, workspace, remote, branch, env):
                    pass

                def remote_commit_id(self, workspace, remote, branch, env):
                    return base_server

                def add_workspace(self, destination, name, revision, env):
                    path = Path(destination)
                    path.mkdir(parents=True, exist_ok=True)
                    self.stable_counter += 1
                    self.workspaces[str(path)] = {"head": base_local, "stable": f"stable-{self.stable_counter}",
                                                  "files": {}, "owned": []}
                    return path

                def change_id(self, workspace, revision="@"):
                    state = self.workspaces[str(Path(workspace))]
                    return self.commits[revision]["stable"] if revision in self.commits else state["stable"]

                def diff(self, workspace, revision, env):
                    if revision in self.commits:
                        return self.commits[revision]["diff"]
                    path = Path(workspace)
                    files = {str(item.relative_to(path)): item.read_text()
                             for item in path.rglob("*") if item.is_file()}
                    state = self.workspaces[str(path)]
                    changes = {name: content for name, content in files.items()
                               if state["files"].get(name) != content}
                    removed = set(state["files"]) - set(files)
                    return "\n".join([*(f"+{name}:{value}" for name, value in changes.items()),
                                      *(f"-{name}" for name in removed)])

                def commit(self, workspace, description, env):
                    path = Path(workspace)
                    state = self.workspaces[str(path)]
                    files = {str(item.relative_to(path)): item.read_text()
                             for item in path.rglob("*") if item.is_file()}
                    difference = self.diff(path, "@", env)
                    self.commit_counter += 1
                    commit = f"local-{self.commit_counter}"
                    stable = state["stable"]
                    self.commits[commit] = {"parent": state["head"], "files": files, "diff": difference,
                                            "description": description, "stable": stable}
                    state["head"] = commit
                    state["files"] = files
                    state["owned"].append(commit)
                    self.stable_counter += 1
                    state["stable"] = f"stable-{self.stable_counter}"
                    return commit

                def mutable_ancestors(self, workspace, revision="@", env=None):
                    state = self.workspaces[str(Path(workspace))]
                    return list(state["owned"])

                def parents(self, workspace, revision, env):
                    return [self.commits[revision]["parent"]]

                def is_ancestor(self, workspace, ancestor, revision, env):
                    return False

                def upload(self, workspace, revision, branch, remote, *, dry_run, env):
                    if dry_run:
                        return "preview"
                    record = self.commits[revision]
                    self.review.upload(self, revision, branch, record)
                    with open(FakeJJ.proof_path, "a", encoding="utf-8") as stream:
                        stream.write("push\n")
                    return "uploaded"

                def verify_preview(self, workspace, output, commits, branch, remote, env):
                    return commits

            class FakeReview:
                settings = None

                def __init__(self, resolved_settings):
                    self.settings = resolved_settings
                    self.base = resolved_settings.http_url.rstrip("/")
                    self.records = {}
                    self.server_for_local = {base_local: base_server}
                    self.counter = 0
                    self.last_record = None
                    class Response:
                        def __init__(self, payload):
                            self.payload = payload
                        def __enter__(self):
                            return self
                        def __exit__(self, *_args):
                            return False
                        def read(self, limit=-1):
                            return self.payload[:limit]
                    class Opener:
                        def open(inner, request, timeout=10):
                            content = next(iter(self.last_record["_tree"].values())).encode()
                            return Response(base64.b64encode(content))
                    self.opener = Opener()

                @staticmethod
                def _segment(value):
                    return value

                def _request(self, method, path):
                    return {".gerrit-readiness-probe": {}}

                def verify_identity(self):
                    return {"username": self.settings.username}

                def ssh_keys(self):
                    return [{"valid": True, "ssh_public_key": "ssh-ed25519 fakekey"}]

                def ensure_branch(self, branch):
                    return base_server

                def feedback(self, changes):
                    return []

                def upload(self, jj, commit, branch, local_record):
                    self.counter += 1
                    role = "probe" if "readiness" in local_record["description"] else f"feature{self.counter - 1}"
                    events.append(f"upload:{role}")
                    revision = f"server-revision-{self.counter}"
                    parent_local = local_record["parent"]
                    server_parent = self.server_for_local[parent_local]
                    number = 900 + self.counter
                    change_id = local_record["description"].split("Change-Id: ", 1)[1].strip()
                    record = {"project": self.settings.project, "branch": branch, "change_id": change_id,
                              "current_revision": revision, "_number": number,
                              "owner": {"username": self.settings.username},
                              "revisions": {revision: {"_number": 1, "commit": {"message": local_record["description"]}}},
                              "_server_parent": server_parent, "_tree": local_record["files"], "_role": role}
                    self.records[change_id] = record
                    self.server_for_local[commit] = revision

                def read_change(self, change_id, branch):
                    record = self.records[change_id]
                    self.last_record = record
                    events.append(f"readback:{record['_role']}")
                    return record

                def verify_revision(self, change_id, branch, revision):
                    return True

            class FakeLease:
                def __init__(self):
                    self.calls = []

                def acquire(self, cancel_event):
                    self.calls.append("acquire")
                    return not cancel_event.is_set()

                def release(self):
                    self.calls.append("release")

            class FakeMonitor:
                def __init__(self, review, changes, **kwargs):
                    self.deadline = kwargs["deadline"]
                    self.changed = changes
                    self.stopped = False
                    self.coverage = False

                def start(self):
                    self.coverage = True

                def snapshot(self):
                    return {"coverage": self.coverage, "deadline": self.deadline, "pending": [],
                            "checks": [{"supported": False}] if blocked_at_deadline else [],
                            "gaps": [], "stopped": self.stopped}

                def set_changes(self, changes):
                    self.changed = changes

                def feedback(self, fresh_only=False):
                    return []

                def claim_feedback(self, items):
                    return items

                def handled(self, items, replied_ids):
                    pass

                def check_health(self):
                    return self.snapshot()

                def ready_to_complete(self):
                    monitor_state["ready_checks"] += 1
                    return False if blocked_at_deadline else False

                def stop(self, reason, completed=False):
                    self.stopped = True
                    self.coverage = False

            def fake_clock():
                fake_clock.calls += 1
                return 1000 if fake_clock.calls == 1 else 50000
            fake_clock.calls = 0

            unresolved = Settings(
                repository=str(repo), http_url="", ssh_host="", username="", project="",
                known_hosts="", credential_mode="secretspec", spec_file="/fake/spec",
                field_env={"http_url": "GERRIT_HTTP", "ssh_host": "GERRIT_HOST", "username": "GERRIT_USER",
                           "project": "GERRIT_PROJECT", "known_hosts": "GERRIT_KNOWN_HOSTS"},
                max_units=2, poll_interval=300,
            )
            resolved = Settings.from_dict({**unresolved.to_dict(), "http_url": "https://review.invalid/context",
                                           "ssh_host": "ssh.resolved.invalid", "username": "resolved-user",
                                           "project": "resolved/project", "known_hosts": "/fake/known_hosts",
                                           "field_env": {}})
            context = FakeContext(resolved)
            review = FakeReview(resolved)
            review.context = context
            FakeJJ.review = review
            FakeJJ.proof_path = str(proof_path)
            lease = FakeLease()
            workflow = Workflow(unresolved, state_dir, jj_lock=threading.RLock(), lease=lease)
            agent_calls = 0
            agent_environments = []

            def run_agent(workspace, prompt, environment, timeout, emit, cancel_event):
                nonlocal agent_calls
                agent_calls += 1
                agent_environments.append(environment)
                events.append(f"agent{agent_calls}")
                target = Path(workspace) / "unit.txt"
                target.write_text((target.read_text() if target.exists() else "") + f"unit {agent_calls}\n")
                return {"done": agent_calls == 2, "message": f"core: Add unit {agent_calls}",
                        "tests": [["true"]], "replies": []}

            workflow.run_agent = run_agent
            workflow.run_tests = lambda *args, **kwargs: [{"argv": ["true"], "returncode": 0}]
            @contextlib.contextmanager
            def credential_context(settings):
                yield context

            def emit_event(kind, text):
                events.append(f"event:{kind}:{text}")
                if kind == "chain" and "readback:feature2" in events and not blocked_at_deadline:
                    cancel.set()

            monitor_patch = patch("gerrit.monitoring.ReviewMonitor", FakeMonitor) if blocked_at_deadline else contextlib.nullcontext()
            clock_patch = patch("gerrit.live.time.time", side_effect=fake_clock) if blocked_at_deadline else contextlib.nullcontext()

            def record_fetch_proof(*args, **kwargs):
                with open(proof_path, "a", encoding="utf-8") as stream:
                    stream.write("fetch\n")

            with patch("gerrit.credentials.credentials", credential_context), \
                 patch("gerrit.live.Jujutsu", FakeJJ), \
                 patch("gerrit.review.GerritClient", side_effect=lambda _context: review), \
                 patch("gerrit.live.LiveAdapter._bounded_helper", return_value={"returncode": 0,
                                                                                "stdout": "ssh-ed25519 fakekey"}), \
                 patch("gerrit.confirmation.verify_server_revision", side_effect=record_fetch_proof), \
                 monitor_patch, clock_patch:
                outcome = workflow.run({"id": "task-123456789012", "title": "Test default lane"},
                                       emit_event, cancel)

            self.assertFalse(any(record["_role"] == "probe" and record["_server_parent"] != base_server
                                 for record in review.records.values()))
            self.assertEqual(context.settings.project, "resolved/project")
            monitor_state["wait_calls"] = cancel.wait_calls
            monitor_state["proof_snapshots"] = proof_snapshots
            monitor_state["agent_environments"] = agent_environments
            self._last_review_records = list(review.records.values())
            return outcome, events, lease, monitor_state

    def test_feedback_correction_is_tip_only(self):
        probe = {"role": "readiness_probe", "change_id": "Iprobe"}
        first = {"role": "feature", "change_id": "Ifirst"}
        second = {"role": "feature", "change_id": "Isecond"}
        self.assertFalse(LiveAdapter._feedback_target_is_tip([first, second], first))
        self.assertTrue(LiveAdapter._feedback_target_is_tip([first, second], second))
        self.assertFalse(LiveAdapter._feedback_target_is_tip([first, second], probe))
        self.assertFalse(LiveAdapter._feedback_target_is_tip([], first))

    def test_tip_feedback_correction_refreshes_owned_stack_and_binds_reply(self):
        with tempfile.TemporaryDirectory(prefix="tip-feedback-", dir="/tmp/opencode") as temp:
            root = Path(temp)
            state_dir = root / "state"
            state_dir.mkdir()
            feature_workspace = root / "feature"
            feature_workspace.mkdir()
            (feature_workspace / "feature.txt").write_text("first\nsecond\n")
            project = "team/project"
            branch = "agents/task"
            change_ids = ("I" + "1" * 40, "I" + "2" * 40)
            descriptions = tuple(f"core: Add feature {index}\n\nChange-Id: {change_ids[index - 1]}"
                                 for index in (1, 2))
            local_commits = {
                "local-first": {"parent": "local-base", "stable": "stable-first",
                                "description": descriptions[0], "files": {"feature.txt": "first\n"},
                                "diff": "+first"},
                "local-tip": {"parent": "local-first", "stable": "stable-tip",
                              "description": descriptions[1], "files": {"feature.txt": "first\nsecond\n"},
                              "diff": "+second"},
            }
            server_revisions = ("server-first", "server-tip-old")
            changes = []
            for index, commit in enumerate(("local-first", "local-tip")):
                entry = LiveAdapter._mapping(local_commits[commit]["stable"], commit, change_ids[index],
                                             descriptions[index], local_commits[commit]["diff"], "confirmed")
                entry.update({"role": "feature", "branch": branch, "server_change": str(101 + index),
                              "patchset": 1, "server_revision": server_revisions[index],
                              "server_owner": "alice", "tests": [["true"]]})
                changes.append(entry)

            class JJ:
                def __init__(self):
                    self.commits = dict(local_commits)
                    self.states = {str(feature_workspace): {"head": "local-tip", "stable": "stable-empty",
                                 "files": {"feature.txt": "first\nsecond\n"},
                                 "owned": ["local-first", "local-tip"]}}
                    self.update_calls = []
                    self.squash_calls = []
                    self.mapping = None

                @staticmethod
                def tree_diff(parent, child):
                    return "\n".join(f"{name}:{value}" for name, value in child.items()
                                      if parent.get(name) != value)

                def diff(self, workspace, revision, env):
                    if revision in self.commits:
                        return self.commits[revision]["diff"]
                    path = Path(workspace)
                    state = self.states[str(path)]
                    files = {str(item.relative_to(path)): item.read_text()
                             for item in path.rglob("*") if item.is_file()}
                    return self.tree_diff(state["files"], files)

                def add_workspace(self, destination, name, revision, env):
                    path = Path(destination)
                    path.mkdir(parents=True)
                    revision_data = self.commits[revision]
                    for filename, content in revision_data["files"].items():
                        (path / filename).write_text(content)
                    chain = ["local-first", "local-tip"]
                    self.states[str(path)] = {"head": revision, "stable": "stable-fix-empty",
                                              "files": dict(revision_data["files"]), "owned": chain[:chain.index(revision) + 1]}
                    return path

                def change_id(self, workspace, revision="@"):
                    return self.commits[revision]["stable"] if revision in self.commits else self.states[str(Path(workspace))]["stable"]

                def commit(self, workspace, description, env):
                    path = Path(workspace)
                    state = self.states[str(path)]
                    files = {str(item.relative_to(path)): item.read_text()
                             for item in path.rglob("*") if item.is_file()}
                    commit = "local-fix"
                    self.commits[commit] = {"parent": state["head"], "stable": "stable-fix",
                                            "description": description, "files": files,
                                            "diff": self.tree_diff(state["files"], files)}
                    state["head"] = commit
                    state["stable"] = "stable-after-fix"
                    state["files"] = files
                    state["owned"].append(commit)
                    return commit

                def mutable_ancestors(self, workspace, revision="@", env=None):
                    state = self.states[str(Path(workspace))]
                    if revision not in state["owned"]:
                        return list(state["owned"])
                    return state["owned"][:state["owned"].index(revision) + 1]

                def squash_into(self, workspace, source, target, env):
                    self.squash_calls.append((str(workspace), source, target))
                    old = self.commits[target]
                    replacement = "local-tip-fixed"
                    fixed = self.commits[source]
                    parent_files = self.commits[old["parent"]]["files"] if old["parent"] in self.commits else {}
                    self.commits[replacement] = {"parent": old["parent"], "stable": old["stable"],
                                                 "description": old["description"], "files": fixed["files"],
                                                 "diff": self.tree_diff(parent_files, fixed["files"])}
                    self.mapping = {target: replacement}

                def update_stale(self, workspace, env):
                    self.update_calls.append(str(workspace))
                    state = self.states[str(Path(workspace))]
                    state["owned"][-1] = self.mapping[state["owned"][-1]]
                    state["head"] = self.mapping[state["head"]]
                    state["files"] = dict(self.commits[state["head"]]["files"])
                    for filename, content in state["files"].items():
                        (Path(workspace) / filename).write_text(content)

                def mutable_map(self, workspace, env):
                    state = self.states[str(Path(workspace))]
                    return {self.commits[commit]["stable"]: commit for commit in state["owned"]}

                def describe(self, workspace, revision):
                    return self.commits[revision]["description"]

                def parents(self, workspace, revision, env):
                    return [self.commits[revision]["parent"]]

                def upload(self, workspace, revision, branch_name, remote, *, dry_run, env):
                    if dry_run:
                        return "preview"
                    review.upload(revision, branch_name, self.commits[revision])
                    with open(proof_path, "a", encoding="utf-8") as stream:
                        stream.write("push\n")
                    return "uploaded"

                def verify_preview(self, workspace, output, commits, branch_name, remote, env):
                    return commits

            class Transport:
                git_wrapper = "/fake/wrapper"

                def __init__(self):
                    self.proof_path = str(proof_path)

                def confirmed(self, operation):
                    return operation in Path(self.proof_path).read_text().splitlines()

            class Review:
                settings = SimpleNamespace(project=project, username="alice", ssh_host="review.invalid",
                                           ssh_port=29418)

                def __init__(self):
                    self.records = {}
                    for index, entry in enumerate(changes):
                        rev = entry["server_revision"]
                        self.records[entry["change_id"]] = {
                            "project": project, "branch": branch, "change_id": entry["change_id"],
                            "current_revision": rev, "_number": 101 + index,
                            "owner": {"username": "alice"},
                            "revisions": {rev: {"_number": 1,
                                "commit": {"message": entry["description"]}}}}
                    self.replies = []
                    self.verify_calls = []

                def verify_revision(self, change_id, branch_name, revision):
                    self.verify_calls.append((change_id, revision))

                def read_change(self, change_id, branch_name):
                    return self.records[change_id]

                def upload(self, revision, branch_name, record):
                    change_id = record["description"].split("Change-Id: ", 1)[1]
                    server_revision = "server-tip-corrected"
                    number = 102
                    self.records[change_id] = {
                        "project": project, "branch": branch_name, "change_id": change_id,
                        "current_revision": server_revision, "_number": number,
                        "owner": {"username": "alice"},
                        "revisions": {server_revision: {"_number": 2,
                            "commit": {"message": record["description"]}}}}

                def post_review(self, change_id, branch_name, revision, message):
                    self.replies.append((change_id, branch_name, revision, message))

            class Context:
                def environment(self):
                    return {"PATH": "/usr/bin:/bin"}

            class Monitor:
                def __init__(self):
                    self.items = [{"id": "comment-tip", "kind": "comment", "change_id": change_ids[1]}]
                    self.handled_ids = set()
                    self.feedback_calls = 0
                    self.changes = []

                def check_health(self):
                    return {"coverage": True}

                def handled(self, items, replied_ids):
                    self.handled_ids.update(replied_ids)
                    self.items = [item for item in self.items if item["id"] not in replied_ids]

                def set_changes(self, items):
                    self.changes = items

                def feedback(self, fresh_only=False):
                    self.feedback_calls += 1
                    return list(self.items)

            proof_path = root / "proof"
            proof_path.write_text("stale proof\n")
            fake_jj = JJ()
            review = Review()
            workflow = SimpleNamespace(settings={"project": project}, state_dir=state_dir,
                                       jj_lock=threading.RLock(), publish=lambda *_: None)
            workflow.run_agent = lambda workspace, prompt, environment, timeout, emit, cancel: (
                (Path(workspace) / "feature.txt").write_text("first\nsecond corrected\n") and
                {"done": False, "message": "core: Correct tip", "tests": [["true"]],
                 "replies": [{"feedback_id": "comment-tip", "change_id": change_ids[1],
                              "message": "Fixed in this revision."}]})
            workflow.run_tests = lambda workspace, commands, **kwargs: [{"argv": ["true"], "returncode": 0}]
            adapter = LiveAdapter(workflow)
            monitor = Monitor()
            adapter._monitor_object = monitor
            adapter._server_base_revision = "server-base"
            settings = SimpleNamespace(agent_timeout=30, username="alice", http_url="https://review.invalid",
                                       ssh_host="review.invalid", ssh_port=29418, project=project,
                                       base_branch="main")
            feedback = list(monitor.items)
            proofs = []
            def confirm(**kwargs):
                proofs.append((kwargs["local_revision"], kwargs["expected_parent"],
                               kwargs["expected_server_parent"]))
                with open(proof_path, "a", encoding="utf-8") as stream:
                    stream.write("fetch\n")

            with patch("gerrit.confirmation.verify_server_revision", side_effect=confirm):
                did_correct, units_run, replied = adapter._feedback_unit(
                    {"id": "task-tip", "title": "Tip correction"}, settings, feature_workspace,
                    fake_jj, "remote", branch, {"PATH": "/usr/bin:/bin"}, Transport(), review,
                    changes, ["local-first", "local-tip"], changes[1], feedback, Context(),
                    lambda *_: None, threading.Event(), 0)

            self.assertTrue(did_correct)
            self.assertEqual(units_run, 1)
            self.assertEqual(fake_jj.squash_calls[0][2], "local-tip")
            self.assertEqual(fake_jj.update_calls, [str(feature_workspace)])
            self.assertEqual(changes[1]["local_commit_id"], "local-tip-fixed")
            self.assertEqual(changes[1]["status"], "confirmed")
            self.assertEqual(review.verify_calls, [(change_ids[0], "server-first"),
                                                   (change_ids[1], "server-tip-old")])
            self.assertEqual(set(proofs), {("local-first", "local-base", "server-base"),
                                          ("local-tip-fixed", "local-first", "server-first")})
            self.assertEqual(review.replies, [(change_ids[1], branch, "server-tip-corrected",
                                               "Fixed in this revision.")])
            monitor.handled(feedback, replied)
            monitor.set_changes(changes)
            self.assertEqual(monitor.feedback(fresh_only=True), [])
            self.assertEqual(monitor.handled_ids, {"comment-tip"})
            self.assertEqual(monitor.feedback_calls, 1)

    def test_ancestor_feedback_returns_before_workspace_mutation(self):
        first = {"role": "feature", "change_id": "Ifirst"}
        second = {"role": "feature", "change_id": "Isecond"}
        workflow = type("Workflow", (), {"settings": {}, "state_dir": None,
                                          "jj_lock": None, "publish": lambda *_: None})()
        adapter = LiveAdapter(workflow)
        corrected, units, replies = adapter._feedback_unit(
            {"id": "task"}, None, None, None, None, None, None, None, None,
            [first, second], ["commit-1", "commit-2"], first, [], None, lambda *_: None,
            None, 0,
        )
        self.assertFalse(corrected)
        self.assertEqual(units, 0)
        self.assertEqual(replies, set())

    def test_feature_chain_url_excludes_exact_probe_change_id(self):
        settings = type("Settings", (), {"http_url": "https://review.invalid/context", "project": "a/b"})()
        url = LiveAdapter._feature_chain_url(settings, "agents/task", "Iprobe")
        self.assertEqual(
            url,
            "https://review.invalid/context/q/project%3Aa%2Fb%20branch%3Aagents%2Ftask%20-change%3AIprobe",
        )

    def test_ssh_destination_uses_resolved_secretspec_fields(self):
        resolved = type("Resolved", (), {"ssh_host": "ssh.resolved.invalid", "username": "resolved-user",
                                         "ssh_port": 29418, "project": "resolved/project"})()
        self.assertEqual(LiveAdapter._ssh_url(resolved),
                         "ssh://resolved-user@ssh.resolved.invalid:29418/resolved/project")

    def test_monitor_coverage_completes_independently_of_task_failure(self):
        workflow = type("Workflow", (), {"settings": {}, "state_dir": None,
                                          "jj_lock": None, "publish": lambda *_: None})()
        adapter = LiveAdapter(workflow)
        state = {"coverage": True, "stopped": False, "gaps": []}

        class Monitor:
            def ready_to_complete(self):
                return True

            def stop(self, reason, completed=False):
                state["coverage"] = False
                state["stopped"] = True
                state["completed"] = completed

            def snapshot(self):
                return dict(state)

        class Lease:
            released = False

            def release(self):
                self.released = True

        lease = Lease()

        def run_impl(task, emit, cancel, passed_lease):
            adapter._monitor_object = Monitor()
            return {"state": "failed", "_monitor_completed": True, "monitor": {"stopped": False}}

        with patch.object(adapter, "_run", side_effect=run_impl):
            outcome = adapter.run({}, lambda *_: None, None, lease)
        self.assertEqual(outcome["state"], "failed")
        self.assertTrue(lease.released)
        self.assertTrue(outcome["monitor"]["stopped"])
        self.assertFalse(outcome["monitor"]["coverage"])
        self.assertTrue(outcome["monitor"]["completed"])


if __name__ == "__main__":
    unittest.main()
