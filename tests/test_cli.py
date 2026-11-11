import contextlib
import io
import tempfile
import unittest
from pathlib import Path

from gerrit import main


class CliTests(unittest.TestCase):
    def test_help_and_version(self):
        for args in (["--help"], ["--version"], ["serve", "--help"], ["check", "--help"], ["init", "--help"]):
            with self.subTest(args=args), contextlib.redirect_stdout(io.StringIO()):
                with self.assertRaises(SystemExit) as exit:
                    main(args)
                self.assertEqual(exit.exception.code, 0)

    def test_init_creates_once_and_never_overwrites(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / "nested" / "settings.json"
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                self.assertEqual(main(["init", "--settings", str(path)]), 0)
            original = path.read_text()
            self.assertIn('"repository"', original)
            self.assertIn('"--standalone"', original)
            with contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(main(["init", "--settings", str(path)]), 1)
            self.assertEqual(path.read_text(), original)

    def test_check_missing_settings_fails_without_network(self):
        with tempfile.TemporaryDirectory() as root, contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(main(["check", "--settings", str(Path(root) / "missing.json")]), 1)


if __name__ == "__main__":
    unittest.main()
