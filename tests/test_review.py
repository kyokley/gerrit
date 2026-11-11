import json
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer

from gerrit.review import GerritClient, task_branch, chain_url
from gerrit.settings import Settings


class Handler(BaseHTTPRequestHandler):
    records = []
    branches = {}
    post_status = 200
    identity = {"_account_id": 1, "username": "bot", "name": "Bot Display Name"}
    change = {"_number": 7, "project": "proj", "branch": "agents/x", "change_id": "Iabc",
              "current_revision": "r1", "revisions": {"r1": {"_number": 1, "commit": {"commit": "r1"}}}}

    def do_GET(self):
        self.records.append(("GET", self.path, None))
        if self.path.endswith("/detail"):
            self.respond(self.identity)
        elif "/changes/?q=" in self.path:
            self.respond([self.change])
        elif "/comments" in self.path:
            self.respond({"f.txt": [
                {"id": "self", "author": {"_account_id": 1}, "message": "own"},
                {"id": "other", "author": {"_account_id": 2}, "message": "reviewer feedback"}]})
        elif "/messages" in self.path:
            self.respond([
                {"id": "self-msg", "author": {"_account_id": 1}, "message": "own"},
                {"id": "other-msg", "author": {"_account_id": 2}, "message": "reviewer message"}])
        elif "/checks/" in self.path:
            self.send_error(404)
        elif "/branches/" in self.path:
            name = self.path.rsplit("/branches/", 1)[1]
            if name.endswith("refs%2Fheads%2Fmain"):
                self.respond({"revision": "base1"})
            elif name in self.branches:
                self.respond({"revision": self.branches[name]})
            else:
                self.send_error(404)
        elif "/changes/" in self.path and "/revisions/" not in self.path:
            self.respond(self.change)
        else:
            self.send_error(404)

    def do_POST(self):
        body = self.rfile.read(int(self.headers.get("Content-Length", 0)))
        self.records.append(("POST", self.path, json.loads(body or b"{}")))
        if self.post_status == 404:
            self.send_error(404)
            return
        self.respond({})

    def do_PUT(self):
        body = self.rfile.read(int(self.headers.get("Content-Length", 0)))
        name = self.path.rsplit("/branches/", 1)[1]
        self.records.append(("PUT", self.path, json.loads(body or b"{}")))
        self.branches[name] = json.loads(body)["revision"]
        self.respond({"revision": self.branches[name]})

    def respond(self, obj, status=200):
        data = b")]}\'\n" + json.dumps(obj).encode()
        self.send_response(status); self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data))); self.end_headers(); self.wfile.write(data)

    def log_message(self, *args):
        pass


class ReviewTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = HTTPServer(("127.0.0.1", 0), Handler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True); cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown(); cls.thread.join(); cls.server.server_close()

    def setUp(self):
        Handler.records = []
        Handler.branches = {}
        Handler.post_status = 200
        Handler.identity = {"_account_id": 1, "username": "bot", "name": "Bot Display Name"}
        self.settings = Settings(repository="/tmp", http_url=f"http://127.0.0.1:{self.server.server_port}/gerrit",
                                  username="bot", project="proj", base_branch="main")
        self.client = GerritClient(type("Ctx", (), {"settings": self.settings, "token": "t"})())

    def test_paths_and_identity(self):
        self.assertEqual(self.client.verify_identity()["username"], "bot")
        self.assertIn("/gerrit/a/accounts/self/detail", Handler.records[0][1])

    def test_display_name_never_substitutes_for_username(self):
        Handler.identity = {"_account_id": 1, "username": "different", "name": "bot"}
        with self.assertRaisesRegex(Exception, "does not match"):
            self.client.verify_identity()

    def test_branch_helpers(self):
        self.assertRegex(task_branch("Hello world"), r"^agents/hello-world-[0-9a-f]{8}$")
        self.assertIn("branch%3Aagents%2Fx", chain_url(self.settings, "agents/x"))

    def test_change_feedback_and_scoped_review(self):
        change = self.client.read_change("Iabc", "agents/x")
        self.assertEqual(change["current_revision"], "r1")
        self.client.post_review("Iabc", "agents/x", "r1", "Looks good")
        post = [r for r in Handler.records if r[0] == "POST"][-1]
        self.assertTrue(post[1].endswith("/revisions/r1/review"))
        self.assertEqual(post[2], {"message": "Looks good"})

    def test_feedback_has_revision_branch_and_self_account_filter(self):
        items = self.client.feedback([{"change_id": "Iabc", "branch": "agents/x"}])
        self.assertEqual([x["id"] for x in items if x["kind"] in ("comment", "message")],
                         ["other", "other-msg"])
        self.assertTrue(all(x["branch"] == "agents/x" and x["current_revision"] == "r1" for x in items))
        checks = next(x for x in items if x["kind"] == "checks")
        self.assertFalse(checks["supported"])

    def test_post_review_404_is_not_a_confirmed_write(self):
        Handler.post_status = 404
        with self.assertRaisesRegex(Exception, "no confirmed outcome"):
            self.client.post_review("Iabc", "agents/x", "r1", "Looks good")

    def test_branch_create_uses_base_and_never_moves_existing(self):
        branch = "agents/new"
        self.assertEqual(self.client.ensure_branch(branch), "base1")
        self.assertEqual(self.client.ensure_branch(branch), "base1")
        self.assertEqual(len([r for r in Handler.records if r[0] == "PUT"]), 1)
        Handler.branches[next(iter(Handler.branches))] = "other"
        with self.assertRaises(Exception):
            self.client.ensure_branch(branch)


if __name__ == "__main__":
    unittest.main()
