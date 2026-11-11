import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from gerrit.credentials import CredentialContext
from gerrit.settings import Settings
from gerrit.transport import Transport


class TransportTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.bin = self.root / "bin"
        self.bin.mkdir()
        self.repo = self.root / "repo"
        self.repo.mkdir()
        self.real_git = shutil.which("git")
        subprocess.run([self.real_git, "init", str(self.repo)], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.git_dir = self.repo / ".git"
        self.known = self.root / "trusted_known_hosts"
        self.original_known_data = b"gerrit.example ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIoriginal\n"
        self.known.write_bytes(self.original_known_data)
        self.key = self.root / "id_key"
        self.key.write_text("-----BEGIN OPENSSH PRIVATE KEY-----\nfake\n-----END OPENSSH PRIVATE KEY-----\n")
        self.key.chmod(0o600)
        self.session = self.root / "session"
        self.session.mkdir(mode=0o700)
        self.ssh_log = self.root / "ssh.jsonl"
        self.fake_ssh = self.bin / "ssh"
        self.fake_ssh.write_text(
            f"#!{sys.executable}\n"
            "import json,sys\n"
            f"open({str(self.ssh_log)!r},'a').write(json.dumps(sys.argv[1:])+'\\n')\n"
            "if any('gerrit version' in a for a in sys.argv[1:]): print('gerrit version 3.11.0')\n"
            "raise SystemExit(0)\n")
        self.fake_ssh.chmod(0o700)
        jj = self.bin / "jj"
        jj.write_text(f"#!{sys.executable}\nprint({str(self.git_dir)!r})\n")
        jj.chmod(0o700)
        self.path = f"{self.bin}:{os.environ['PATH']}"
        self.settings = Settings(repository=str(self.repo), ssh_host="gerrit.example", ssh_port=29418,
                                 username="bot", project="group/project", known_hosts=str(self.known))
        with patch.dict(os.environ, {"PATH": self.path}):
            self.transport = Transport(self.settings, str(self.key), str(self.session))
            self.destination = self.transport.expected_url
            self.transport.bind_remote(str(self.repo), "gerrit")
        added = self.run_wrapper("remote", "add", "gerrit", self.destination)
        self.assertEqual(added.returncode, 0, added.stderr)

    def tearDown(self):
        self.tmp.cleanup()

    def ssh_calls(self):
        if not self.ssh_log.exists():
            return []
        return [json.loads(line) for line in self.ssh_log.read_text().splitlines()]

    def run_wrapper(self, *args, env=None):
        child_env = dict(os.environ)
        child_env["PATH"] = self.path
        child_env.update(self.transport.environment(child_env))
        if env:
            child_env.update(env)
        return subprocess.run([self.transport.git_wrapper, "-C", str(self.repo), *args],
                              env=child_env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)

    def test_pinned_ssh_ignores_ambient_ssh_settings_and_snapshots_known_hosts(self):
        self.known.write_bytes(b"changed after snapshot\n")
        env = self.transport.environment({"PATH": self.path, "HOME": str(self.root)})
        env.update({"GIT_SSH_COMMAND": "ssh -o ProxyCommand=bad -i /tmp/attacker-key",
                    "SSH_AUTH_SOCK": "/tmp/attacker-agent"})
        helper = [sys.executable, str(Path(__file__).parents[1] / "src/gerrit/ssh_helper.py"),
                  "--config", self.transport.config_path, "-p", "29418", "bot@gerrit.example",
                  "git-upload-pack 'group/project'"]
        result = subprocess.run(helper, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)
        self.assertEqual(result.returncode, 0, result.stderr)
        args = self.ssh_calls()[-1]
        self.assertIn("-F", args)
        self.assertIn("/dev/null", args)
        self.assertIn("BatchMode=yes", args)
        self.assertIn("IdentitiesOnly=yes", args)
        self.assertIn("IdentityAgent=none", args)
        self.assertIn("IdentityFile=none", args)
        self.assertIn("CertificateFile=none", args)
        self.assertEqual(args.count("-i"), 1)
        self.assertEqual(args[args.index("-i") + 1], str(self.key))
        known_arg = next(a for a in args if a.startswith("UserKnownHostsFile="))
        snapshot = Path(known_arg.split("=", 1)[1])
        self.assertEqual(snapshot.read_bytes(), self.original_known_data)
        self.assertEqual(snapshot.stat().st_mode & 0o777, 0o600)
        self.assertNotIn("ProxyCommand", args)
        self.assertNotIn("/tmp/attacker-key", args)
        self.assertTrue(self.transport.confirmed("fetch"))
        config_data = Path(self.transport.config_path).read_text()
        self.assertNotIn("fake\\n", config_data)
        push = subprocess.run([*helper[:-1], "git-receive-pack '/group/project'"], env=env,
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)
        self.assertEqual(push.returncode, 0, push.stderr)
        self.assertTrue(self.transport.confirmed("push"))

    def test_invalid_ssh_arguments_are_refused_before_openssh(self):
        helper = [sys.executable, str(Path(__file__).parents[1] / "src/gerrit/ssh_helper.py"),
                  "--config", self.transport.config_path]
        for tail in (("-p", "29418", "bot@evil.example", "git-upload-pack 'group/project'"),
                     ("-p", "22", "bot@gerrit.example", "git-upload-pack 'group/project'"),
                     ("-p", "29418", "other@gerrit.example", "git-upload-pack 'group/project'"),
                     ("-p", "29418", "bot@gerrit.example", "git-upload-pack 'other/project'"),
                     ("-p", "29418", "bot@gerrit.example", "arbitrary command"),
                     ("-p", "29418", "-o", "ProxyCommand=bad", "bot@gerrit.example", "git-upload-pack 'group/project'")):
            with self.subTest(tail=tail):
                result = subprocess.run([*helper, *tail], stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)
                self.assertEqual(result.returncode, 2)
                self.assertEqual(self.ssh_calls(), [])

    def test_verify_uses_same_pinned_launcher_and_proof_can_be_unlinked(self):
        Path(self.transport.proof_path).unlink()
        self.assertFalse(self.transport.confirmed("verify"))
        self.assertIn("gerrit version 3.11.0", self.transport.verify())
        self.assertTrue(self.transport.confirmed("verify"))
        self.assertIn("-F", self.ssh_calls()[-1])

    def test_effective_rewrite_pushurl_and_nonssh_rejected_before_fake_ssh(self):
        cases = (
            ("url", "https://gerrit.example/group/project"),
            ("pushurl", "ssh://bot@other.example:29418/group/project"),
        )
        for kind, value in cases:
            with self.subTest(kind=kind):
                subprocess.run([self.real_git, "-C", str(self.repo), "remote", "remove", "gerrit"], check=True)
                subprocess.run([self.real_git, "-C", str(self.repo), "remote", "add", "gerrit", self.destination], check=True)
                if kind == "url":
                    subprocess.run([self.real_git, "-C", str(self.repo), "remote", "set-url", "gerrit", value], check=True)
                else:
                    subprocess.run([self.real_git, "-C", str(self.repo), "remote", "set-url", "--push", "gerrit", value], check=True)
                before = self.ssh_calls()
                result = self.run_wrapper("fetch", "gerrit")
                self.assertEqual(result.returncode, 2)
                self.assertEqual(self.ssh_calls(), before)

        subprocess.run([self.real_git, "-C", str(self.repo), "remote", "set-url", "gerrit", self.destination], check=True)
        subprocess.run([self.real_git, "-C", str(self.repo), "config",
                        f"url.ssh://bot@evil.example:29418/group/.insteadOf", self.destination], check=True)
        before = self.ssh_calls()
        result = self.run_wrapper("fetch", "gerrit")
        self.assertEqual(result.returncode, 2)
        self.assertEqual(self.ssh_calls(), before)

        subprocess.run([self.real_git, "-C", str(self.repo), "config", "--unset-all",
                        f"url.ssh://bot@evil.example:29418/group/.insteadOf"], check=False,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        subprocess.run([self.real_git, "-C", str(self.repo), "config",
                        f"url.ssh://bot@evil.example:29418/group/.pushInsteadOf", self.destination], check=True)
        before = self.ssh_calls()
        result = self.run_wrapper("fetch", "gerrit")
        self.assertEqual(result.returncode, 2)
        self.assertEqual(self.ssh_calls(), before)

    def test_private_bare_git_operation_still_checks_bound_shared_store(self):
        bare = self.root / "verify.git"
        subprocess.run([self.real_git, "init", "--bare", str(bare)], check=True,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        before = self.ssh_calls()
        result = self.run_wrapper(f"--git-dir={bare}", "fetch", self.destination, env={"GIT_TRACE": "1"})
        self.assertNotEqual(result.returncode, 2, result.stderr)
        self.assertGreater(len(self.ssh_calls()), len(before), result.stderr.decode(errors="replace"))

    def test_private_bare_remote_deviation_is_rejected_even_when_bound_store_is_valid(self):
        bare = self.root / "verify.git"
        subprocess.run([self.real_git, "init", "--bare", str(bare)], check=True,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        subprocess.run([self.real_git, f"--git-dir={bare}", "remote", "add", "gerrit",
                        "https://evil.example/group/project"], check=True)
        before = self.ssh_calls()
        result = self.run_wrapper(f"--git-dir={bare}", "fetch", "gerrit")
        self.assertEqual(result.returncode, 2)
        self.assertEqual(self.ssh_calls(), before)

    def test_real_git_wrapper_discards_inherited_ssh_and_git_config_overrides(self):
        before = self.ssh_calls()
        self.run_wrapper("fetch", "gerrit", env={
            "GIT_SSH_COMMAND": "ssh -o ProxyCommand=bad -i /tmp/attacker-key",
            "GIT_CONFIG_COUNT": "1", "GIT_CONFIG_KEY_0": "url.ssh://evil.example/.insteadOf",
            "GIT_CONFIG_VALUE_0": self.destination, "SSH_AUTH_SOCK": "/tmp/attacker-agent"})
        self.assertGreater(len(self.ssh_calls()), len(before))
        args = self.ssh_calls()[-1]
        self.assertNotIn("ProxyCommand", args)
        self.assertNotIn("/tmp/attacker-key", args)
        self.assertEqual(args.count("-i"), 1)

    def test_transport_environment_and_credential_context_are_separate(self):
        context = CredentialContext(self.settings, "secret", str(self.key), transport=self.transport)
        self.assertNotIn("GIT_SSH_COMMAND", context.environment())
        self.assertNotIn(self.transport.config_path, repr(context.environment()))
        self.assertNotIn(self.transport.proof_path, repr(context.environment()))
        self.assertNotIn(str(self.key), repr(context.environment()))
        env = context.ssh_environment()
        self.assertEqual(env["GIT_SSH_VARIANT"], "ssh")
        self.assertIn(self.transport.config_path, env["GIT_SSH_COMMAND"])
        self.assertNotIn("GERRIT_TOKEN", env)


if __name__ == "__main__":
    unittest.main()
