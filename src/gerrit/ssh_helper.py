"""Validate Git's SSH request, then execute only the pinned OpenSSH transport."""
from __future__ import annotations

import json
import fcntl
import os
import shlex
import stat
import subprocess
import sys


def _load(path):
    fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    try:
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode) or st.st_uid != os.getuid() or stat.S_IMODE(st.st_mode) & 0o077:
            raise ValueError
        with os.fdopen(fd, "rb", closefd=False) as f:
            data = f.read(65537)
        if len(data) > 65536:
            raise ValueError
        cfg = json.loads(data)
    finally:
        os.close(fd)
    required = ("ssh_path", "key_path", "known_hosts", "proof_path", "host", "username", "port", "project", "destination")
    if not isinstance(cfg, dict) or any(k not in cfg for k in required):
        raise ValueError
    for name in ("ssh_path", "key_path", "known_hosts", "proof_path"):
        if not isinstance(cfg[name], str) or not os.path.isabs(cfg[name]):
            raise ValueError
    return cfg


def _fail(message):
    print(message, file=sys.stderr)
    return 2


def _parse(argv, cfg):
    port = None
    args = list(argv)
    while args and args[0].startswith("-"):
        opt = args.pop(0)
        if opt == "-p" and args:
            if port is not None:
                raise ValueError
            port = int(args.pop(0))
        elif opt == "-o" and args:
            if args.pop(0) != "SendEnv=GIT_PROTOCOL":
                raise ValueError
        else:
            raise ValueError
    if port != cfg["port"] or not args:
        raise ValueError
    target = args.pop(0)
    if "@" not in target:
        raise ValueError
    username, host = target.split("@", 1)
    expected_host = cfg["host"]
    if expected_host.startswith("[") and expected_host.endswith("]"):
        expected_host = expected_host[1:-1]
    if host.startswith("[") and host.endswith("]"):
        host = host[1:-1]
    if username != cfg["username"] or host.lower() != expected_host.lower() or not args:
        raise ValueError
    command = shlex.split(" ".join(args), posix=True)
    if command == ["gerrit", "version"]:
        return "verify", command
    valid_project_path = command[1] in (cfg["project"], "/" + cfg["project"].lstrip("/")) if len(command) == 2 else False
    if len(command) != 2 or command[0] not in ("git-upload-pack", "git-receive-pack") or not valid_project_path:
        raise ValueError
    return ("fetch" if command[0] == "git-upload-pack" else "push"), command


def _record(cfg, operation, status):
    entry = json.dumps({"operation": operation, "destination": cfg["destination"], "status": status}, separators=(",", ":")).encode() + b"\n"
    fd = os.open(cfg["proof_path"], os.O_WRONLY | os.O_APPEND | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0), 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        st = os.fstat(fd)
        if (not stat.S_ISREG(st.st_mode) or st.st_uid != os.getuid() or
                stat.S_IMODE(st.st_mode) & 0o077 or st.st_size + len(entry) > 1024 * 1024):
            raise OSError("unsafe proof file")
        view = memoryview(entry)
        while view:
            view = view[os.write(fd, view):]
    finally:
        os.close(fd)


def main(argv=None):
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) < 2 or args[0] != "--config":
        return _fail("SSH transport request rejected")
    try:
        cfg = _load(args[1])
        operation, remote_command = _parse(args[2:], cfg)
    except Exception:
        return _fail("SSH transport request rejected")
    destination = f"{cfg['username']}@{cfg['host']}"
    if ":" in cfg["host"] and not cfg["host"].startswith("["):
        destination = f"{cfg['username']}@[{cfg['host']}]"
    ssh_args = [cfg["ssh_path"], "-F", "/dev/null", "-o", "BatchMode=yes",
                "-o", "PreferredAuthentications=publickey", "-o", "PasswordAuthentication=no",
                "-o", "KbdInteractiveAuthentication=no", "-o", "PubkeyAuthentication=yes",
                "-o", "IdentitiesOnly=yes", "-o", "IdentityAgent=none", "-o", "IdentityFile=none",
                "-o", "CertificateFile=none", "-i", cfg["key_path"],
                "-p", str(cfg["port"]), "-o", f"UserKnownHostsFile={cfg['known_hosts']}",
                "-o", "GlobalKnownHostsFile=/dev/null", "-o", "StrictHostKeyChecking=yes",
                "-o", "SendEnv=GIT_PROTOCOL", destination, shlex.join(remote_command)]
    env = {"PATH": "/usr/bin:/bin", "HOME": os.environ.get("HOME", ""),
           "LANG": os.environ.get("LANG", "C"), "LC_ALL": os.environ.get("LC_ALL", "C")}
    if os.environ.get("GIT_PROTOCOL"):
        env["GIT_PROTOCOL"] = os.environ["GIT_PROTOCOL"]
    try:
        result = subprocess.run(ssh_args, env=env, stdin=None, stdout=None, stderr=None, check=False)
        if result.returncode == 0:
            try:
                _record(cfg, operation, 0)
            except OSError:
                return _fail("SSH succeeded but operation proof could not be recorded")
        return result.returncode
    except OSError:
        return _fail("pinned OpenSSH could not be launched")


if __name__ == "__main__":
    raise SystemExit(main())
