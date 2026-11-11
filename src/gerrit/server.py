"""Loopback-only HTTP dashboard and supervisor lifecycle."""

from __future__ import annotations

import json
import secrets
import signal
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib import resources
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

MAX_BODY = 64 * 1024
CONNECTION_TIMEOUT = 10
STATIC = {"/": ("index.html", "text/html; charset=utf-8"),
          "/app.css": ("app.css", "text/css; charset=utf-8"),
          "/app.js": ("app.js", "text/javascript; charset=utf-8")}


def _load_settings(path: Path) -> Any:
    from .settings import Settings, load_settings

    return load_settings(path) if path.exists() else Settings()


def _settings_dict(settings: Any) -> dict[str, Any]:
    return settings.to_dict()


class _DashboardServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, address: tuple[str, int], handler: type[BaseHTTPRequestHandler],
                 settings_path: Path, state_dir: Path):
        super().__init__(address, handler)
        self.settings_path = settings_path
        self.state_dir = state_dir
        self.csrf_token = secrets.token_urlsafe(32)
        self.settings_lock = threading.RLock()
        self.settings = _load_settings(settings_path)
        self.settings_version = 0
        self.supervisor = None

    def get_request(self):
        connection, address = super().get_request()
        connection.settimeout(CONNECTION_TIMEOUT)
        return connection, address

    def handle_error(self, request: Any, client_address: Any) -> None:
        # Partial headers and disconnected clients must not emit diagnostics.
        return

    def get_supervisor(self):
        with self.settings_lock:
            if self.supervisor is None:
                from .runtime import Supervisor

                self.supervisor = Supervisor(self.settings, self.state_dir)
            return self.supervisor


