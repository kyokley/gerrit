import unittest
import os
import tempfile
from pathlib import Path
from unittest.mock import patch

from gerrit.credentials import CredentialContext, credentials
from gerrit.settings import Settings


class CredentialTests(unittest.TestCase):
    def test_agent_environment_excludes_credentials(self):
        context = CredentialContext(Settings(repository="/tmp", http_url="https://gerrit",
                                             ssh_host="gerrit", username="bot", project="p"),
                                   "secret-token", "/private/key")
        self.assertNotIn("GERRIT_TOKEN", context.environment())
        self.assertNotIn("GERRIT_SSH_KEY", context.environment())
        self.assertEqual(context.environment()["GERRIT_SSH_PORT"], "29418")

    def test_reserved_helper_and_ambient_environment_names_rejected(self):
        for settings in (Settings(token_env="PATH"), Settings(ssh_key_env="HOME"),
                         Settings(field_env={"project": "GERRIT_SECRET_FD"}),
                         Settings(field_env={"project": "XDG_CONFIG_HOME"})):
            with self.subTest(settings=settings), self.assertRaises(ValueError):
                with credentials(settings):
                    self.fail("reserved environment reference unexpectedly accepted")

    def _provider(self, directory, variant="good"):
        executable = Path(directory) / "secretspec"
        if variant in ("good", "long-help"):
            script = '''#!/usr/bin/env python3
import os, sys
a=sys.argv[1:]
if a == ["--help"]: print("--file --reason"); raise SystemExit(0)
if a == ["run", "--help"]: print("--profile --scope"); raise SystemExit(0)
e=os.environ.copy(); e.update({"GERRIT_TOKEN":"fake-token", "GERRIT_SSH_KEY":"-----BEGIN OPENSSH PRIVATE KEY-----\\nfake\\n-----END OPENSSH PRIVATE KEY-----", "GERRIT_BRANCH":"develop", "GERRIT_PORT":"2222"})
i=a.index("--"); c=a[i+1:]; os.execvpe(c[0], c, e)
'''
        elif variant == "bad-help":
            script = '#!/usr/bin/env python3\nprint("--profile --scope")\n'
        else:
            script = '''#!/usr/bin/env python3
import os, sys
a=sys.argv[1:]
if a == ["--help"]: print("--file --reason"); raise SystemExit(0)
if a == ["run", "--help"]: print("--profile --scope"); raise SystemExit(0)
os.write(int(os.environ["GERRIT_SECRET_FD"]), b"not-json")
'''
        if variant == "long-help":
            script = script.replace('print("--file --reason")', 'print("x"*3000 + "--file --reason")')
        executable.write_text(script)
        executable.chmod(0o700)
        return executable

    def _settings(self, spec):
        known_hosts = spec.parent / "known_hosts"
        known_hosts.write_text("gerrit.example ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIexample\n")
        return Settings(repository="/tmp/nonexistent-repo", http_url="https://gerrit.example",
                        ssh_host="gerrit.example", username="bot", project="project",
                        known_hosts=str(known_hosts),
                        spec_file=str(spec), credential_mode="secretspec",
                        field_env={"base_branch": "GERRIT_BRANCH", "ssh_port": "GERRIT_PORT"})

    def test_fake_provider_private_fd_and_mapped_defaults(self):
        with tempfile.TemporaryDirectory() as tmp:
            self._provider(tmp)
            spec = Path(tmp) / "secretspec.toml"
            spec.write_text("[spec]\n")
            with patch.dict(os.environ, {"PATH": f"{tmp}:{os.environ['PATH']}", "GERRIT_TOKEN": "ambient-secret"}):
                with credentials(self._settings(spec)) as context:
                    session_directory = Path(context.key_path).parent
                    self.assertEqual(context.token, "fake-token")
                    self.assertEqual(context.settings.base_branch, "develop")
                    self.assertEqual(context.settings.ssh_port, 2222)
                    self.assertNotIn("GERRIT_TOKEN", context.environment())
                    self.assertNotIn("GERRIT_SSH_KEY", context.environment())
                    self.assertNotIn("ambient-secret", repr(context.environment()))
                    self.assertTrue(Path(context.key_path).is_file())
            self.assertFalse(Path(context.key_path).exists())
            self.assertFalse(session_directory.exists())

    def test_provider_missing_mapping_and_malformed_pipe_fail_closed(self):
        for variant, expected in (("good", "required SecretSpec Gerrit field is missing"),
                                  ("malformed", "SecretSpec credential resolution failed")):
            with self.subTest(variant=variant), tempfile.TemporaryDirectory() as tmp:
                self._provider(tmp, variant)
                spec = Path(tmp) / "secretspec.toml"
                spec.write_text("[spec]\n")
                settings = self._settings(spec)
                if variant == "good":
                    settings.field_env = {"base_branch": "MISSING_BRANCH"}
                else:
                    settings.field_env = {}
                with patch.dict(os.environ, {"PATH": f"{tmp}:{os.environ['PATH']}"}):
                    with self.assertRaisesRegex(RuntimeError, expected):
                        with credentials(settings):
                            self.fail("credential resolution unexpectedly succeeded")

    def test_help_denial_is_sanitized(self):
        with tempfile.TemporaryDirectory() as tmp:
            self._provider(tmp, "bad-help")
            spec = Path(tmp) / "secretspec.toml"
            spec.write_text("[spec]\n")
            with patch.dict(os.environ, {"PATH": f"{tmp}:{os.environ['PATH']}"}):
                with self.assertRaisesRegex(RuntimeError, "installed SecretSpec does not support required run options"):
                    with credentials(self._settings(spec)):
                        self.fail("credential resolution unexpectedly succeeded")

    def test_global_help_flags_are_checked_past_old_truncation_boundary(self):
        with tempfile.TemporaryDirectory() as tmp:
            self._provider(tmp, "long-help")
            spec = Path(tmp) / "secretspec.toml"
            spec.write_text("[spec]\n")
            with patch.dict(os.environ, {"PATH": f"{tmp}:{os.environ['PATH']}"}):
                with credentials(self._settings(spec)) as context:
                    self.assertEqual(context.settings.base_branch, "develop")


if __name__ == "__main__":
    unittest.main()
