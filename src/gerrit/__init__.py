"""Local Gerrit task dashboard command line."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
from pathlib import Path
from typing import Sequence

__version__ = "0.1.0"


def default_settings_path() -> Path:
    return Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "gerrit" / "settings.json"


def default_state_dir() -> Path:
    return Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local" / "state")) / "gerrit"


def _settings_template() -> dict[str, object]:
    try:
        from .settings import Settings

        return Settings().to_dict()
    except ImportError:
        return {
            "repository": "", "agent_command": ["opencode", "run", "--standalone", "--format", "json", "{prompt}"],
            "max_parallel": 3, "max_units": 20, "agent_timeout": 1800,
            "http_url": "", "ssh_host": "", "ssh_port": 29418, "username": "",
            "project": "", "base_branch": "main", "known_hosts": "",
            "credential_mode": "token-file", "token_file": "", "ssh_key_file": "",
            "spec_file": "", "profile": "default", "scope": "gerrit",
            "token_env": "GERRIT_TOKEN", "ssh_key_env": "GERRIT_SSH_KEY", "field_env": {},
            "poll_interval": 300, "monitor_hours": 12,
        }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="gerrit", description="Local Gerrit task dashboard")
    parser.add_argument("--version", action="version", version=f"gerrit {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)
    for command in ("init", "check"):
        cmd = sub.add_parser(command)
        cmd.add_argument("--settings", type=Path, default=default_settings_path())
    serve_parser = sub.add_parser("serve")
    serve_parser.add_argument("--settings", type=Path, default=default_settings_path())
    serve_parser.add_argument("--state-dir", type=Path, default=default_state_dir())
    serve_parser.add_argument("--port", type=int, default=8765)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "init":
        if args.settings.exists():
            print(f"Settings already exist: {args.settings}", file=sys.stderr)
            return 1
        try:
            args.settings.parent.mkdir(parents=True, exist_ok=True)
            with args.settings.open("x", encoding="utf-8") as output:
                json.dump(_settings_template(), output, indent=2)
                output.write("\n")
            try:
                args.settings.chmod(0o600)
            except OSError:
                pass
        except (OSError, ValueError):
            print("Could not create settings file", file=sys.stderr)
            return 1
        print(f"Created settings: {args.settings}")
        return 0
    if args.command == "check":
        try:
            from .settings import load_settings

            settings = load_settings(args.settings)
            settings.validate(live=False)
            repository = Path(settings.repository)
            if not repository.is_absolute() or not repository.is_dir():
                raise ValueError("repository must be an existing absolute directory")
            if shutil.which("jj") is None:
                raise ValueError("jj executable is unavailable")
        except Exception:
            print("Settings or local prerequisites are invalid", file=sys.stderr)
            return 1
        print("Settings and local prerequisites are valid")
        return 0
    if not 0 <= args.port <= 65535:
        print("Port must be between 0 and 65535", file=sys.stderr)
        return 2
    from .server import serve

    try:
        serve(args.settings, args.state_dir, port=args.port)
    except Exception:
        print("Server could not start", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
