"""Validated, non-secret orchestrator settings."""
from __future__ import annotations

import json
import os
import re
import tempfile
import stat
from dataclasses import asdict, dataclass, field
from pathlib import Path
from urllib.parse import urlsplit


@dataclass
class Settings:
    repository: str = ""
    agent_command: list[str] = field(default_factory=lambda: ["opencode", "run", "--standalone", "--format", "json", "{prompt}"])
    max_parallel: int = 3
    max_units: int = 20
    agent_timeout: int = 1800
    http_url: str = ""
    ssh_host: str = ""
    ssh_port: int = 29418
    username: str = ""
    project: str = ""
    base_branch: str = "main"
    known_hosts: str = ""
    credential_mode: str = "token-file"
    token_file: str = ""
    ssh_key_file: str = ""
    spec_file: str = ""
    profile: str = "default"
    scope: str = "gerrit"
    token_env: str = "GERRIT_TOKEN"
    ssh_key_env: str = "GERRIT_SSH_KEY"
    field_env: dict[str, str] = field(default_factory=dict)
    poll_interval: int = 300
    monitor_hours: int = 12

    @classmethod
    def from_dict(cls, data):
        if not isinstance(data, dict):
            raise ValueError("settings must be an object")
        allowed = set(cls.__dataclass_fields__)
        unknown = set(data) - allowed
        if unknown:
            raise ValueError("unknown settings: " + ", ".join(sorted(unknown)))
        try:
            value = cls(**data)
        except TypeError as exc:
            raise ValueError("invalid settings") from exc
        value.validate()
        return value

    def to_dict(self):
        return asdict(self)

    def validate(self, live=False):
        text_fields = ("repository", "http_url", "ssh_host", "username", "project", "base_branch",
                       "known_hosts", "credential_mode", "token_file", "ssh_key_file", "spec_file",
                       "profile", "scope", "token_env", "ssh_key_env")
        if any(not isinstance(getattr(self, name), str) for name in text_fields):
            raise ValueError("text settings must be strings")
        if not isinstance(self.repository, str) or (self.repository and not Path(self.repository).is_absolute()):
            raise ValueError("repository must be an absolute path")
        for name in ("known_hosts", "token_file", "ssh_key_file", "spec_file"):
            value = getattr(self, name)
            if value and not Path(value).is_absolute():
                raise ValueError(f"{name} must be an absolute path")
        if not isinstance(self.agent_command, list) or not self.agent_command or not all(isinstance(x, str) and x for x in self.agent_command):
            raise ValueError("agent_command must be a nonempty argv list")
        if any(type(v) is not int or v <= 0 for v in (self.max_parallel, self.max_units, self.agent_timeout, self.ssh_port, self.poll_interval, self.monitor_hours)):
            raise ValueError("numeric settings must be positive integers")
        if self.ssh_port > 65535 or self.max_parallel > 32 or self.max_units > 500:
            raise ValueError("numeric setting exceeds its safe bound")
        if self.monitor_hours < 12:
            raise ValueError("monitor_hours must be at least 12")
        if self.credential_mode not in ("token-file", "secretspec"):
            raise ValueError("unsupported credential_mode")
        if not isinstance(self.field_env, dict) or any(k not in {"http_url", "ssh_host", "username", "project", "base_branch", "ssh_port", "known_hosts"} or not isinstance(v, str) or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", v) for k, v in self.field_env.items()):
            raise ValueError("field_env must map Gerrit fields to environment variable names")
        if self.token_env == self.ssh_key_env or self.token_env in self.field_env.values() or self.ssh_key_env in self.field_env.values():
            raise ValueError("credential environment names must be distinct and secret-only")
        for env_name in (self.token_env, self.ssh_key_env):
            if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", env_name) or env_name.startswith("GERRIT_SECRET_"):
                raise ValueError("invalid credential environment variable")
        if any(name.startswith("GERRIT_SECRET_") for name in self.field_env.values()):
            raise ValueError("field_env cannot use reserved helper environment names")
        for name in ("ssh_host", "username", "project", "base_branch"):
            value = getattr(self, name)
            if value and (any(ord(c) < 32 or ord(c) == 127 for c in value) or value.startswith("-") or any(c.isspace() for c in value)):
                raise ValueError(f"invalid {name}")
        if self.ssh_host and not re.fullmatch(r"[A-Za-z0-9._:-]+", self.ssh_host):
            raise ValueError("invalid ssh_host")
        if self.http_url:
            u = urlsplit(self.http_url)
            if u.scheme not in ("http", "https") or not u.hostname or u.username or u.password or u.query or u.fragment:
                raise ValueError("http_url must be an HTTP(S) base URL without userinfo/query/fragment")
        if live:
            required = ["http_url", "ssh_host", "username", "project", "base_branch", "known_hosts"]
            if not self.repository or any(not getattr(self, k) and k not in self.field_env for k in required):
                raise ValueError("required Gerrit destination settings are missing")
            if not Path(self.repository).is_dir():
                raise ValueError("repository directory does not exist")
            if self.credential_mode == "token-file" and (not self.token_file or not self.ssh_key_file):
                raise ValueError("token-file mode requires token_file and ssh_key_file")
            if self.credential_mode == "secretspec" and not self.spec_file:
                raise ValueError("secretspec mode requires spec_file")


def load_settings(path):
    try:
        fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        try:
            st = os.fstat(fd)
            if not stat.S_ISREG(st.st_mode) or st.st_size > 1024 * 1024:
                raise ValueError("settings must be a bounded regular file")
            with os.fdopen(fd, "rb", closefd=False) as f:
                raw = f.read(1024 * 1024 + 1)
        finally:
            os.close(fd)
        if len(raw) > 1024 * 1024:
            raise ValueError("settings file exceeds size limit")
        return Settings.from_dict(json.loads(raw))
    except (OSError, json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise ValueError("cannot load settings") from exc


def save_settings(path, settings):
    settings.validate(live=False)
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=f".{target.name}.", suffix=".tmp", dir=target.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(settings.to_dict(), f, indent=2)
            f.write("\n")
        os.replace(temp_name, target)
    finally:
        try:
            os.unlink(temp_name)
        except FileNotFoundError:
            pass
