"""Authenticated, full-object Gerrit upload confirmation."""

from __future__ import annotations

import os
import shutil
import tempfile
from pathlib import Path


class ConfirmationError(RuntimeError):
    pass


def _positive_gerrit_integer(value: int | str) -> int:
    if type(value) is int:
        number = value
    elif isinstance(value, str) and value.isascii() and value.isdecimal() and len(value) <= 19:
        number = int(value)
    else:
        raise ConfirmationError("authenticated readback lacks a safe positive Gerrit number")
    if number <= 0 or number > 9_223_372_036_854_775_807:
        raise ConfirmationError("authenticated readback lacks a safe positive Gerrit number")
    return number


def _run(argv: list[str], *, env: dict[str, str], timeout: int = 30) -> str:
    try:
        from .workflow import Workflow
        result = Workflow._bounded_command(argv, cwd=Path.cwd(), env=dict(env), timeout=timeout, limit=64 * 1024)
    except Exception as exc:
        raise ConfirmationError("immutable Git object verification failed") from exc
    if result["returncode"]:
        raise ConfirmationError("immutable Git object is missing or invalid")
    return result["stdout"].strip()


def local_object_store(jj, workspace: str | os.PathLike[str], env: dict[str, str]) -> Path:
    """Resolve jj's Git store without consulting or mutating a working copy."""
    try:
        value = jj.run("git", "root", cwd=workspace, env=env, ignore_working_copy=True).stdout.strip()
    except Exception as exc:
        raise ConfirmationError("Jujutsu Git object store could not be resolved") from exc
    path = Path(value).resolve()
    if not path.is_dir():
        raise ConfirmationError("Jujutsu Git object store is unavailable")
    return path


def commit_facts(store: Path, revision: str, env: dict[str, str]) -> tuple[str, tuple[str, ...]]:
    git = shutil.which("git")
    if not git:
        raise ConfirmationError("Git is unavailable for immutable object verification")
    line = _run([git, "--git-dir", str(store), "--no-replace-objects", "show", "-s",
                 "--format=%T%n%P", revision], env=env)
    parts = line.splitlines()
    if len(parts) not in (1, 2) or not parts[0]:
        raise ConfirmationError("immutable commit tree or parents are invalid")
    return parts[0], tuple(parts[1].split()) if len(parts) == 2 else ()


def verify_server_revision(*, jj, workspace, local_revision: str, expected_parent: str,
                           server_revision: str, number: str, patchset: int | str,
                           expected_server_parent: str, remote_url: str, transport,
                           env: dict[str, str]) -> None:
    """Compare complete tree and parent mapping after exact ref fetch in private storage."""
    if not server_revision:
        raise ConfirmationError("authenticated readback lacks exact revision identity")
    change_number = _positive_gerrit_integer(number)
    patchset_number = _positive_gerrit_integer(patchset)
    store = local_object_store(jj, workspace, env)
    local_tree, local_parents = commit_facts(store, local_revision, env)
    if len(local_parents) != 1 or local_parents[0] != expected_parent:
        raise ConfirmationError("local immutable commit has an unexpected parent")
    root = Path(tempfile.mkdtemp(prefix="gerrit-confirm-"))
    os.chmod(root, 0o700)
    try:
        bare = root / "objects.git"
        git = shutil.which("git")
        if not git:
            raise ConfirmationError("Git is unavailable for server confirmation")
        init_env = {"PATH": env.get("PATH", os.defpath), "HOME": env.get("HOME", "/nonexistent"),
                    "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": os.devnull}
        _run([git, "init", "--bare", str(bare)], env=init_env, timeout=15)
        ref = f"refs/changes/{change_number % 100:02d}/{change_number}/{patchset_number}"
        fetch_env = dict(env)
        fetch_env["GIT_TERMINAL_PROMPT"] = "0"
        # Wrapper validates the exact bound SSH URL and invokes the strict SSH launcher.
        try:
            from .workflow import Workflow
            result = Workflow._bounded_command([transport.git_wrapper, "--git-dir", str(bare), "fetch",
                                                "--no-tags", remote_url, f"{ref}:refs/verify/revision"],
                                               cwd=root, env=fetch_env, timeout=120, limit=64 * 1024)
        except Exception as exc:
            raise ConfirmationError("exact Gerrit revision could not be fetched through pinned transport") from exc
        if result["returncode"]:
            raise ConfirmationError("exact Gerrit revision could not be fetched through pinned transport")
        server_tree, server_parents = commit_facts(bare, "refs/verify/revision", fetch_env)
        # Gerrit rewrites commit metadata and hashes; compare tree and parent trees
        # through the authenticated, already-confirmed parent mapping.
        fetched_revision = _run([git, "--git-dir", str(bare), "--no-replace-objects", "rev-parse",
                                 "refs/verify/revision"], env=fetch_env)
        if (fetched_revision.lower() != server_revision.lower() or server_tree != local_tree or len(server_parents) != 1 or
                server_parents[0] != expected_server_parent):
            raise ConfirmationError("server revision tree or parent mapping differs from local commit")
        parent_tree, _ = commit_facts(store, expected_parent, env)
        fetched_parent_tree, _ = commit_facts(bare, server_parents[0], fetch_env)
        if parent_tree != fetched_parent_tree:
            raise ConfirmationError("server revision parent does not map to the expected confirmed parent")
    finally:
        shutil.rmtree(root, ignore_errors=True)
