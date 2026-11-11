import json
import socket
import tempfile
import threading
import time
import types
import unittest
import urllib.error
import urllib.request
from pathlib import Path

from gerrit.server import MAX_BODY, _DashboardServer, Handler


class FakeSettings:
    def __init__(self, values=None):
        self.values = dict(values or {"repository": ""})

    @classmethod
    def from_dict(cls, value):
        if "secret" in value:
            raise ValueError("secret invalid")
        return cls(value)

    def to_dict(self):
        return self.values

    def validate(self, live=False):
        if live and not self.values.get("repository"):
            raise ValueError("missing")


class FakeSupervisor:
    def __init__(self, settings, state_dir):
        self.settings = settings
        self.tasks = []

    def snapshot(self):
        return {"tasks": self.tasks, "max_parallel": 3}

    def start(self, title):
        task = {"id": "t1", "title": title}
        self.tasks.append(task)
        return task

    def cancel(self, task_id):
        return {"id": task_id, "state": "cancelled"}

    def update_settings(self, settings):
        self.settings = settings

    def close(self):
        pass


class ServerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.original_settings = __import__("sys").modules.get("gerrit.settings")
        cls.original_runtime = __import__("sys").modules.get("gerrit.runtime")
        settings_mod = types.ModuleType("gerrit.settings")
        settings_mod.Settings = FakeSettings
        settings_mod.load_settings = lambda path: FakeSettings()
        settings_mod.save_settings = lambda path, value: None
        runtime_mod = types.ModuleType("gerrit.runtime")
        runtime_mod.Supervisor = FakeSupervisor
        __import__("sys").modules["gerrit.settings"] = settings_mod
        __import__("sys").modules["gerrit.runtime"] = runtime_mod

    @classmethod
    def tearDownClass(cls):
        modules = __import__("sys").modules
        for name, original in (("gerrit.settings", cls.original_settings), ("gerrit.runtime", cls.original_runtime)):
            if original is None:
                modules.pop(name, None)
            else:
                modules[name] = original

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.server = _DashboardServer(("127.0.0.1", 0), Handler, Path(self.temp.name) / "settings.json", Path(self.temp.name))
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base = f"http://127.0.0.1:{self.server.server_port}"

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()
        self.temp.cleanup()

    def request(self, path, method="GET", body=None, headers=None):
        data = None if body is None else json.dumps(body).encode()
        req = urllib.request.Request(self.base + path, data=data, method=method, headers=headers or {})
        try:
            response = urllib.request.urlopen(req)
        except urllib.error.HTTPError as err:
            response = err
        code = response.code
        headers_out = response.headers
        payload = response.read()
        response.close()
        return code, json.loads(payload) if "json" in headers_out.get("Content-Type", "") else payload, headers_out

    def mutation_headers(self):
        _, state, _ = self.request("/api/state")
        return {"Origin": self.base, "X-CSRF-Token": state["csrf_token"], "Content-Type": "application/json"}

    def test_state_and_settings_save_then_task_and_cancel(self):
        code, state, _ = self.request("/api/state")
        self.assertEqual(code, 200)
        self.assertIn("csrf_token", state)
        headers = self.mutation_headers()
        self.assertEqual(self.request("/api/settings", "POST", {"repository": "/repo"}, headers)[0], 200)
        _, state_after_save, _ = self.request("/api/state")
        self.assertEqual(state_after_save["settings_version"], 1)
        code, task, _ = self.request("/api/tasks", "POST", {"title": "Task"}, headers)
        self.assertEqual((code, task["title"]), (201, "Task"))
        code, cancelled, _ = self.request("/api/tasks/t1/cancel", "POST", {}, headers)
        self.assertEqual((code, cancelled["state"]), (200, "cancelled"))

    def test_invalid_state_location_does_not_partially_save_settings(self):
        headers = self.mutation_headers()
        code, _, _ = self.request("/api/settings", "POST", {"repository": self.temp.name}, headers)
        self.assertEqual(code, 400)
        _, state, _ = self.request("/api/state")
        self.assertEqual(state["settings_version"], 0)
        self.assertEqual(state["settings"]["repository"], "")

    def test_host_origin_csrf_and_body_limits(self):
        self.assertEqual(self.request("/api/state", headers={"Host": "example.com"})[0], 400)
        malformed_hosts = (
            f"127.0.0.1:{self.server.server_port}/evil",
            f"127.0.0.1:{self.server.server_port}?x=1",
            f"127.0.0.1:{self.server.server_port}#fragment",
        )
        for host in malformed_hosts:
            with self.subTest(host=host):
                self.assertEqual(self.request("/api/state", headers={"Host": host})[0], 400)
        headers = self.mutation_headers()
        headers["Origin"] = "http://localhost:" + str(self.server.server_port)
        self.assertEqual(self.request("/api/settings", "POST", {}, headers)[0], 403)
        headers = self.mutation_headers()
        headers.pop("X-CSRF-Token")
        self.assertEqual(self.request("/api/settings", "POST", {}, headers)[0], 403)
        headers = self.mutation_headers()
        headers["Content-Type"] = "application/json; nonsense"
        self.assertEqual(self.request("/api/settings", "POST", {}, headers)[0], 415)
        headers = self.mutation_headers()
        headers["Content-Length"] = str(MAX_BODY + 1)
        code, _, _ = self.request("/api/settings", "POST", {}, headers)
        self.assertEqual(code, 413)
        self.assertEqual(self.request("/api/state", headers={"Host": "127.0.0.1:80"})[0], 400)

    def test_first_concurrent_state_access_creates_one_supervisor(self):
        runtime_module = __import__("sys").modules["gerrit.runtime"]
        original = runtime_module.Supervisor
        count = 0
        count_lock = threading.Lock()

        class CountingSupervisor(FakeSupervisor):
            def __init__(self, settings, state_dir):
                nonlocal count
                with count_lock:
                    count += 1
                time.sleep(0.02)
                super().__init__(settings, state_dir)

        runtime_module.Supervisor = CountingSupervisor
        barrier = threading.Barrier(8)
        results = []

        def get_first():
            barrier.wait()
            results.append(self.server.get_supervisor())

        workers = [threading.Thread(target=get_first) for _ in range(8)]
        try:
            for worker in workers:
                worker.start()
            for worker in workers:
                worker.join()
        finally:
            runtime_module.Supervisor = original
        self.assertEqual(count, 1)
        self.assertEqual(len({id(item) for item in results}), 1)

    def test_accepted_connection_has_bounded_timeout(self):
        server = _DashboardServer(("127.0.0.1", 0), Handler, Path(self.temp.name) / "other.json", Path(self.temp.name))
        client = socket.create_connection(server.server_address)
        accepted, _ = server.get_request()
        try:
            self.assertEqual(accepted.gettimeout(), 10)
        finally:
            client.close()
            accepted.close()
            server.server_close()

    def test_static_allowlist_method_and_headers(self):
        code, _, headers = self.request("/")
        self.assertEqual(code, 200)
        self.assertEqual(headers.get("X-Content-Type-Options"), "nosniff")
        self.assertEqual(self.request("/../settings.json")[0], 404)
        self.assertEqual(self.request("/app.js")[0], 200)
        self.assertEqual(self.request("/api/state", "PUT")[0], 405)
        self.assertNotIn("Access-Control-Allow-Origin", headers)


if __name__ == "__main__":
    unittest.main()