class Handler(BaseHTTPRequestHandler):
    server: _DashboardServer

    def log_message(self, format: str, *args: Any) -> None:
        return

    def _headers(self) -> None:
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Content-Security-Policy", "default-src 'self'; object-src 'none'; frame-ancestors 'none'; base-uri 'none'")
        self.send_header("Cache-Control", "no-store")

    def _send(self, code: int, data: bytes, content_type: str = "application/json; charset=utf-8") -> None:
        self.send_response(code)
        self._headers()
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _json(self, code: int, value: Any) -> None:
        self._send(code, json.dumps(value, ensure_ascii=False).encode("utf-8"))

    def _error(self, code: int, message: str) -> None:
        self._json(code, {"error": message})

    def _origin_host(self, value: str, *, origin: bool) -> bool:
        try:
            if any(char in value for char in "\\ \t\r\n"):
                return False
            if not origin and any(char in value for char in "/?#"):
                return False
            parsed = urlsplit(value if origin else "http://" + value)
            if parsed.path or parsed.query or parsed.fragment:
                return False
            if origin and parsed.scheme != "http":
                return False
            if parsed.username is not None or parsed.password is not None:
                return False
            host = parsed.hostname
            port = parsed.port
            effective_port = port if port is not None else (80 if origin else 80)
            return host == "127.0.0.1" and effective_port == self.server.server_port
        except (ValueError, TypeError):
            return False

    def _check_request(self, mutation: bool = False) -> bool:
        host = self.headers.get_all("Host", [])
        if len(host) != 1 or not self._origin_host(host[0], origin=False):
            self._error(400, "Invalid Host")
            return False
        origins = self.headers.get_all("Origin", [])
        if len(origins) > 1 or (origins and not self._origin_host(origins[0], origin=True)):
            self._error(403, "Origin not allowed")
            return False
        if mutation:
            if not origins:
                self._error(403, "Origin required")
                return False
            if not secrets.compare_digest(self.headers.get("X-CSRF-Token", ""), self.server.csrf_token):
                self._error(403, "Invalid CSRF token")
                return False
        return True

    def _body(self) -> dict[str, Any] | None:
        content_types = self.headers.get_all("Content-Type", [])
        if len(content_types) != 1 or content_types[0].strip().lower() not in (
            "application/json", "application/json; charset=utf-8", "application/json; charset=\"utf-8\""
        ):
            self._error(415, "Content-Type must be application/json")
            return None
        if self.headers.get("Transfer-Encoding") is not None:
            self._error(400, "Transfer-Encoding is not supported")
            return None
        lengths = self.headers.get_all("Content-Length", [])
        try:
            if len(lengths) != 1 or not lengths[0].isdigit():
                raise ValueError
            length = int(lengths[0])
        except ValueError:
            self._error(400, "Invalid Content-Length")
            return None
        if length > MAX_BODY:
            self._error(413, "Request body too large")
            return None
        try:
            raw = self.rfile.read(length)
            value = json.loads(raw)
            if len(raw) != length or not isinstance(value, dict):
                raise ValueError
            return value
        except TimeoutError:
            self.close_connection = True
            self._error(408, "Request body timed out")
            return None
        except (UnicodeDecodeError, json.JSONDecodeError, ValueError):
            self._error(400, "Invalid JSON body")
            return None

    def do_GET(self) -> None:
        if not self._check_request():
            return
        path = urlsplit(self.path).path
        if path == "/api/settings":
            with self.server.settings_lock:
                self._json(200, _settings_dict(self.server.settings))
            return
        if path == "/api/state":
            try:
                with self.server.settings_lock:
                    supervisor = self.server.get_supervisor()
                    snapshot = supervisor.snapshot()
                    snapshot["csrf_token"] = self.server.csrf_token
                    snapshot["settings"] = _settings_dict(self.server.settings)
                    snapshot["settings_version"] = self.server.settings_version
                self._json(200, snapshot)
            except Exception:
                self._error(503, "Supervisor unavailable")
            return
        asset = STATIC.get(path)
        if asset:
            try:
                payload = resources.files("gerrit").joinpath("static", asset[0]).read_bytes()
            except (FileNotFoundError, ModuleNotFoundError, OSError):
                self._error(404, "Not found")
                return
            self._send(200, payload, asset[1])
            return
        self._error(404, "Not found")

    def do_POST(self) -> None:
        if not self._check_request(mutation=True):
            return
        body = self._body()
        if body is None:
            return
        path = urlsplit(self.path).path
        try:
            if path == "/api/settings":
                from .settings import Settings, save_settings

                settings = Settings.from_dict(body)
                settings.validate(live=False)
                with self.server.settings_lock:
                    repository = settings.to_dict().get("repository")
                    if repository:
                        repo_path = Path(repository).resolve()
                        state_path = self.server.state_dir.resolve()
                        if state_path == repo_path or repo_path in state_path.parents:
                            raise ValueError("state_dir must be outside the configured repository")
                    save_settings(self.server.settings_path, settings)
                    self.server.settings = settings
                    self.server.settings_version += 1
                    if self.server.supervisor is not None:
                        self.server.supervisor.update_settings(settings)
                self._json(200, _settings_dict(settings))
                return
            if path == "/api/tasks":
                title = body.get("title")
                if not isinstance(title, str) or not title.strip() or len(title) > 500:
                    self._error(400, "Title must be non-empty text of at most 500 characters")
                    return
                with self.server.settings_lock:
                    self.server.settings.validate(live=True)
                    task = self.server.get_supervisor().start(title.strip())
                self._json(201, task)
                return
            if path.startswith("/api/tasks/") and path.endswith("/cancel"):
                task_id = path[len("/api/tasks/"):-len("/cancel")].strip("/")
                if not task_id or "/" in task_id:
                    self._error(404, "Not found")
                    return
                task = self.server.get_supervisor().cancel(task_id)
                self._json(200, task)
                return
            self._error(404, "Not found")
        except (ValueError, FileNotFoundError):
            self._error(400, "Invalid request or settings")
        except Exception:
            self._error(500, "Request could not be completed")

    def _method_not_allowed(self) -> None:
        if self._check_request():
            self._error(405, "Method not allowed")

    do_PUT = _method_not_allowed
    do_PATCH = _method_not_allowed
    do_DELETE = _method_not_allowed
    do_OPTIONS = _method_not_allowed

    def send_error(self, code: int, message: str | None = None, explain: str | None = None) -> None:
        self._error(code, "Bad request" if code < 500 else "Request failed")


def serve(settings_path: str | Path, state_dir: str | Path, port: int = 8765) -> None:
    settings_path, state_dir = Path(settings_path), Path(state_dir)
    if not 0 <= port <= 65535:
        raise ValueError("invalid port")
    server = _DashboardServer(("127.0.0.1", port), Handler, settings_path, state_dir)
    previous_handlers: dict[int, Any] = {}

    def stop(_signum: int, _frame: Any) -> None:
        threading.Thread(target=server.shutdown, daemon=True).start()

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            previous_handlers[sig] = signal.signal(sig, stop)
        except ValueError:
            pass
    try:
        server.serve_forever()
    finally:
        server.server_close()
        if server.supervisor is not None:
            try:
                server.supervisor.close()
            except Exception:
                pass
        for sig, handler in previous_handlers.items():
            signal.signal(sig, handler)
