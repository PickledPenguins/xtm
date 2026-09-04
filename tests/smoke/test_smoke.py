"""Is it fundamentally broken?

Three questions, answered in about a second: does the program start, does
`--help` and `--version` work, and does the one operation everybody runs
produce something. Not coverage -- a fast "yes" or a fast "no" before the
rest of the suite is worth running at all.

These shell out, unlike the rest of the fast half, and they are worth the
exception: a smoke test that does not start the real program tests nothing.
"""

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from tests.support import REPO_ROOT

XTM = REPO_ROOT / "xtm.py"


def run(*argv, **kwargs):
    return subprocess.run([sys.executable, str(XTM)] + list(argv),
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                          universal_newlines=True, timeout=30, **kwargs)


class TestSmoke(unittest.TestCase):

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="xtm-smoke-"))
        self.env = {"XTM_CONFIG_DIR": str(self.tmp / "cfg"),
                    "XTM_STATE_DIR": str(self.tmp / "state"),
                    "PATH": ""}

    def test_help_works(self):
        result = run("--help")
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertIn("usage", result.stdout.lower())

    def test_version_works(self):
        result = run("--version")
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertIn("xtm", result.stdout.lower())

    def test_a_first_run_creates_a_config_and_reports_it(self):
        """The one operation everybody runs, on a machine with nothing set up."""
        import os
        result = run("--list", env=dict(os.environ, **self.env))
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertIn("default", result.stdout)
        self.assertTrue((self.tmp / "cfg" / "config.yaml").exists(),
                        "the first run did not create a config")

    def test_the_json_view_is_machine_readable(self):
        import os
        result = run("--list", "--json", "--quiet",
                     env=dict(os.environ, **self.env))
        self.assertEqual(0, result.returncode, result.stderr)
        payload = json.loads(result.stdout)
        self.assertIn("profile", payload)


if __name__ == "__main__":
    unittest.main()
