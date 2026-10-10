"""What a command says when it cannot do what was asked.

These are the paths a user reaches by typing the wrong name, answering "no",
or running against a tmux that will not cooperate. Each one asserts the
message and the exit code, because "it exited non-zero" is not what the user
sees.

Confirmation prompts need a terminal: xtm treats a missing tty as consent, on
the grounds that a scripted caller has already opted in and cannot answer. So
the cancel paths here run xtm under a pty, which is also the only way to
exercise the prompt at all.
"""

import os
import pty
import subprocess
import sys
import unittest

from tests.support import IntegrationBase, REPO_ROOT

XTM_SCRIPT = REPO_ROOT / "src" / "xtm.py"


class TestPromptCancellation(IntegrationBase):
    """Answering "no" must leave everything alone."""

    def run_with_answer(self, argv, answer):
        """Run xtm attached to a pty and type `answer` at its prompt."""
        master, slave = pty.openpty()
        process = subprocess.Popen(
            [sys.executable, str(XTM_SCRIPT)] + list(argv),
            stdin=slave, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            universal_newlines=True, env=dict(self.env))
        os.close(slave)
        os.write(master, answer.encode())
        stdout, stderr = process.communicate(timeout=40)
        os.close(master)
        return process.returncode, stdout, stderr

    def test_declining_to_delete_a_profile_keeps_it(self):
        self.run_xtm("--copy-profile", "default", "doomed")
        code, _out, err = self.run_with_answer(
            ["--delete-profile", "doomed"], "n\n")
        self.assertEqual(0, code, err)
        self.assertIn("Canceled", err)
        self.assertIn("doomed", self.run_xtm("-P").stdout)

    def test_declining_close_all_kills_nothing(self):
        self.run_xtm("-s", "work1", "0,0,900,700")
        self.run_xtm("-o", "work1")
        code, _out, err = self.run_with_answer(["--close-all"], "n\n")
        self.assertEqual(0, code, err)
        self.assertIn("Canceled", err)

    def test_accepting_deletes_the_profile(self):
        """The other half: the prompt is a real question, not a formality."""
        self.run_xtm("--copy-profile", "default", "doomed")
        code, _out, err = self.run_with_answer(
            ["--delete-profile", "doomed"], "y\n")
        self.assertEqual(0, code, err)
        self.assertNotIn("doomed", self.run_xtm("-P").stdout)


class TestProfileManagementErrors(IntegrationBase):
    """Naming something that is not there, or that already is."""

    def test_copying_a_profile_that_does_not_exist(self):
        result = self.run_xtm("--copy-profile", "ghost", "copy")
        self.assertEqual(1, result.returncode)
        self.assertIn("ghost", result.stderr)
        self.assertIn("does not exist", result.stderr)

    def test_copying_onto_a_name_already_taken(self):
        self.run_xtm("--copy-profile", "default", "taken")
        result = self.run_xtm("--copy-profile", "default", "taken")
        self.assertEqual(1, result.returncode)
        self.assertIn("already exists", result.stderr)

    def test_renaming_a_profile_that_does_not_exist(self):
        result = self.run_xtm("--rename-profile", "ghost", "other")
        self.assertEqual(1, result.returncode)
        self.assertIn("does not exist", result.stderr)

    def test_renaming_onto_a_name_already_taken(self):
        self.run_xtm("--copy-profile", "default", "taken")
        self.run_xtm("--copy-profile", "default", "other")
        result = self.run_xtm("--rename-profile", "taken", "other")
        self.assertEqual(1, result.returncode)
        self.assertIn("already exists", result.stderr)

    def test_renaming_onto_the_reserved_profile_says_which_rule_it_broke(self):
        """`default` is reserved, and the message says so rather than
        reporting the generic name clash."""
        self.run_xtm("--copy-profile", "default", "taken")
        result = self.run_xtm("--rename-profile", "taken", "default")
        self.assertEqual(1, result.returncode)
        self.assertIn("reserved", result.stderr)

    def test_deleting_a_profile_that_does_not_exist(self):
        result = self.run_xtm("--delete-profile", "ghost", "-y")
        self.assertEqual(1, result.returncode)
        self.assertIn("ghost", result.stderr)

    def test_making_a_profile_global_that_does_not_exist(self):
        result = self.run_xtm("--make-global", "ghost")
        self.assertEqual(1, result.returncode)
        self.assertIn("ghost", result.stderr)

    def test_deleting_the_current_profile_under_dry_run_clears_nothing(self):
        self.run_xtm("--copy-profile", "default", "doomed")
        self.run_xtm("--profile", "doomed", "-C")
        result = self.run_xtm("--dry-run", "--delete-profile", "doomed", "-y")
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertIn("[dry-run]", result.stderr)
        self.assertIn("doomed", self.run_xtm("-P").stdout,
                      "a dry run deleted the profile for real")


class TestTmuxRefusals(IntegrationBase):
    """tmux is there, and says no."""

    def refusing(self, verb):
        """Environment for a fake tmux that refuses exactly one verb."""
        return {"XTM_FAKE_TMUX_REFUSE": verb}

    def test_a_refused_kill_is_reported_per_session(self):
        self.run_xtm("-s", "work1", "0,0,900,700")
        self.run_xtm("-o", "work1")
        result = self.run_xtm("-k", "work1", env=self.refusing("kill-session"))
        self.assertEqual(1, result.returncode)
        self.assertIn("work1", result.stderr)
        self.assertNotIn("Traceback", result.stderr)

    def test_a_refused_detach_is_reported_per_session(self):
        self.run_xtm("-s", "work1", "0,0,900,700")
        self.run_xtm("-o", "work1")
        result = self.run_xtm("-t", "work1", env=self.refusing("detach-client"))
        self.assertEqual(1, result.returncode)
        self.assertIn("work1", result.stderr)
        self.assertNotIn("Traceback", result.stderr)

    def test_a_switch_that_cannot_detach_the_old_windows_still_switches(self):
        """Leaving a window open is cosmetic: warn, name it, and switch."""
        self.write_config("""\
            profiles:
              desk:
                prefix: desk-
                sessions:
                  a: {x: 0, y: 0, width: 800, height: 600}
              lab:
                prefix: lab-
                sessions:
                  b: {x: 0, y: 0, width: 800, height: 600}
            """)
        self.run_xtm("desk")
        result = self.run_xtm("lab", env=self.refusing("detach-client"))
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertIn("xtm: warning: Could not detach: desk-a\n", result.stderr)
        self.assertEqual("lab\n", self.run_xtm("--current-profile", "--quiet").stdout)


if __name__ == "__main__":
    unittest.main()
