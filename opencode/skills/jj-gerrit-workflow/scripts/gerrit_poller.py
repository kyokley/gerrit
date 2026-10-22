#!/usr/bin/env python3
"""Durable, read-only Gerrit feedback poller."""

import argparse
import base64
import contextlib
import fcntl
import hashlib
import json
import os
import re
import sqlite3
import stat
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path


class PollError(Exception):
    """Sanitized operational error safe to print."""


CONFIG_KEYS = {"rest_base_url", "username", "expected_account_id", "project", "branch", "token_file"}


def secure_read(path):
    """Read an owner-only regular file without following symlinks."""
    try:
        fd = os.open(os.fspath(path), os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0))
        try:
            info = os.fstat(fd)
            if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) & ~0o600:
                raise PollError("protected file must be an owner-owned regular file with mode 0600 or stricter")
            chunks = []
            while True:
                block = os.read(fd, 65536)
                if not block:
                    break
                chunks.append(block)
                if sum(map(len, chunks)) > 1_048_576:
                    raise PollError("protected file is too large")
            return b"".join(chunks)
        finally:
            os.close(fd)
    except PollError:
        raise
    except OSError as exc:
        raise PollError("cannot securely read protected file") from None


def _loopback(host):
    import ipaddress
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def load_config(path, allow_http_loopback=False):
    try:
        raw = secure_read(path)
        config = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise PollError("configuration is not valid UTF-8 JSON") from None
    if not isinstance(config, dict) or set(config) != CONFIG_KEYS:
        raise PollError("configuration must contain exactly the documented fields")
    url = config.get("rest_base_url")
    if not isinstance(url, str) or any(ord(ch) < 0x20 or ord(ch) == 0x7f for ch in url):
        raise PollError("REST base URL is invalid")
    try:
        parsed = urllib.parse.urlsplit(url)
        hostname = parsed.hostname
    except ValueError:
        raise PollError("REST base URL is invalid") from None
    if (parsed.scheme != "https" and not (allow_http_loopback and parsed.scheme == "http" and _loopback(hostname or ""))):
        raise PollError("REST base URL must use HTTPS (HTTP requires explicit loopback permission)")
    if not hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise PollError("REST base URL is invalid")
    username = config["username"]
    if not isinstance(username, str) or not username or ":" in username or any(ord(ch) < 0x20 or ord(ch) == 0x7f for ch in username):
        raise PollError("username is invalid")
    if not isinstance(config["project"], str) or not config["project"] or not isinstance(config["branch"], str) or not config["branch"] or any(ord(ch) < 0x20 or ord(ch) == 0x7f for ch in config["project"] + config["branch"]):
        raise PollError("project and branch must be non-empty strings")
    if type(config["expected_account_id"]) is not int or config["expected_account_id"] <= 0:
        raise PollError("expected_account_id must be a positive integer")
    if not isinstance(config["token_file"], str) or not config["token_file"]:
        raise PollError("token_file must be a path")
    config["rest_base_url"] = url.rstrip("/") + "/"
    return config


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise PollError("Gerrit redirect rejected")


class RestClient:
    def __init__(self, config, token_file, allow_http_loopback=False, timeout=15):
        self.config = config
        self.token_file = token_file
        self.timeout = timeout
        try:
            parsed = urllib.parse.urlsplit(config["rest_base_url"])
            hostname = parsed.hostname
        except ValueError:
            raise PollError("REST base URL is invalid") from None
        if parsed.scheme != "https" and not (allow_http_loopback and parsed.scheme == "http" and _loopback(hostname or "")):
            raise PollError("REST base URL must use HTTPS (HTTP requires explicit loopback permission)")
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect())

    def get(self, path):
        try:
            token = secure_read(self.token_file).decode("utf-8").strip()
        except UnicodeDecodeError:
            raise PollError("token file is malformed UTF-8") from None
        if not token or "\n" in token or "\r" in token:
            raise PollError("token file is empty or malformed")
        if any(ord(ch) < 0x20 or ord(ch) == 0x7f for ch in token):
            raise PollError("token file is empty or malformed")
        auth = base64.b64encode((self.config["username"] + ":" + token).encode()).decode("ascii")
        url = urllib.parse.urljoin(self.config["rest_base_url"], path.lstrip("/"))
        request = urllib.request.Request(url, headers={"Authorization": "Basic " + auth, "Accept": "application/json"})
        try:
            with self.opener.open(request, timeout=self.timeout) as response:
                if response.status < 200 or response.status >= 300:
                    raise PollError("Gerrit request failed (HTTP %d)" % response.status)
                body = response.read(8_388_609)
            if len(body) > 8_388_608:
                raise PollError("Gerrit response exceeds size limit")
            if body.startswith(b")]}'\n"):
                body = body[5:]
            value = json.loads(body)
            if not isinstance(value, (dict, list)):
                raise PollError("Gerrit response has an invalid shape")
            return value
        except PollError:
            raise
        except urllib.error.HTTPError as exc:
            raise PollError("Gerrit request failed (HTTP %d)" % exc.code) from None
        except urllib.error.URLError:
            raise PollError("Gerrit request failed (network or TLS error)") from None
        except (TimeoutError, OSError):
            raise PollError("Gerrit request failed (network or TLS error)") from None
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise PollError("Gerrit response is malformed JSON") from None


