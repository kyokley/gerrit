import tempfile
import unittest
import os
from pathlib import Path

from gerrit.settings import Settings, load_settings, save_settings


class SettingsTests(unittest.TestCase):
    def test_defaults_roundtrip_and_unknown_rejected(self):
        value = Settings.from_dict({"repository": "/tmp/repo"})
        self.assertEqual(value.max_parallel, 3)
        self.assertEqual(value.agent_command, ["opencode", "run", "--standalone", "--format", "json", "{prompt}"])
        self.assertEqual(Settings.from_dict(value.to_dict()), value)
        with self.assertRaises(ValueError):
            Settings.from_dict({"oops": 1})

    def test_save_missing_destination_and_live_validation(self):
        value = Settings(repository="/tmp/repo")
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "settings.json"
            save_settings(path, value)
            self.assertEqual(load_settings(path), value)
        value.validate(live=False)
        with self.assertRaises(ValueError):
            value.validate(live=True)

    def test_bounds_secret_name_collisions_and_reserved_names(self):
        for values in ({"ssh_port": 65536}, {"max_parallel": 33}, {"max_units": 501},
                       {"token_env": "SAME", "ssh_key_env": "SAME"},
                       {"field_env": {"username": "GERRIT_TOKEN"}},
                       {"field_env": {"project": "GERRIT_SECRET_FD"}},
                       {"ssh_host": "-oProxyCommand=bad"}):
            with self.subTest(values=values), self.assertRaises(ValueError):
                Settings.from_dict(values)

    def test_atomic_save_replaces_symlink_and_load_bounds(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            target, victim = root / "settings.json", root / "victim"
            victim.write_text("untouched")
            target.symlink_to(victim)
            save_settings(target, Settings(repository="/tmp/repo"))
            self.assertFalse(target.is_symlink())
            self.assertEqual(victim.read_text(), "untouched")
            self.assertEqual(os.stat(target).st_mode & 0o777, 0o600)
            target.write_bytes(b" " * (1024 * 1024 + 1))
            with self.assertRaises(ValueError):
                load_settings(target)


if __name__ == "__main__":
    unittest.main()
