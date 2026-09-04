"""Every shipped example must still describe the tool that ships with it.

Documentation that is never executed drifts. Each example directory carries
a config, a README explaining it, and an expected.json recording what xtm
reports for that config; running them here means an example that stops
matching the tool fails the build instead of quietly going stale.
"""

import json
import os
import subprocess
import tempfile
import sys
import unittest

from tests.support import (
    REPO_ROOT,
    xtm,
)

EXAMPLES = REPO_ROOT / "examples"


def example_dirs():
    """Every example directory, in the order they are meant to be read."""
    return sorted(d for d in EXAMPLES.iterdir()
                  if d.is_dir() and (d / "config.yaml").exists())


class TestExamples(unittest.TestCase):

    def test_there_are_examples_to_run(self):
        # Guards the rest of this file: if discovery broke, every other test
        # here would vacuously pass over an empty list.
        self.assertGreaterEqual(len(example_dirs()), 6)

    def test_every_example_matches_its_recorded_output(self):
        result = subprocess.run(
            [sys.executable, str(EXAMPLES / "run.py")],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            universal_newlines=True, timeout=300)
        self.assertEqual(result.returncode, 0,
                         "examples/run.py failed:\n%s\n%s"
                         % (result.stdout, result.stderr))

    def test_every_example_has_a_readme(self):
        for directory in example_dirs():
            readme = directory / "README.md"
            self.assertTrue(readme.exists(),
                            "%s has no README.md" % (directory.name,))
            body = readme.read_text()
            self.assertGreater(
                len(body), 400,
                "%s/README.md is too short to explain anything" % (directory.name,))
            self.assertTrue(
                body.startswith("# "),
                "%s/README.md should open with a title" % (directory.name,))

    def test_every_example_is_listed_in_the_index(self):
        index = (EXAMPLES / "README.md").read_text()
        for directory in example_dirs():
            self.assertIn(directory.name, index,
                          "%s is not mentioned in examples/README.md"
                          % (directory.name,))

    def test_every_example_config_is_valid(self):
        """An example that xtm itself rejects is worse than no example."""
        for directory in example_dirs():
            text = (directory / "config.yaml").read_text()
            data = xtm.load_yaml(text)
            self.assertIn("profiles", data, directory.name)
            for name in data["profiles"]:
                xtm.validate_profile(data, name)

    def test_every_example_config_reads_the_same_under_both_backends(self):
        """The whole point of example 06, applied to all of them."""
        if not xtm.HAVE_PYYAML:
            self.skipTest("PyYAML is not installed, so there is nothing to "
                          "compare the built-in parser against")
        saved = xtm.HAVE_PYYAML
        self.addCleanup(setattr, xtm, "HAVE_PYYAML", saved)
        for directory in example_dirs():
            text = (directory / "config.yaml").read_text()
            xtm.HAVE_PYYAML = False
            builtin = xtm.load_yaml(text)
            xtm.HAVE_PYYAML = True
            pyyaml_ = xtm.load_yaml(text)
            self.assertEqual(builtin, pyyaml_,
                             "%s reads differently depending on whether "
                             "PyYAML is installed" % (directory.name,))

    def test_expected_output_is_recorded_for_every_example(self):
        for directory in example_dirs():
            expected = directory / "expected.json"
            self.assertTrue(expected.exists(),
                            "%s has no expected.json" % (directory.name,))
            payload = json.loads(expected.read_text())
            self.assertIn("sessions", payload, directory.name)


class TestNegativeExamples(unittest.TestCase):
    """Every config in examples/negative/ must fail, as recorded.

    A rule is easiest to understand from the config that breaks it and the
    sentence the tool answers with, so these are documentation first. They
    are also the only place the exact wording of a refusal is pinned:
    change a message and one of these fails, which is the prompt to decide
    whether the new wording is better rather than to find out later.
    """

    NEGATIVE = EXAMPLES / "negative"

    def negative_dirs(self):
        return sorted(d for d in self.NEGATIVE.iterdir() if d.is_dir())

    def test_there_are_negative_examples_to_run(self):
        self.assertGreaterEqual(len(self.negative_dirs()), 8)

    def test_each_one_carries_a_readme_and_a_recorded_error(self):
        for directory in self.negative_dirs():
            self.assertTrue((directory / "config.yaml").exists(),
                            "%s has no config.yaml" % (directory.name,))
            readme = directory / "README.md"
            self.assertTrue(readme.exists(),
                            "%s has no README.md" % (directory.name,))
            self.assertIn("**The fix:**", readme.read_text(),
                          "%s does not say what the fix is" % (directory.name,))
            recorded = directory / "expected-error.txt"
            self.assertTrue(recorded.exists(),
                            "%s has no expected-error.txt" % (directory.name,))
            self.assertGreater(len(recorded.read_text().strip()), 20,
                               "%s recorded an empty message"
                               % (directory.name,))

    def test_every_negative_example_fails_as_recorded(self):
        result = subprocess.run(
            [sys.executable, str(EXAMPLES / "run.py"), "--negative"],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            universal_newlines=True, timeout=300)
        self.assertEqual(result.returncode, 0,
                         "negative examples did not fail as recorded:\n%s\n%s"
                         % (result.stdout, result.stderr))

    def test_the_catalogue_does_not_depend_on_the_machine_tmpdir(self):
        """The recorded messages name the config as CONFIG, whatever $TMPDIR is.

        The path was once stripped with a literal `/tmp` pattern, so under
        `TMPDIR=/var/tmp` every case failed with a mangled "/varCONFIG" --
        a suite whose result depends on the machine running it.
        """
        with tempfile.TemporaryDirectory(prefix="xtm-tmpdir-") as elsewhere:
            environment = dict(os.environ, TMPDIR=elsewhere)
            result = subprocess.run(
                [sys.executable, str(EXAMPLES / "run.py"), "--negative"],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                universal_newlines=True, timeout=300, env=environment)
        self.assertEqual(result.returncode, 0,
                         "the negative catalogue failed with TMPDIR=%s:\n%s\n%s"
                         % (elsewhere, result.stdout, result.stderr))

    def test_every_negative_example_is_listed_in_the_index(self):
        index = (EXAMPLES / "README.md").read_text()
        for directory in self.negative_dirs():
            self.assertIn(directory.name, index,
                          "negative/%s is not mentioned in examples/README.md"
                          % (directory.name,))
