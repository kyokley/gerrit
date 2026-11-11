"""Bounded, authenticated Gerrit REST operations."""
from __future__ import annotations

import hashlib
import json
import re
import urllib.error
import urllib.parse
import urllib.request


class ReviewError(RuntimeError):
    pass


_LIMIT = 2 * 1024 * 1024


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def task_branch(title):
    ascii_title = title.encode("ascii", "ignore").decode("ascii").lower()
    slug = re.sub(r"[^a-z0-9]+", "-", ascii_title).strip("-")[:48].strip("-") or "task"
    return f"agents/{slug}-{hashlib.sha256(title.encode('utf-8')).hexdigest()[:8]}"


def chain_url(settings, branch):
    query = f"project:{settings.project} branch:{branch}"
    return settings.http_url.rstrip("/") + "/q/" + urllib.parse.quote(query, safe="")


class GerritClient:
    def __init__(self, context):
        self.context = context
        self.settings = context.settings
        self.base = self.settings.http_url.rstrip("/")
        self.opener = urllib.request.build_opener(_NoRedirect)
        self._account_id = None

    def _request(self, method, path, body=None, allow_not_found=False):
        url = self.base + "/a/" + path.lstrip("/")
        data = None if body is None else json.dumps(body).encode("utf-8")
        req = urllib.request.Request(url, data=data, method=method,
                                     headers={"Authorization": "Basic " + __import__("base64").b64encode((self.settings.username + ":" + self.context.token).encode()).decode(),
                                              "Content-Type": "application/json", "Accept": "application/json"})
        try:
            with self.opener.open(req, timeout=10) as response:
                payload = response.read(_LIMIT + 1)
                if len(payload) > _LIMIT:
                    raise ReviewError("Gerrit response exceeds size limit")
                if not payload:
                    return None
                if payload.startswith(b")]}'"):
                    payload = payload.split(b"\n", 1)[1] if b"\n" in payload else b""
                return json.loads(payload)
        except urllib.error.HTTPError as exc:
            exc.close()
            if exc.code in (301, 302, 303, 307, 308):
                raise ReviewError("Gerrit redirect refused") from None
            if exc.code == 404 and allow_not_found:
                return None
            raise ReviewError(f"Gerrit request failed ({exc.code})") from None
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, OSError):
            raise ReviewError("Gerrit request failed") from None

    @staticmethod
    def _segment(value):
        if not isinstance(value, str) or not value or "\x00" in value:
            raise ReviewError("invalid Gerrit identifier")
        return urllib.parse.quote(value, safe="")

    def verify_identity(self):
        account = self._request("GET", "accounts/self/detail")
        if not isinstance(account, dict):
            raise ReviewError("Gerrit identity unavailable")
        expected = self.settings.username
        if account.get("username") != expected:
            raise ReviewError("authenticated Gerrit account does not match configured username")
        self._account_id = account.get("_account_id")
        return account

    def ssh_keys(self):
        """Return public SSH-key records for binding the selected managed key."""
        result = self._request("GET", "accounts/self/sshkeys")
        if not isinstance(result, list):
            raise ReviewError("Gerrit SSH keys unavailable")
        return result

    def ensure_branch(self, branch):
        project = self._segment(self.settings.project)
        ref = "refs/heads/" + branch
        ref_encoded = self._segment(ref)
        base = self._request("GET", f"projects/{project}/branches/{self._segment('refs/heads/' + self.settings.base_branch)}")
        if not isinstance(base, dict) or not base.get("revision"):
            raise ReviewError("base branch revision unavailable")
        current = self._request("GET", f"projects/{project}/branches/{ref_encoded}", allow_not_found=True)
        if current is not None:
            if current.get("revision") != base["revision"]:
                raise ReviewError("generated branch already exists at a different revision")
            return base["revision"]
        try:
            self._request("PUT", f"projects/{project}/branches/{ref_encoded}", {"revision": base["revision"]})
        except ReviewError:
            # Write outcome is uncertain: one readback, never repeat create.
            current = self._request("GET", f"projects/{project}/branches/{ref_encoded}", allow_not_found=True)
            if isinstance(current, dict) and current.get("revision") == base["revision"]:
                return base["revision"]
            raise ReviewError("branch creation outcome requires reconciliation") from None
        current = self._request("GET", f"projects/{project}/branches/{ref_encoded}", allow_not_found=True)
        if not isinstance(current, dict) or current.get("revision") != base["revision"]:
            raise ReviewError("created branch failed revision readback")
        return base["revision"]

    def read_change(self, change_id, branch):
        query = f"project:{self.settings.project} branch:{branch} change:{change_id}"
        path = "changes/?q=" + urllib.parse.quote(query, safe="") + "&o=CURRENT_REVISION&o=CURRENT_COMMIT"
        result = self._request("GET", path)
        if not isinstance(result, list):
            raise ReviewError("Gerrit change not found")
        matching = [c for c in result if c.get("project") == self.settings.project and c.get("branch") == branch and c.get("change_id") == change_id]
        if len(matching) != 1:
            raise ReviewError("Gerrit change mapping is ambiguous or out of scope")
        change = matching[0]
        current = change.get("current_revision")
        if not current or not isinstance(change.get("revisions"), dict):
            raise ReviewError("Gerrit change lacks revision data")
        rev = change["revisions"].get(current)
        if not isinstance(rev, dict) or not isinstance(rev.get("commit"), dict):
            raise ReviewError("Gerrit change lacks commit data")
        return change

    def feedback(self, changes):
        if self._account_id is None:
            self.verify_identity()
        collected = []
        for item in changes:
            change_id = item if isinstance(item, str) else item.get("change_id") or item.get("id")
            branch = item.get("branch") if isinstance(item, dict) else None
            if not change_id or not branch:
                continue
            change = self.read_change(change_id, branch)
            number = change.get("_number")
            comments = self._request("GET", f"changes/{self._segment(str(number))}/comments") or {}
            messages = self._request("GET", f"changes/{self._segment(str(number))}/messages") or []
            checks = self._request("GET", f"changes/{self._segment(str(number))}/checks/", allow_not_found=True)
            metadata = {"branch": branch, "current_revision": change["current_revision"]}
            supported = checks is not None
            collected.append({"id": f"checks:{change_id}:{change['current_revision']}", "kind": "checks",
                              "change_id": change_id, **metadata, "supported": supported,
                              "evidence": checks if supported else None})
            for comment_file, entries in comments.items() if isinstance(comments, dict) else ():
                for c in entries:
                    author = c.get("author", {})
                    if self._account_id is not None and author.get("_account_id") == self._account_id:
                        continue
                    collected.append({"id": c.get("id") or hashlib.sha256(json.dumps(c, sort_keys=True).encode()).hexdigest(), "kind": "comment", "change_id": change_id, **metadata, "file": comment_file, "text": c.get("message", "")})
            for m in messages if isinstance(messages, list) else ():
                author = m.get("author", {})
                if self._account_id is not None and author.get("_account_id") == self._account_id:
                    continue
                collected.append({"id": m.get("id") or hashlib.sha256(json.dumps(m, sort_keys=True).encode()).hexdigest(), "kind": "message", "change_id": change_id, **metadata, "text": m.get("message", "")})
            if supported:
                values = checks.values() if isinstance(checks, dict) else checks if isinstance(checks, list) else ()
                for c in values:
                    collected.append({"id": c.get("id") or hashlib.sha256(json.dumps(c, sort_keys=True).encode()).hexdigest(), "kind": "check", "change_id": change_id, **metadata, "supported": True, "data": c})
        return collected

    def verify_revision(self, change_id, branch, revision):
        change = self.read_change(change_id, branch)
        if change.get("current_revision") != revision:
            raise ReviewError("Gerrit current revision changed")
        return change

    def post_review(self, change_id, branch, revision, message):
        if not isinstance(message, str) or not message.strip() or len(message.encode("utf-8")) > 16_384:
            raise ReviewError("review message is empty or too large")
        change = self.verify_revision(change_id, branch, revision)
        path = f"changes/{self._segment(str(change['_number']))}/revisions/{self._segment(revision)}/review"
        try:
            result = self._request("POST", path, {"message": message})
        except ReviewError:
            raise ReviewError("review write has no confirmed outcome; reconcile before retry") from None
        if result is None:
            raise ReviewError("review write has no confirmed outcome; reconcile before retry")
        return result
