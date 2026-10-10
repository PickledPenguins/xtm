"""Every shipped example must still describe the tool that ships with it.

Documentation that is never executed drifts. Each example directory carries
a config, a README explaining it, and an expected.json recording what xtm
reports for that config; running them here means an example that stops
matching the tool fails the build instead of quietly going stale.
"""

import json
import os
import platform
import re
import subprocess
import tempfile
import sys
import unittest

from tests.support import (
    REPO_ROOT,
    IntegrationBase,
    xtm,
)

EXAMPLES = REPO_ROOT / "examples"


TIERS = ("simple", "moderate", "complex")

# What a catalog is for is that a reader finds their own case in it, so the
# floor is on variety: enough simple cases that every feature has one, enough
# moderate ones for the combinations people really make, and enough complex
# ones that the interactions are shown more than once.
TIER_MINIMUM = {"simple": 15, "moderate": 15, "complex": 10}


def example_dirs(tier=None):
    """Every example directory, in the order they are meant to be read."""
    found = []
    for name in ((tier,) if tier else TIERS):
        directory = EXAMPLES / name
        if directory.is_dir():
            found.extend(sorted(d for d in directory.iterdir()
                                if d.is_dir() and (d / "config.yaml").exists()))
    return found


class TestExamples(unittest.TestCase):

    def test_there_are_examples_to_run(self):
        # Guards the rest of this file: if discovery broke, every other test
        # here would vacuously pass over an empty list.
        self.assertGreaterEqual(len(example_dirs()), 6)

    def test_each_tier_carries_enough_cases(self):
        """A catalog too thin to find your own case in sends the reader to
        the manual, which is what it exists to save them from."""
        for tier, minimum in sorted(TIER_MINIMUM.items()):
            found = len(example_dirs(tier))
            self.assertGreaterEqual(
                found, minimum,
                "examples/%s/ has %d cases, below the %d this catalog holds "
                "itself to" % (tier, found, minimum))

    def test_every_example_says_which_level_it_is(self):
        """The README's level line is how a reader picks where to start."""
        for directory in example_dirs():
            readme = (directory / "README.md").read_text()
            self.assertIn("Level:", readme,
                          "%s/%s does not say which level it is"
                          % (directory.parent.name, directory.name))

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

    def test_the_catalog_does_not_depend_on_the_machine_tmpdir(self):
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
                         "the negative catalog failed with TMPDIR=%s:\n%s\n%s"
                         % (elsewhere, result.stdout, result.stderr))

    def test_every_negative_example_is_listed_in_the_index(self):
        index = (EXAMPLES / "README.md").read_text()
        for directory in self.negative_dirs():
            self.assertIn(directory.name, index,
                          "negative/%s is not mentioned in examples/README.md"
                          % (directory.name,))


class TestWorkflowExamples(unittest.TestCase):
    """Every command sequence in examples/workflows/ produces its transcript.

    The configuration examples only report; these run the commands that
    switch, capture, edit and check, against the fake tools, so a change in
    what a command does or says shows up as a changed transcript.
    """

    WORKFLOWS = EXAMPLES / "workflows"

    def workflow_dirs(self):
        return sorted(d for d in self.WORKFLOWS.iterdir() if d.is_dir())

    def test_there_are_workflows_to_run(self):
        self.assertGreaterEqual(len(self.workflow_dirs()), 8)

    def test_each_one_carries_a_readme_a_script_and_a_transcript(self):
        for directory in self.workflow_dirs():
            for name in ("README.md", "script", "expected.txt"):
                self.assertTrue((directory / name).exists(),
                                "%s has no %s" % (directory.name, name))
            self.assertIn("Level: **workflow**", (directory / "README.md").read_text(),
                          "%s does not say its level" % (directory.name,))

    def test_every_workflow_produces_its_recorded_transcript(self):
        result = subprocess.run(
            [sys.executable, str(EXAMPLES / "run.py"), "--workflows"],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            universal_newlines=True, timeout=300)
        self.assertEqual(result.returncode, 0,
                         "workflows did not match their transcripts:\n%s\n%s"
                         % (result.stdout, result.stderr))
        self.assertIn("\n%d workflow(s) match their recorded transcript.\n"
                      % (len(self.workflow_dirs()),), result.stdout)

    def test_every_workflow_is_listed_in_the_index(self):
        index = (EXAMPLES / "README.md").read_text()
        for directory in self.workflow_dirs():
            self.assertIn("workflows/%s" % (directory.name,), index,
                          "workflows/%s is not mentioned in examples/README.md"
                          % (directory.name,))
class TestExampleProfile(unittest.TestCase):
    """A run that executes the examples reports where the time went.

    The examples are the closest thing here to real usage, so they are the
    right place to ask that question -- and a profile nobody looks at is a
    profile that stops being produced, so the run prints it.
    """

    def test_the_profiler_reports_xtm_own_functions(self):
        result = subprocess.run(
            [sys.executable, str(REPO_ROOT / "scripts" / "profile_examples.py")],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            universal_newlines=True, timeout=300)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("example(s) in", result.stdout)
        self.assertIn("xtm.py:", result.stdout,
                      "the table names no xtm function, so the filter broke")
        self.assertIn("load_yaml", result.stdout,
                      "reading the config is most of the work and should be "
                      "in any honest profile of it")

    def test_the_profile_is_written_where_it_can_be_read_later(self):
        subprocess.run(
            [sys.executable, str(REPO_ROOT / "scripts" / "profile_examples.py")],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=300)
        written = REPO_ROOT / ".build" / "profile"
        self.assertTrue((written / "examples.txt").exists(),
                        "no text report was written")
        self.assertTrue((written / "examples.pstats").exists(),
                        "no pstats file was written, so nothing can be "
                        "explored afterwards")


class TestReadmeQuickStart(IntegrationBase):
    """The output the README's quick start shows is what xtm prints for the
    profile it shows, so a first-time reader can tell success from silence."""

    def test_bringing_up_the_sample_profile_prints_what_the_readme_shows(self):
        readme = (REPO_ROOT / "README.md").read_text()
        config = re.search(r"```yaml\n(profiles:.*?)```", readme, re.S).group(1)
        shown = re.search(r"```console\n\$ xtm desk\n(.*?)```", readme, re.S).group(1)
        self.write_config(config)
        # The sample binds the profile to workstation*; act as one of those.
        result = self.run_xtm("--hostname", "workstation1", "desk")
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertEqual("Acting as host 'workstation1' rather than %s.\n"
                         % (platform.node().split(".")[0],) + shown, result.stderr)
