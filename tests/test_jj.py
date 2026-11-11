import tempfile
import unittest
from pathlib import Path
import os
import shutil
import sys
import subprocess

from gerrit.jj import Jujutsu


class JujutsuWorkspaceTests(unittest.TestCase):
    def test_workspace_is_created_on_requested_base_without_colocation(self):
        with tempfile.TemporaryDirectory(prefix="jj-runtime-test-", dir="/tmp/opencode") as temp:
            root = Path(temp)
            repo = root / "repo"
            repo.mkdir()
            subprocess.run(["jj", "git", "init", "--colocate", str(repo)], check=True, capture_output=True)
            jj = Jujutsu(repo)
            capabilities = jj.capability({"PATH": os.environ["PATH"]})
            self.assertTrue(all(capabilities[key] for key in ("upload", "workspace", "fetch", "remote", "at_operation",
                                                               "ignore_working_copy", "config", "squash", "update_stale")))
            base = jj.run("log", "-r", "@", "-T", "change_id", "--no-graph").stdout.strip()
            current_before = jj.run("log", "-r", "@", "-T", "commit_id", "--no-graph", ignore_working_copy=True).stdout.strip()
            (repo / "untouched-user-file.txt").write_text("leave this working copy untouched\n")
            workspace_path = root / "work"
            jj.add_workspace(workspace_path, "lane-test", base)
            current_after = jj.run("log", "-r", "@", "-T", "commit_id", "--no-graph", ignore_working_copy=True).stdout.strip()
            self.assertEqual(current_after, current_before)
            self.assertTrue((workspace_path / ".jj").exists())
            self.assertEqual(jj.run("log", "-r", "@-", "-T", "change_id", "--no-graph", cwd=workspace_path).stdout.strip(), base)
            git = str(Path(shutil.which("git")).resolve())
            wrapper = root / "git-wrapper"
            wrapper.write_text(f"#!{sys.executable}\nimport os,sys\nos.execv({git!r},[{git!r},*sys.argv[1:]])\n")
            wrapper.chmod(0o700)
            pinned = Jujutsu(repo, git_executable_path=wrapper)
            self.assertTrue(pinned.verify_transport_config(repo, str(wrapper), {"PATH": os.environ["PATH"]}))

    def test_feedback_squash_preserves_stable_change_and_destination_description(self):
        with tempfile.TemporaryDirectory(prefix="jj-feedback-test-", dir="/tmp/opencode") as temp:
            root = Path(temp); repo = root / "repo"; repo.mkdir()
            subprocess.run(["jj", "git", "init", "--colocate", str(repo)], check=True, capture_output=True)
            jj = Jujutsu(repo)
            env = {"PATH": os.environ["PATH"]}
            base = jj.run("log", "-r", "@", "-T", "commit_id", "--no-graph", env=env).stdout.strip()
            workspace = root / "work"
            jj.add_workspace(workspace, "feedback-test", base, env)
            target_file = workspace / "feature.txt"
            target_file.write_text("feature\n")
            stable = jj.change_id(workspace, "@")
            target_description = "core: Add feature\n\nChange-Id: I" + "a" * 40
            target = jj.commit(workspace, target_description, env)
            target_file.write_text("feature\nreview correction\n")
            fix_stable = jj.change_id(workspace, "@")
            fix = jj.commit(workspace, "core: Correct feature\n\nChange-Id: I" + "b" * 40, env)
            jj.squash_into(workspace, fix, target, env)
            refreshed = jj.mutable_map(workspace, env)[stable]
            self.assertNotEqual(refreshed, target)
            self.assertEqual(jj.change_id(workspace, refreshed), stable)
            self.assertIn("Change-Id: I" + "a" * 40, jj.describe(workspace, refreshed))
            self.assertIn("review correction", jj.diff(workspace, refreshed, env))
            self.assertNotIn(fix_stable, jj.mutable_map(workspace, env))

    def test_jj_0471_offline_gerrit_preview_uses_stderr_and_resolves_head(self):
        with tempfile.TemporaryDirectory(prefix="jj-preview-test-", dir="/tmp/opencode") as temp:
            root = Path(temp); repo = root / "repo"; repo.mkdir()
            bare = root / "named-remote.git"
            subprocess.run(["jj", "git", "init", "--colocate", str(repo)], check=True, capture_output=True)
            subprocess.run(["git", "init", "--bare", str(bare)], check=True, capture_output=True)
            jj = Jujutsu(repo)
            env = {"PATH": os.environ["PATH"], "HOME": str(root)}
            jj.add_remote(repo, "audit", str(bare), env)
            (repo / "probe.txt").write_text("offline preview only\n")
            stable = jj.change_id(repo)
            commit = jj.commit(repo, "probe: Check offline preview format\n\nChange-Id: I" + "c" * 40, env)
            selection = jj.mutable_ancestors(repo, commit, env)
            self.assertEqual(selection, [commit])
            preview = jj.upload(repo, commit, "agents/test", "audit", dry_run=True, env=env)
            self.assertIn("Found 1 heads to push to Gerrit (remote 'audit'), target branch 'agents/test'.", preview)
            self.assertIn("Dry-run: Would push", preview)
            self.assertEqual(jj.verify_preview(repo, preview, selection, "agents/test", "audit", env), [commit])
            self.assertEqual(jj.change_id(repo, commit), stable)

    def test_cross_workspace_squash_refreshes_only_owned_stale_feature_workspace(self):
        with tempfile.TemporaryDirectory(prefix="jj-cross-workspace-", dir="/tmp/opencode") as temp:
            root = Path(temp); repo = root / "repo"; repo.mkdir()
            subprocess.run(["jj", "git", "init", "--colocate", str(repo)], check=True, capture_output=True)
            jj = Jujutsu(repo)
            env = {"PATH": os.environ["PATH"], "HOME": str(root)}
            base = jj.commit_id(repo, "@")

            feature = root / "feature-workspace"
            jj.add_workspace(feature, "feature-owned", base, env)
            (feature / "feature.txt").write_text("feature base\n")
            target_change = jj.change_id(feature, "@")
            target_description = "core: Add feature\n\nChange-Id: I" + "d" * 40
            target_commit = jj.commit(feature, target_description, env)
            self.assertEqual(jj.change_id(feature, "@-"), target_change)
            self.assertNotEqual(jj.change_id(feature, "@"), target_change)

            correction = root / "correction-workspace"
            jj.add_workspace(correction, "correction-owned", target_commit, env)
            (correction / "feature.txt").write_text("feature base\nreview correction\n")
            fix_commit = jj.commit(correction, "core: Fix feature\n\nChange-Id: I" + "e" * 40, env)
            jj.squash_into(correction, fix_commit, target_commit, env)

            # The feature workspace has an empty @ child of the rewritten target.
            jj.update_stale(feature, env)
            refreshed_target = jj.commit_id(feature, "@-")
            self.assertEqual(jj.change_id(feature, refreshed_target), target_change)
            self.assertIn("Change-Id: I" + "d" * 40, jj.describe(feature, refreshed_target))
            self.assertIn("review correction", jj.diff(feature, refreshed_target, env))
            self.assertEqual(jj.diff(feature, "@", env), "")


if __name__ == "__main__":
    unittest.main()
