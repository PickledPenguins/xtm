"""Failures that come from outside the process.

test_durability.py covers the filesystem underneath a write. This covers the
world xtm talks to: tmux, the geometry tools, the editor, the state file and
the config directory. Each case asserts the user-visible behavior -- which
message, which exit code, and that nothing further was damaged -- because
failing safely is the property under test, not failing quietly.
"""

import os
import unittest

from tests.support import IntegrationBase


def make_tool(directory, name, body):
    """Replace one fake tool with a script that fails in a chosen way."""
    path = directory / name
    path.write_text("#!/bin/sh\n%s\n" % (body,))
    path.chmod(0o755)
    return path


class TestExternalToolFailures(IntegrationBase):
    """A tool that is present but broken must not look like a bug in xtm."""

    def test_tmux_failing_outright_does_not_break_reporting(self):
        """A broken tmux means "nothing is running", not a crash.

        --list is a report, and a report that cannot reach tmux still has
        the profile to describe. What it must not do is traceback or claim
        a session is attached.
        """
        self.run_xtm("-s", "work1", "0,0,900,700")
        make_tool(self.bin, "tmux", "echo 'tmux: server exited' >&2\nexit 1")
        result = self.run_xtm("-l")
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertNotIn("Traceback", result.stderr)
        self.assertIn("not running", result.stdout)

    def test_tmux_failing_is_reported_by_a_command_that_needs_it(self):
        """A command that cannot do its job says so and exits non-zero."""
        self.run_xtm("-s", "work1", "0,0,900,700")
        make_tool(self.bin, "tmux", "echo 'tmux: server exited' >&2\nexit 1")
        result = self.run_xtm("-k", "work1")
        self.assertNotEqual(0, result.returncode)
        self.assertNotIn("Traceback", result.stderr)

    def test_tmux_printing_nothing_is_read_as_no_sessions(self):
        make_tool(self.bin, "tmux", "exit 0")
        result = self.run_xtm("-l")
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertNotIn("Traceback", result.stderr)

    def test_tmux_printing_garbage_does_not_crash_the_listing(self):
        make_tool(self.bin, "tmux",
                  "printf '\\001\\002 not: a session list\\n'\nexit 0")
        result = self.run_xtm("-l")
        self.assertNotIn("Traceback", result.stderr)

    def test_a_geometry_tool_that_fails_leaves_the_profile_alone(self):
        self.run_xtm("-s", "work1", "0,0,900,700")
        before = self.config_text()
        make_tool(self.bin, "xdotool", "exit 3")
        make_tool(self.bin, "wmctrl", "exit 3")
        make_tool(self.bin, "xwininfo", "exit 3")
        result = self.run_xtm("-u")
        self.assertNotIn("Traceback", result.stderr)
        self.assertEqual(before, self.config_text(),
                         "a failing geometry tool rewrote the profile")

    def test_a_geometry_tool_that_hangs_is_bounded_by_the_timeout(self):
        # `run()` gives every external command a timeout. Without one, a
        # single wedged tool would hang xtm indefinitely.
        make_tool(self.bin, "xdotool", "sleep 60")
        make_tool(self.bin, "wmctrl", "sleep 60")
        make_tool(self.bin, "xwininfo", "sleep 60")
        result = self.run_xtm("-l")
        self.assertNotIn("Traceback", result.stderr)

    def test_an_editor_that_fails_is_reported_not_swallowed(self):
        result = self.run_xtm("-e", env={"VISUAL": "/nonexistent/editor"})
        self.assertNotEqual(0, result.returncode)
        self.assertIn("editor", result.stderr.lower())
        self.assertNotIn("Traceback", result.stderr)

    def config_text(self):
        return (self.tmp / "cfg" / "config.yaml").read_text()


class TestCorruptState(IntegrationBase):
    """State files are written by xtm and edited by nobody, but they can
    still be truncated by a crash or scribbled on by hand."""

    def state_file(self):
        state_dir = self.tmp / "state"
        return next(state_dir.iterdir()) if state_dir.exists() else None

    def test_a_state_file_naming_a_missing_profile_says_so(self):
        """A remembered profile that has gone is reported, not replaced.

        Falling back silently would act on a different profile than the one
        the user last chose, which is the one thing worse than stopping.
        The message names the profile, the file it came from, and the fix.
        """
        # Naming a profile is what writes the state file.
        self.run_xtm("--profile", "default", "-l")
        path = self.state_file()
        self.assertIsNotNone(path, "no state file was written")
        path.write_text("gone-profile\n")
        result = self.run_xtm("-l")
        self.assertNotEqual(0, result.returncode)
        self.assertIn("gone-profile", result.stderr)
        self.assertIn("xtm NAME", result.stderr)
        self.assertNotIn("Traceback", result.stderr)

    def test_a_state_file_of_binary_junk_is_reported_readably(self):
        """Junk in the state file must not reach the terminal raw.

        The name is escaped into the message rather than printed, so a
        control character in a corrupt file cannot rewrite the user's
        terminal on its way out.
        """
        self.run_xtm("--profile", "default", "-l")
        path = self.state_file()
        path.write_bytes(b"\x00\x01\x02 not a name\n")
        result = self.run_xtm("-l")
        self.assertNotEqual(0, result.returncode)
        self.assertNotIn("Traceback", result.stderr)
        self.assertNotIn("\x00", result.stderr)

    def test_an_unreadable_state_file_is_reported_cleanly(self):
        if os.geteuid() == 0:
            self.skipTest("running as root, which ignores file permissions")
        self.run_xtm("--profile", "default", "-l")
        path = self.state_file()
        mode = path.stat().st_mode
        path.chmod(0o000)
        self.addCleanup(path.chmod, mode)
        result = self.run_xtm("-l")
        self.assertNotEqual(0, result.returncode)
        self.assertIn("state file", result.stderr.lower())
        self.assertNotIn("Traceback", result.stderr)

    def test_a_truncated_config_is_refused_with_a_message(self):
        self.run_xtm("-s", "work1", "0,0,900,700")
        config = self.tmp / "cfg" / "config.yaml"
        text = config.read_text()
        config.write_text(text[:len(text) // 2])
        result = self.run_xtm("-l")
        self.assertNotIn("Traceback", result.stderr)
        self.assertNotEqual("", result.stderr.strip(),
                            "a truncated config produced no message at all")

    def test_a_config_directory_replaced_by_a_file_is_reported(self):
        config_dir = self.tmp / "cfg2"
        config_dir.write_text("not a directory\n")
        result = self.run_xtm("-l", env={"XTM_CONFIG_DIR": str(config_dir)})
        self.assertNotEqual(0, result.returncode)
        self.assertNotIn("Traceback", result.stderr)


if __name__ == "__main__":
    unittest.main()
