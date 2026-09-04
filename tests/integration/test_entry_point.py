"""scripts/run_tests.sh is the entry point, so its own behaviour is a contract.

Selecting no modules is the case worth pinning: a pattern that matches
nothing must say so, in both the isolated and the shared mode. The shared
mode once ran a bare `python -m unittest`, which is discovery from the
current directory -- so a typo silently ran the whole suite from the
repository root and reported nothing about the pattern.
"""

import subprocess
import unittest

from tests.support import REPO_ROOT

RUNNER = REPO_ROOT / "scripts" / "run_tests.sh"


def run(*argv):
    return subprocess.run([str(RUNNER)] + list(argv), cwd=str(REPO_ROOT),
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                          universal_newlines=True, timeout=120)


class TestModuleSelection(unittest.TestCase):

    def test_a_pattern_matching_nothing_is_refused_in_isolated_mode(self):
        result = run("nosuchpattern")
        self.assertEqual(2, result.returncode, result.stdout + result.stderr)
        self.assertIn("no test module matches nosuchpattern", result.stderr)

    def test_a_pattern_matching_nothing_is_refused_in_shared_mode(self):
        result = run("--shared", "nosuchpattern")
        self.assertEqual(2, result.returncode, result.stdout + result.stderr)
        self.assertIn("no test module matches nosuchpattern", result.stderr)

    def test_a_pattern_that_matches_runs_only_those_modules(self):
        result = run("test_geometry")
        self.assertEqual(0, result.returncode, result.stdout + result.stderr)
        self.assertIn("tests.unit.test_geometry", result.stdout)
        self.assertNotIn("tests.unit.test_yaml", result.stdout)

    def test_the_runner_is_executable(self):
        # It is invoked by path in CI and in the documentation.
        self.assertTrue(RUNNER.stat().st_mode & 0o111,
                        "%s is not executable" % (RUNNER,))

    def test_every_runnable_script_carrying_a_shebang_is_executable(self):
        """A shebang is a claim about how the file is run."""
        for path in sorted(REPO_ROOT.glob("**/*.py")):
            if ".build" in path.parts or "mutants" in path.parts:
                continue
            first = path.read_text().split("\n", 1)[0]
            if first.startswith("#!"):
                self.assertTrue(
                    path.stat().st_mode & 0o111,
                    "%s opens with %r but is not executable"
                    % (path.relative_to(REPO_ROOT), first))


if __name__ == "__main__":
    unittest.main()