class GerritPoller:
    def __init__(self, config_path, db_path, interval=300, allow_http_loopback=False, client=None, monotonic=time.monotonic, acquire_lock=True):
        if type(interval) is not int or interval < 1:
            raise PollError("interval must be a positive integer")
        self.config_path = Path(config_path)
        self.db_path = Path(db_path)
        self.interval = interval
        self.monotonic = monotonic
        self.config = load_config(self.config_path, allow_http_loopback)
        self.client = client or RestClient(self.config, self.config["token_file"], allow_http_loopback)
        self._last_complete = {}
        self._last_error = None
        self.db_path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        self._check_dir(self.db_path.parent)
        self.lock_fd = None
        self.lock_path = Path(str(self.db_path) + ".lock")
        if acquire_lock:
            try:
                self.lock_fd = os.open(self.lock_path, os.O_CREAT | os.O_RDWR | getattr(os, "O_NOFOLLOW", 0), 0o600)
                lock_info = os.fstat(self.lock_fd)
                if not stat.S_ISREG(lock_info.st_mode) or lock_info.st_uid != os.getuid() or stat.S_IMODE(lock_info.st_mode) & ~0o600:
                    raise PollError("lock file must be owner-owned with mode 0600 or stricter")
                fcntl.flock(self.lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except PollError:
                os.close(self.lock_fd)
                self.lock_fd = None
                raise
            except (OSError, BlockingIOError):
                if self.lock_fd is not None:
                    os.close(self.lock_fd)
                    self.lock_fd = None
                raise PollError("another poller holds the database lock") from None
        if self.db_path.exists() or self.db_path.is_symlink():
            try:
                info = os.stat(self.db_path, follow_symlinks=False)
                if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) & ~0o600:
                    self.close()
                    raise PollError("database must be an owner-owned regular file with mode 0600 or stricter")
            except OSError:
                self.close()
                raise PollError("cannot securely inspect poller database") from None
        for suffix in ("-journal", "-wal", "-shm"):
            sidecar = Path(str(self.db_path) + suffix)
            if sidecar.exists() or sidecar.is_symlink():
                try:
                    info = os.stat(sidecar, follow_symlinks=False)
                    if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) & ~0o600:
                        self.close()
                        raise PollError("SQLite sidecar must be an owner-owned regular file with mode 0600 or stricter")
                except OSError:
                    self.close()
                    raise PollError("cannot securely inspect SQLite sidecar") from None
        old_umask = os.umask(0o077)
        try:
            self.db = sqlite3.connect(self.db_path, timeout=5)
            os.chmod(self.db_path, 0o600)
        except (OSError, sqlite3.Error):
            self.close()
            raise PollError("cannot open poller database") from None
        finally:
            os.umask(old_umask)
        self.db.row_factory = sqlite3.Row
        try:
            self._schema()
        except Exception:
            self.close()
            raise

    @staticmethod
    def _check_dir(path):
        info = os.stat(path, follow_symlinks=False)
        if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o700:
            raise PollError("database directory must be owner-owned with mode 0700")

    def _schema(self):
        binding = hashlib.sha256(json.dumps(self.config, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        with self._secure_umask():
            self.db.executescript("""
            CREATE TABLE IF NOT EXISTS metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS enrolled (change_number INTEGER PRIMARY KEY, coverage REAL NOT NULL DEFAULT 0);
            CREATE TABLE IF NOT EXISTS events (
                event_id INTEGER PRIMARY KEY AUTOINCREMENT, change_number INTEGER NOT NULL,
                fingerprint TEXT NOT NULL UNIQUE, kind TEXT NOT NULL, payload TEXT NOT NULL,
                created_at TEXT NOT NULL, acknowledged INTEGER NOT NULL DEFAULT 0
            );
            """)
            row = self.db.execute("SELECT value FROM metadata WHERE key='binding'").fetchone()
            if row and row[0] != binding:
                raise PollError("database scope/config binding differs; use a new database for this scope")
            self.db.execute("INSERT OR IGNORE INTO metadata(key,value) VALUES('binding',?)", (binding,))
            self.db.commit()
        for suffix in ("-journal", "-wal", "-shm"):
            sidecar = Path(str(self.db_path) + suffix)
            if sidecar.exists():
                os.chmod(sidecar, 0o600)

    def enroll(self, number):
        if type(number) is not int or number <= 0:
            raise PollError("change number must be a positive integer")
        with self._secure_umask():
            self.db.execute("INSERT OR IGNORE INTO enrolled(change_number,coverage) VALUES(?,0)", (number,))
            self.db.commit()

    @contextlib.contextmanager
    def _secure_umask(self):
        old_umask = os.umask(0o077)
        try:
            yield
        finally:
            os.umask(old_umask)

    def _change_path(self, number, suffix):
        return "/a/changes/" + str(number) + "/" + suffix

    @staticmethod
    def _event_fingerprint(payload):
        data = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(data.encode()).hexdigest()

    def _collect(self, number):
        detail = self.client.get(self._change_path(number, "detail?o=MESSAGES&o=CURRENT_REVISION"))
        if (not isinstance(detail, dict) or type(detail.get("_number")) is not int or detail.get("_number") != number
                or detail.get("project") != self.config["project"] or detail.get("branch") != self.config["branch"]):
            raise PollError("change scope does not match configured project and branch")
        comments = self.client.get(self._change_path(number, "comments"))
        if not isinstance(comments, dict) or not isinstance(detail.get("messages"), list):
            raise PollError("Gerrit response has an invalid shape")
        events = []
        for msg in detail["messages"]:
            if not isinstance(msg, dict) or not isinstance(msg.get("message"), str):
                raise PollError("Gerrit message response is malformed")
            author = msg.get("author") if isinstance(msg.get("author"), dict) else {}
            revision = msg.get("_revision_number")
            payload = {"change_number": number, "kind": "message", "id": msg.get("id"), "message": msg["message"], "author": author, "revision": revision}
            fp = self._event_fingerprint(payload)
            events.append((fp, "message", payload))
        for path, entries in comments.items():
            if not isinstance(path, str) or not isinstance(entries, list):
                raise PollError("Gerrit comment response is malformed")
            for comment in entries:
                if not isinstance(comment, dict) or not isinstance(comment.get("message"), str):
                    raise PollError("Gerrit comment response is malformed")
                author = comment.get("author") if isinstance(comment.get("author"), dict) else {}
                patch_set = comment.get("patch_set")
                payload = {
                    "change_number": number, "kind": "inline_comment", "id": comment.get("id"),
                    "path": path, "message": comment["message"], "author": author,
                    "patch_set": patch_set, "in_reply_to": comment.get("in_reply_to"),
                    "unresolved": comment.get("unresolved"), "updated": comment.get("updated"),
                    "side": comment.get("side"), "line": comment.get("line"),
                    "range": comment.get("range"), "parent": comment.get("parent"),
                    "tag": comment.get("tag"),
                }
                fp = self._event_fingerprint(payload)
                events.append((fp, "inline_comment", payload))
        return events

    def poll(self):
        try:
            identity = self.client.get("/a/accounts/self")
            if not isinstance(identity, dict) or type(identity.get("_account_id")) is not int or identity.get("_account_id") != self.config["expected_account_id"]:
                raise PollError("Gerrit authenticated identity does not match configured account")
            numbers = [row[0] for row in self.db.execute("SELECT change_number FROM enrolled ORDER BY change_number")]
            if not numbers:
                raise PollError("no changes enrolled")
            events = []
            for number in numbers:
                events.extend((number, *event) for event in self._collect(number))
            now = self.monotonic()
            credits = {}
            for number in numbers:
                previous = self._last_complete.get(number)
                elapsed = now - previous if previous is not None else 0
                credits[number] = elapsed if 0 <= elapsed <= 2 * self.interval else 0
            self._store_poll(events, credits)
            self._last_complete.update({number: now for number in numbers})
            self._last_error = None
            return self.status()
        except Exception as exc:
            self._last_complete.clear()
            self._last_error = exc if isinstance(exc, PollError) else PollError("poll failed safely")
            try:
                self._record_error(str(self._last_error))
            except sqlite3.Error:
                pass
            raise self._last_error

    def _store_poll(self, events, credits):
        try:
            with self._secure_umask(), self.db:
                self.db.executemany("UPDATE enrolled SET coverage=coverage+? WHERE change_number=?", ((credit, number) for number, credit in credits.items() if credit))
                for number, fingerprint, kind, payload in events:
                    self.db.execute("INSERT OR IGNORE INTO events(change_number,fingerprint,kind,payload,created_at) VALUES(?,?,?,?,?)", (number, fingerprint, kind, json.dumps(payload, ensure_ascii=False, sort_keys=True), time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())))
                self.db.execute("INSERT OR REPLACE INTO metadata(key,value) VALUES('last_success_utc',?)", (time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),))
                self.db.execute("INSERT OR REPLACE INTO metadata(key,value) VALUES('last_error','')")
        except sqlite3.Error:
            raise PollError("poll transaction failed; no coverage credited") from None

    def _record_error(self, message):
        with self._secure_umask():
            self.db.execute("INSERT OR REPLACE INTO metadata(key,value) VALUES('last_error',?)", (message,))
            self.db.commit()

    def pending_events(self):
        return [dict(row, payload=json.loads(row["payload"])) for row in self.db.execute("SELECT event_id AS event_id,change_number,fingerprint,kind,payload,created_at,acknowledged FROM events WHERE acknowledged=0 ORDER BY event_id")]

    def ack(self, event_id):
        if type(event_id) is not int or event_id <= 0:
            raise PollError("event id must be a positive integer")
        with self._secure_umask():
            cursor = self.db.execute("UPDATE events SET acknowledged=1 WHERE event_id=?", (event_id,))
            self.db.commit()
        if cursor.rowcount != 1:
            raise PollError("event id not found")

    def status(self):
        changes = [dict(change_number=row[0], coverage_seconds=int(row[1])) for row in self.db.execute("SELECT change_number,coverage FROM enrolled ORDER BY change_number")]
        pending = self.db.execute("SELECT count(*) FROM events WHERE acknowledged=0").fetchone()[0]
        last = self.db.execute("SELECT value FROM metadata WHERE key='last_success_utc'").fetchone()
        error = self.db.execute("SELECT value FROM metadata WHERE key='last_error'").fetchone()
        return {"changes": changes, "required_coverage_seconds": 43200, "complete": bool(changes) and all(c["coverage_seconds"] >= 43200 for c in changes), "pending_events": pending, "last_success_utc": last[0] if last else None, "last_error": (error[0] or None) if error else None}

    def close(self):
        if getattr(self, "db", None):
            self.db.close()
            self.db = None
        if getattr(self, "lock_fd", None) is not None:
            try:
                fcntl.flock(self.lock_fd, fcntl.LOCK_UN)
                os.close(self.lock_fd)
            except OSError:
                pass
            self.lock_fd = None


