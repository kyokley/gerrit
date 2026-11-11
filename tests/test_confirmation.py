import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from gerrit.confirmation import ConfirmationError, verify_server_revision


class _JJ:
    def __init__(self, root):
        self.root = root

    def run(self, *args, **kwargs):
        return type("Result", (), {"stdout": str(self.root) + "\n"})()


class _Transport:
    git_wrapper = shutil.which("git")


class ConfirmationTests(unittest.TestCase):
    def run_git(self, *args, cwd, env=None):
        return subprocess.run(["git", *args], cwd=cwd, env=env, check=True,
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True).stdout.strip()

    def test_full_tree_and_parent_confirmation_handles_binary_delete_and_mode(self):
        with tempfile.TemporaryDirectory(prefix="confirm-git-", dir="/tmp/opencode") as temp:
            root = Path(temp)
            local = root / "local.git"
            remote = root / "server.git"
            subprocess.run(["git", "init", "--bare", str(local)], check=True, capture_output=True)
            subprocess.run(["git", "init", "--bare", str(remote)], check=True, capture_output=True)
            work = root / "work"
            subprocess.run(["git", "clone", str(local), str(work)], check=True, capture_output=True)
            env = dict(os.environ, GIT_AUTHOR_NAME="Test", GIT_AUTHOR_EMAIL="test@example.invalid",
                       GIT_COMMITTER_NAME="Test", GIT_COMMITTER_EMAIL="test@example.invalid")
            (work / "gone").write_text("delete me")
            (work / "binary").write_bytes(b"\x00\xffbinary")
            (work / "script").write_text("#!/bin/sh\nexit 0\n")
            (work / "script").chmod(0o755)
            self.run_git("add", "-A", cwd=work, env=env)
            self.run_git("commit", "-m", "base", cwd=work, env=env)
            local_parent = self.run_git("rev-parse", "HEAD", cwd=work, env=env)
            self.run_git("push", str(remote), "HEAD:refs/heads/base", cwd=work, env=env)
            (work / "gone").unlink()
            (work / "binary").write_bytes(b"\x00\xfeupdated")
            (work / "script").chmod(0o644)
            self.run_git("add", "-A", cwd=work, env=env)
            self.run_git("commit", "-m", "local feature", cwd=work, env=env)
            local_revision = self.run_git("rev-parse", "HEAD", cwd=work, env=env)
            local_tree = self.run_git("rev-parse", "HEAD^{tree}", cwd=work, env=env)
            self.run_git("push", str(remote), f"{local_revision}:refs/heads/local-feature", cwd=work, env=env)

            # Make a distinct server commit object with the same complete tree and parent.
            server_revision = self.run_git("-c", "user.name=Server", "-c", "user.email=server@example.invalid",
                                           "commit-tree", local_tree, "-p", local_parent,
                                           "-m", "server metadata differs", cwd=work, env=env)
            change_numbers = (1, 9, 10, 101)
            for number in change_numbers:
                suffix = f"{number % 100:02d}"
                self.run_git("push", str(remote),
                             f"{server_revision}:refs/changes/{suffix}/{number}/2", cwd=work, env=env)
            self.run_git("push", str(remote), f"{local_parent}:refs/changes/01/101/1", cwd=work, env=env)
            base_tree = self.run_git("rev-parse", f"{local_parent}^{{tree}}", cwd=work, env=env)
            wrong_tree_revision = self.run_git("-c", "user.name=Server", "-c", "user.email=server@example.invalid",
                                               "commit-tree", base_tree, "-p", local_parent,
                                               "-m", "wrong tree", cwd=work, env=env)
            self.run_git("push", str(remote), f"{wrong_tree_revision}:refs/changes/01/101/3", cwd=work, env=env)
            # A local refetch-capable object store holds both local immutable objects.
            store = root / "store.git"
            subprocess.run(["git", "init", "--bare", str(store)], check=True, capture_output=True)
            self.run_git("fetch", str(remote), f"{local_parent}:refs/local/base", cwd=work, env=env)
            local_store_parent = self.run_git("rev-parse", "refs/local/base", cwd=work, env=env)
            # Copy local feature objects into the isolated store using a local transport.
            self.run_git("--git-dir", str(store), "fetch", str(remote), f"{local_revision}:refs/feature", cwd=work, env=env)
            self.run_git("--git-dir", str(store), "fetch", str(remote), f"{local_store_parent}:refs/base", cwd=work, env=env)
            refs = self.run_git("--git-dir", str(remote), "show-ref", cwd=work, env=env)
            for number in change_numbers:
                suffix = f"{number % 100:02d}"
                self.assertIn(f"refs/changes/{suffix}/{number}/2", refs)
                verify_server_revision(jj=_JJ(store), workspace=work, local_revision=local_revision,
                                       expected_parent=local_store_parent, server_revision=server_revision,
                                       number=number, patchset=2, expected_server_parent=local_parent,
                                       remote_url=str(remote), transport=_Transport(), env=env)
            with self.assertRaises(ConfirmationError):
                verify_server_revision(jj=_JJ(store), workspace=work, local_revision=local_revision,
                                       expected_parent=local_store_parent, server_revision=server_revision,
                                       number="101", patchset=2, expected_server_parent="0" * 40,
                                       remote_url=str(remote), transport=_Transport(), env=env)
            with self.assertRaises(ConfirmationError):
                verify_server_revision(jj=_JJ(store), workspace=work, local_revision=local_revision,
                                       expected_parent=local_store_parent, server_revision=wrong_tree_revision,
                                       number="101", patchset=3, expected_server_parent=local_parent,
                                       remote_url=str(remote), transport=_Transport(), env=env)

    def test_invalid_gerrit_change_numbers_and_patchsets_are_rejected(self):
        for number, patchset in ((0, 1), (-1, 1), (True, 1), (1, 0), (1, -1), (1, False)):
            with self.subTest(number=number, patchset=patchset):
                with self.assertRaises(ConfirmationError):
                    verify_server_revision(jj=None, workspace="unused", local_revision="local",
                                           expected_parent="parent", server_revision="revision",
                                           number=number, patchset=patchset,
                                           expected_server_parent="server-parent", remote_url="unused",
                                           transport=_Transport(), env={})


if __name__ == "__main__":
    unittest.main()
