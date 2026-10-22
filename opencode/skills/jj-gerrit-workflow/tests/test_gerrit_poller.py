import json
import io
import os
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

from scripts.gerrit_poller import GerritPoller, PollError, load_config, secure_read, verify_identity


class Clock:
    def __init__(self):
        self.value = 100.0

    def __call__(self):
        return self.value


class FakeClient:
    def __init__(self):
        self.identity = {"_account_id": 7, "username": "bot"}
        self.details = {}
        self.comments = {}
        self.fail_at = None

    def get(self, path):
        if self.fail_at == path:
            raise PollError("Gerrit request failed (HTTP 401)")
        if path.endswith("/accounts/self"):
            return self.identity
        number = int(path.split("/")[3])
        if path.endswith("/comments"):
            return self.comments[number]
        return self.details[number]


class PollerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        os.chmod(self.root, 0o700)
        self.token = self.root / "token"
        self.token.write_text("secret-token\n")
        os.chmod(self.token, 0o600)
        self.config_path = self.root / "config.json"
        self.config = {
            "rest_base_url": "https://gerrit.example/",
            "username": "bot",
            "expected_account_id": 7,
            "project": "team/project",
            "branch": "main",
            "token_file": str(self.token),
        }
        self.config_path.write_text(json.dumps(self.config))
        os.chmod(self.config_path, 0o600)
        self.db = self.root / "poller.sqlite3"
        self.clock = Clock()
        self.client = FakeClient()

    def tearDown(self):
        self.tmp.cleanup()

    def make_poller(self, interval=300):
        return GerritPoller(
            self.config_path, self.db, interval=interval,
            client=self.client, monotonic=self.clock,
        )

    def enroll(self, poller, *numbers):
        for number in numbers:
            poller.enroll(number)
            self.client.details[number] = {
                "_number": number, "project": "team/project", "branch": "main",
                "messages": [], "current_revision": "rev1",
            }
            self.client.comments[number] = {}

    def test_secure_token_rejects_symlink_and_permissive_mode(self):
        link = self.root / "token-link"
        link.symlink_to(self.token)
        with self.assertRaises(PollError):
            secure_read(link)
        os.chmod(self.token, 0o644)
        with self.assertRaises(PollError):
            secure_read(self.token)

    def test_config_and_token_security_and_binding(self):
        poller = self.make_poller()
        self.enroll(poller, 12)
        poller.close()
        changed = dict(self.config, branch="release")
        self.config_path.write_text(json.dumps(changed))
        with self.assertRaises(PollError):
            self.make_poller()

    def test_rejects_unscoped_or_non_numeric_enrollment(self):
        poller = self.make_poller()
        for value in (0, -1, "12", True):
            with self.assertRaises(PollError):
                poller.enroll(value)

    def test_edits_create_distinct_deduplicated_feedback_events(self):
        poller = self.make_poller()
        self.enroll(poller, 12)
        self.client.comments[12] = {
            "src/a.py": [{"id": "c1", "message": "first", "author": {"_account_id": 8}, "patch_set": 2, "in_reply_to": "root", "unresolved": True, "updated": "2026-01-01T00:00:00Z", "line": 7}]
        }
        poller.poll()
        poller.poll()
        self.client.comments[12]["src/a.py"][0]["message"] = "edited"
        poller.poll()
        events = poller.pending_events()
        self.assertEqual(len(events), 2)
        self.assertEqual({e["payload"]["message"] for e in events}, {"first", "edited"})
        self.assertEqual({e["payload"]["patch_set"] for e in events}, {2})
        self.assertEqual({e["payload"]["id"] for e in events}, {"c1"})
        self.assertEqual({e["payload"]["in_reply_to"] for e in events}, {"root"})
        self.assertTrue(all(e["payload"]["line"] == 7 for e in events))

    def test_resolution_only_edit_is_a_distinct_event(self):
        poller = self.make_poller()
        self.enroll(poller, 12)
        self.client.comments[12] = {"src/a.py": [{"id": "c1", "message": "done?", "patch_set": 1, "unresolved": True}]}
        poller.poll()
        self.client.comments[12]["src/a.py"][0]["unresolved"] = False
        poller.poll()
        self.assertEqual(len(poller.pending_events()), 2)
        self.assertEqual({e["payload"]["unresolved"] for e in poller.pending_events()}, {False, True})

    def test_coverage_requires_successful_bounded_polls_and_resets_on_failure(self):
        poller = self.make_poller(interval=10)
        self.enroll(poller, 12)
        poller.poll()
        self.clock.value += 10
        poller.poll()
        self.assertEqual(poller.status()["changes"][0]["coverage_seconds"], 10)
        self.clock.value += 10
        self.client.fail_at = "/a/accounts/self"
        with self.assertRaises(PollError):
            poller.poll()
        self.assertEqual(poller.status()["last_error"], "Gerrit request failed (HTTP 401)")
        self.client.fail_at = None
        self.clock.value += 10
        poller.poll()
        self.assertEqual(poller.status()["changes"][0]["coverage_seconds"], 10)
        self.clock.value += 21
        poller.poll()
        self.clock.value += 10
        poller.poll()
        self.assertEqual(poller.status()["changes"][0]["coverage_seconds"], 20)

    def test_new_enrollment_starts_coverage_at_first_observation(self):
        poller = self.make_poller(interval=10)
        self.enroll(poller, 12)
        poller.poll()
        self.clock.value += 10
        poller.poll()
        poller.enroll(13)
        self.client.details[13] = {"_number": 13, "project": "team/project", "branch": "main", "messages": [], "current_revision": "rev1"}
        self.client.comments[13] = {}
        self.clock.value += 10
        poller.poll()
        coverage = {row["change_number"]: row["coverage_seconds"] for row in poller.status()["changes"]}
        self.assertEqual(coverage, {12: 20, 13: 0})
        self.clock.value += 10
        poller.poll()
        coverage = {row["change_number"]: row["coverage_seconds"] for row in poller.status()["changes"]}
        self.assertEqual(coverage, {12: 30, 13: 10})

    def test_pending_events_survive_coverage_and_ack_is_explicit(self):
        poller = self.make_poller(interval=10)
        self.enroll(poller, 12)
        self.client.details[12]["messages"] = [{"id": "m1", "message": "please fix", "author": {"_account_id": 8}, "_revision_number": 3}]
        poller.poll()
        self.assertEqual(len(poller.pending_events()), 1)
        self.assertEqual(poller.pending_events()[0]["payload"]["revision"], 3)
        poller.ack(poller.pending_events()[0]["event_id"])
        self.assertEqual(poller.pending_events(), [])

    def test_scope_mismatch_and_identity_failure_do_not_credit(self):
        poller = self.make_poller()
        self.enroll(poller, 12)
        self.client.details[12]["branch"] = "other"
        with self.assertRaises(PollError):
            poller.poll()
        self.assertEqual(poller.status()["changes"][0]["coverage_seconds"], 0)
        self.client.details[12]["branch"] = "main"
        self.client.identity["_account_id"] = 99
        with self.assertRaises(PollError):
            poller.poll()

    def test_restart_and_failed_response_do_not_credit_interprocess_gap(self):
        p1 = self.make_poller(interval=10)
        self.enroll(p1, 12)
        p1.poll()
        p1.close()
        self.clock.value += 100
        p2 = self.make_poller(interval=10)
        self.client.fail_at = "/a/accounts/self"
        with self.assertRaises(PollError):
            p2.poll()
        self.client.fail_at = None
        self.clock.value += 10
        p2.poll()
        self.assertEqual(p2.status()["changes"][0]["coverage_seconds"], 0)

    def test_transaction_rolls_back_events_on_failure(self):
        poller = self.make_poller()
        self.enroll(poller, 12)
        self.client.details[12]["messages"] = [{"id": "m1", "message": "existing", "_revision_number": 1}]
        poller.poll()
        self.clock.value += 10
        poller.poll()
        previous_success = poller.status()["last_success_utc"]
        self.client.details[12]["messages"].append({"id": "m2", "message": "will rollback", "_revision_number": 1})
        poller.db.execute("CREATE TRIGGER fail_new_event BEFORE INSERT ON events WHEN NEW.kind='message' AND NEW.payload LIKE '%will rollback%' BEGIN SELECT RAISE(ABORT, 'injected'); END")
        self.clock.value += 10
        with self.assertRaises(PollError):
            poller.poll()
        self.assertEqual(len(poller.pending_events()), 1)
        self.assertEqual(poller.pending_events()[0]["payload"]["message"], "existing")
        self.assertEqual(poller.status()["changes"][0]["coverage_seconds"], 10)
        self.assertEqual(poller.status()["last_success_utc"], previous_success)

    def test_rest_client_rejects_redirects(self):
        from scripts.gerrit_poller import RestClient, _NoRedirect

        client = RestClient(self.config, str(self.token), allow_http_loopback=False)
        self.assertTrue(any(isinstance(handler, _NoRedirect) for handler in client.opener.handlers))
        req = __import__("urllib.request").request.Request("https://gerrit.example/a/accounts/self")
        with self.assertRaises(PollError):
            _NoRedirect().redirect_request(req, None, 302, "Found", {"Location": "https://elsewhere.invalid"}, "https://elsewhere.invalid")

    def test_config_validation_and_http_loopback_gate(self):
        bad = dict(self.config, rest_base_url="http://gerrit.example/")
        self.config_path.write_text(json.dumps(bad))
        with self.assertRaises(PollError):
            load_config(self.config_path)
        good = dict(bad, rest_base_url="http://127.0.0.1:8080/")
        self.config_path.write_text(json.dumps(good))
        os.chmod(self.config_path, 0o600)
        self.assertEqual(load_config(self.config_path, allow_http_loopback=True)["project"], "team/project")
        for host in ("127.0.0.1", "[::1]"):
            loopback = dict(good, rest_base_url=f"http://{host}:8080/")
            self.config_path.write_text(json.dumps(loopback))
            self.assertEqual(load_config(self.config_path, allow_http_loopback=True)["project"], "team/project")
        for host in ("localhost", "127.0.0.1.evil", "192.0.2.1"):
            not_loopback = dict(good, rest_base_url=f"http://{host}:8080/")
            self.config_path.write_text(json.dumps(not_loopback))
            with self.assertRaises(PollError):
                load_config(self.config_path, allow_http_loopback=True)
        malformed = dict(self.config, rest_base_url="https://[broken/")
        self.config_path.write_text(json.dumps(malformed))
        with self.assertRaises(PollError):
            load_config(self.config_path)

    def test_http_mode_disables_proxy_environment(self):
        from scripts.gerrit_poller import RestClient
        request = __import__("urllib.request").request
        with patch("urllib.request.build_opener", wraps=request.build_opener) as build:
            RestClient(dict(self.config, rest_base_url="http://127.0.0.1:8080/"), str(self.token), allow_http_loopback=True)
        proxy = next(arg for arg in build.call_args.args if isinstance(arg, request.ProxyHandler))
        self.assertEqual(proxy.proxies, {})

    def test_change_lookup_uses_numeric_id_and_validates_response_number(self):
        poller = self.make_poller()
        self.enroll(poller, 12)
        self.assertEqual(poller._change_path(12, "comments"), "/a/changes/12/comments")
        self.client.details[12]["_number"] = 13
        with self.assertRaises(PollError):
            poller.poll()

    def test_config_and_identity_reject_boolean_account_ids_and_bad_credentials(self):
        bad = dict(self.config, username="bot:attacker")
        self.config_path.write_text(json.dumps(bad))
        with self.assertRaises(PollError):
            load_config(self.config_path)
        bad = dict(self.config, expected_account_id=True)
        self.config_path.write_text(json.dumps(bad))
        with self.assertRaises(PollError):
            load_config(self.config_path)
        config = dict(self.config, expected_account_id=1)
        self.config_path.write_text(json.dumps(config))
        poller = self.make_poller()
        self.enroll(poller, 12)
        self.client.identity["_account_id"] = True
        with self.assertRaises(PollError):
            poller.poll()

    def test_concurrent_database_lock_is_exclusive(self):
        first = self.make_poller()
        with self.assertRaises(PollError):
            self.make_poller()
        first.close()

    def test_auth_failure_does_not_echo_token_or_response(self):
        from scripts.gerrit_poller import RestClient
        client = RestClient(self.config, str(self.token))
        with patch("urllib.request.OpenerDirector.open", side_effect=__import__("urllib.error").error.HTTPError("url", 401, "expired secret-token", {}, None)):
            with self.assertRaises(PollError) as err:
                client.get("/a/accounts/self")
        self.assertNotIn("secret-token", str(err.exception))
        self.assertNotIn("expired", str(err.exception))
        self.assertEqual(str(err.exception), "Gerrit request failed (HTTP 401)")

    def test_invalid_utf8_token_is_sanitized(self):
        from scripts.gerrit_poller import RestClient
        self.token.write_bytes(b"\xff")
        client = RestClient(self.config, str(self.token))
        with self.assertRaises(PollError):
            client.get("/a/accounts/self")

    def test_verify_checks_both_expected_id_and_username(self):
        self.assertEqual(verify_identity(self.config, self.client), {"verified": True, "account_id": 7, "username": "bot"})
        self.client.identity["username"] = "other"
        with self.assertRaises(PollError):
            verify_identity(self.config, self.client)
        self.client.identity = {"_account_id": True, "username": "bot"}
        config = dict(self.config, expected_account_id=1)
        with self.assertRaises(PollError):
            verify_identity(config, self.client)

    def test_verify_cli_needs_no_database_and_emits_no_token(self):
        from scripts.gerrit_poller import _main
        output = io.StringIO()
        with patch("scripts.gerrit_poller.RestClient", return_value=self.client):
            with redirect_stdout(output):
                result = _main(["--config", str(self.config_path), "verify"])
        self.assertEqual(result, 0)
        self.assertEqual(json.loads(output.getvalue()), {"verified": True, "account_id": 7, "username": "bot"})
        self.assertNotIn("secret-token", output.getvalue())
        self.assertFalse(self.db.exists())

    def test_malformed_response_is_not_a_success(self):
        from scripts.gerrit_poller import RestClient
        client = RestClient(self.config, str(self.token))
        class Response:
            status = 200
            def read(self, *_): return b'{"truncated":'
            def __enter__(self): return self
            def __exit__(self, *_): return False
        with patch("urllib.request.OpenerDirector.open", return_value=Response()):
            with self.assertRaises(PollError):
                client.get("/a/accounts/self")


if __name__ == "__main__":
    unittest.main()