def verify_identity(config, client=None):
    """Verify the authenticated Gerrit account without opening a database."""
    client = client or RestClient(config, config["token_file"])
    identity = client.get("/a/accounts/self")
    account_id = identity.get("_account_id") if isinstance(identity, dict) else None
    username = identity.get("username") if isinstance(identity, dict) else None
    if (type(account_id) is not int or account_id != config["expected_account_id"]
            or not isinstance(username, str) or username != config["username"]):
        raise PollError("Gerrit authenticated identity does not match configured account")
    return {"verified": True, "account_id": account_id, "username": username}


def _main(argv=None):
    parser = argparse.ArgumentParser(description="Read-only Gerrit feedback poller")
    parser.add_argument("--config", required=True)
    parser.add_argument("--db")
    parser.add_argument("--interval", type=int, default=300)
    parser.add_argument("--allow-http-loopback", action="store_true")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("verify")
    enroll = sub.add_parser("enroll"); enroll.add_argument("changes", nargs="+", type=int)
    sub.add_parser("poll").add_argument("--once", action="store_true")
    sub.add_parser("status")
    sub.add_parser("events")
    sub.add_parser("ack").add_argument("event_id", type=int)
    args = parser.parse_args(argv)
    poller = None
    try:
        if args.command == "verify":
            config = load_config(args.config, args.allow_http_loopback)
            result = verify_identity(config, RestClient(config, config["token_file"], args.allow_http_loopback))
            print(json.dumps(result, sort_keys=True))
            return 0
        if not args.db:
            parser.error("--db is required for this command")
        poller = GerritPoller(args.config, args.db, args.interval, args.allow_http_loopback, acquire_lock=args.command == "poll")
        if args.command == "enroll":
            for number in args.changes:
                poller.enroll(number)
            output = poller.status()
        elif args.command == "status":
            output = poller.status()
        elif args.command == "events":
            output = poller.pending_events()
        elif args.command == "ack":
            poller.ack(args.event_id); output = poller.status()
        else:
            while True:
                output = poller.poll()
                print(json.dumps(output, sort_keys=True), flush=True)
                if output["complete"] or args.once:
                    return 0
                time.sleep(args.interval)
        print(json.dumps(output, sort_keys=True))
        return 0
    except PollError as exc:
        print(json.dumps({"error": str(exc)}, sort_keys=True), file=sys.stderr)
        return 1
    finally:
        if poller:
            poller.close()


if __name__ == "__main__":
    raise SystemExit(_main())
