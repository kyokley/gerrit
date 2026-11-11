"""Git executable wrapper that checks effective fetch and push URLs first."""
from __future__ import annotations

import json
import os
import shlex
import signal
import stat
import subprocess
import sys
import time
from pathlib import Path


_OUTPUT_LIMIT = 128 * 1024


def _config(path):
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
    for field in ("git_path", "git_dir", "repository", "destination", "remote_name", "config_path"):
        if not cfg.get(field):
            raise ValueError
    return cfg


def _environment(config_path):
    env = dict(os.environ)
    for key in tuple(env):
        if key == "GIT_CONFIG" or key.startswith("GIT_CONFIG_") or key in {
            "GIT_SSH", "GIT_SSH_COMMAND", "GIT_SSH_VARIANT", "SSH_AUTH_SOCK",
            "GIT_ASKPASS", "SSH_ASKPASS", "GIT_PROXY_COMMAND",
        }:
            env.pop(key, None)
    helper = str(Path(__file__).with_name("ssh_helper.py"))
    env["GIT_SSH_COMMAND"] = shlex.join([sys.executable, helper, "--config", config_path])
    env["GIT_SSH_VARIANT"] = "ssh"
    env["GIT_CONFIG_NOSYSTEM"] = "1"
    env["GIT_CONFIG_GLOBAL"] = os.devnull
    return env


def _run_bounded(argv, cwd, env):
    proc = subprocess.Popen(argv, cwd=cwd, env=env, stdin=subprocess.DEVNULL,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            start_new_session=True, close_fds=True)
    import selectors
    selector = selectors.DefaultSelector()
    result = {"out": bytearray(), "err": bytearray()}
    selector.register(proc.stdout, selectors.EVENT_READ, "out")
    selector.register(proc.stderr, selectors.EVENT_READ, "err")
    deadline = time.monotonic() + 5
    try:
        while selector.get_map():
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise RuntimeError("git remote validation timed out")
            for key, _ in selector.select(remaining):
                chunk = os.read(key.fileobj.fileno(), 4096)
                if not chunk:
                    selector.unregister(key.fileobj)
                    key.fileobj.close()
                    continue
                value = result[key.data]
                value.extend(chunk)
                if len(value) > _OUTPUT_LIMIT:
                    raise RuntimeError("git remote validation output exceeds limit")
        status = proc.wait(timeout=max(0.1, deadline - time.monotonic()))
        return status, bytes(result["out"]), bytes(result["err"])
    finally:
        selector.close()
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        try:
            proc.wait(timeout=2)
        except subprocess.TimeoutExpired:
            proc.kill(); proc.wait()
        for stream in (proc.stdout, proc.stderr):
            if not stream.closed:
                stream.close()


def _check_remote(cfg, env, prefix=None, cwd=None):
    git = cfg["git_path"]
    cwd = cfg["repository"] if cwd is None else cwd
    remote = cfg["remote_name"]
    gitdir = cfg["git_dir"]
    prefix = [f"--git-dir={gitdir}"] if prefix is None else prefix
    for push in (False, True):
        args = [git, *prefix, "remote", "get-url"]
        if push:
            args.append("--push")
        args.extend(("--all", remote))
        status, out, _ = _run_bounded(args, cwd, env)
        try:
            urls = out.decode("utf-8", "strict").splitlines()
        except UnicodeDecodeError:
            raise RuntimeError("effective Git remote URL is invalid") from None
        if status or urls != [cfg["destination"]]:
            raise RuntimeError("effective Git fetch/push URL is outside the verified destination")


def _network_target(args, cfg):
    index = 0
    while index < len(args):
        item = args[index]
        if item == "--":
            index += 1
            break
        if not item.startswith("-"):
            break
        if item in {"-C", "-c", "--git-dir", "--work-tree", "--namespace", "--config-env"}:
            index += 2
        else:
            index += 1
    prefix = args[:index]
    command = args[index] if index < len(args) else ""
    if command not in {"fetch", "push", "ls-remote"}:
        if command == "remote":
            subcommand = args[index + 1] if index + 1 < len(args) else ""
            if subcommand not in {"add", "remove", "rename", "set-branches", "set-url", "get-url"}:
                raise RuntimeError("Git network operation is not permitted through the bound transport")
            if subcommand == "add" and any(item in {"-f", "--fetch"} for item in args[index + 2:]):
                raise RuntimeError("Git remote add with fetch is not permitted")
        if command in {"clone", "pull", "submodule", "fetch-pack", "send-pack", "remote-ext",
                       "http-fetch", "http-push", "lfs", "svn", "annex", "daemon", "upload-pack", "receive-pack"}:
            raise RuntimeError("Git network operation is not permitted through the bound transport")
        return False, None, None, prefix
    operands = args[index + 1:]
    if "--multiple" in operands:
        raise RuntimeError("multiple Git network targets are not permitted")
    takes_value = {"--depth", "--deepen", "--shallow-since", "--shallow-exclude", "--negotiation-tip",
                   "--filter", "--server-option", "--refmap", "--upload-pack", "--receive-pack", "--exec",
                   "--force-with-lease", "-j", "--jobs", "-o"}
    positional = []
    skip = False
    for offset, arg in enumerate(operands):
        if skip:
            skip = False
            continue
        if arg == "--":
            positional.extend(operands[offset + 1:])
            break
        if arg in {"--repo", "--upload-pack", "--receive-pack"} or any(arg.startswith(value + "=") for value in ("--repo", "--upload-pack", "--receive-pack")):
            raise RuntimeError("Git network target override is not permitted")
        if arg in takes_value:
            skip = True
            continue
        if arg.startswith("-"):
            continue
        positional.append(arg)
    if not positional or positional[0] not in {cfg["remote_name"], cfg["destination"]}:
        raise RuntimeError("Git network operation must name the bound Gerrit remote")
    return True, command, positional[0], prefix


def main(argv=None):
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) < 2:
        print("Git transport is not bound to a verified remote", file=sys.stderr)
        return 2
    try:
        cfg = _config(args.pop(0))
        env = _environment(cfg["config_path"])
        network, command, target, prefix = _network_target(args, cfg)
        if network:
            _check_remote(cfg, env)
            if target == cfg["remote_name"]:
                _check_remote(cfg, env, prefix=prefix, cwd=os.getcwd())
    except Exception as exc:
        print(str(exc) if isinstance(exc, RuntimeError) else "Git transport destination check failed", file=sys.stderr)
        return 2
    try:
        return subprocess.run([cfg["git_path"], "-c", "protocol.allow=never",
                               "-c", "protocol.ssh.allow=always", *args], env=env, check=False).returncode
    except OSError:
        print("actual Git executable could not be launched", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
