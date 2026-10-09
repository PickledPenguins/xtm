"""Command helpers exercised in-process with a scripted run()."""

import builtins
import os
import shutil
import sys
import tempfile
import unittest
from unittest import mock

from tests.support import (
    FakeResult,
    RunPatchMixin,
    xtm,
)


class TestCaptureAndStatusHelpers(unittest.TestCase):

    def test_capture_layout_skips_unreadable_windows(self):
        saved = (xtm.find_all_xtm_sessions, xtm.get_window_geometry)
        xtm.find_all_xtm_sessions = lambda tool: ["good", "bad"]
        xtm.get_window_geometry = lambda s, t: (
            None if s == "bad" else {"x": 1, "y": 2, "width": 3, "height": 4})
        try:
            layout = xtm.capture_layout("xdotool")
        finally:
            xtm.find_all_xtm_sessions, xtm.get_window_geometry = saved
        self.assertEqual(list(layout), ["good"])

    def test_session_status(self):
        own = {"a": ["/dev/pts/1"]}
        self.assertEqual(xtm.session_status("a", {"a": True}, own), "attached")
        self.assertEqual(xtm.session_status("a", {"a": True}, {}),
                         "attached elsewhere")
        self.assertEqual(xtm.session_status("a", {"a": False}, {}), "detached")
        self.assertEqual(xtm.session_status("a", {}, {}), "not running")

    def test_count_unnamed_open_counts_xtm_windows_only(self):
        profile = {"sessions": {"named": {}}}
        windows = {"named": ["/dev/pts/1"], "stray1": ["/dev/pts/2"],
                   "stray2": ["/dev/pts/3"]}
        self.assertEqual(xtm.count_unnamed_open(profile, windows), 2)


class TestBulkFailureHandling(unittest.TestCase):
    """Bulk commands must keep going and then report a non-zero status."""

    def setUp(self):
        self.args = xtm.build_parser().parse_args([])
        self.config = {"profiles": {"p": {
            "stack": dict(xtm.DEFAULT_STACK),
            "sessions": {"a": {"x": 0, "y": 0, "width": 8, "height": 6},
                         "b": {"x": 1, "y": 1, "width": 8, "height": 6}}}}}
        self.saved = (xtm.xtm_windows, xtm.place_window)
        self.addCleanup(self._restore)
        self.placed = []

    def _restore(self):
        xtm.xtm_windows, xtm.place_window = self.saved

    def windows(self, *sessions):
        """Every named session shown in an xtm window on its own tty."""
        return lambda clients=None: dict(
            (s, ["/dev/pts/%d" % (i,)]) for i, s in enumerate(sessions))

    def _fail_on(self, failing):
        def place(session, geometry, tty):
            if session in failing:
                raise xtm.XtmError("simulated placement failure")
            self.placed.append(session)
        return place

    def test_reset_continues_past_a_failure(self):
        xtm.xtm_windows = self.windows("a", "b")
        xtm.place_window = self._fail_on({"a"})
        self.assertEqual(xtm.cmd_reset(self.args, self.config, "p"),
                         xtm.EXIT_ERROR)
        self.assertEqual(self.placed, ["b"])

    def test_reset_all_reports_stray_failures(self):
        xtm.xtm_windows = self.windows("a", "b", "stray")
        xtm.place_window = self._fail_on({"stray"})
        self.assertEqual(xtm.cmd_reset_all(self.args, self.config, "p"),
                         xtm.EXIT_ERROR)
        self.assertEqual(sorted(self.placed), ["a", "b"])

    def test_reset_all_succeeds_when_everything_places(self):
        xtm.xtm_windows = self.windows("a", "b", "stray")
        xtm.place_window = self._fail_on(set())
        self.assertEqual(xtm.cmd_reset_all(self.args, self.config, "p"),
                         xtm.EXIT_OK)
        self.assertEqual(sorted(self.placed), ["a", "b", "stray"])


class TestFocusAndConfirm(RunPatchMixin):

    def setUp(self):
        original = xtm.find_window_id
        xtm.find_window_id = lambda session, tool: "0x1"
        self.addCleanup(lambda: setattr(xtm, "find_window_id", original))
        # Restores the whole RunContext, so nothing this class sets can
        # leak into the next test.
        context = xtm.scoped_context()
        context.__enter__()
        self.addCleanup(context.__exit__, None, None, None)

    def test_focus_requires_an_acting_tool(self):
        with self.assertRaises(xtm.XtmError):
            xtm.focus_window("s", "xwininfo")

    def test_focus_dry_run_does_nothing(self):
        xtm.CONTEXT.dry_run = True
        self.patch_run(lambda cmd: self.fail("no command should run"))
        self.assertTrue(xtm.focus_window("s", "xdotool"))

    def test_focus_reports_command_failure(self):
        self.patch_run(lambda cmd: FakeResult(1))
        self.assertFalse(xtm.focus_window("s", "xdotool"))
        self.assertFalse(xtm.focus_window("s", "wmctrl"))

    def test_focus_missing_window(self):
        xtm.find_window_id = lambda session, tool: None
        self.assertFalse(xtm.focus_window("s", "xdotool"))

    def test_confirm_shortcuts(self):
        self.assertTrue(xtm.confirm("q", True))
        xtm.CONTEXT.dry_run = True
        self.assertTrue(xtm.confirm("q", False))
        xtm.CONTEXT.dry_run = False

    def test_confirm_reads_the_answer(self):
        original = builtins.input
        stdin_original = sys.stdin.isatty
        try:
            sys.stdin.isatty = lambda: True
            builtins.input = lambda prompt="": "y"
            self.assertTrue(xtm.confirm("q", False))
            builtins.input = lambda prompt="": "n"
            self.assertFalse(xtm.confirm("q", False))
            builtins.input = lambda prompt="": ""
            self.assertFalse(xtm.confirm("q", False))

            def raise_eof(prompt=""):
                raise EOFError()

            builtins.input = raise_eof
            self.assertFalse(xtm.confirm("q", False))
        finally:
            builtins.input = original
            sys.stdin.isatty = stdin_original

    def test_confirm_without_a_terminal_proceeds(self):
        stdin_original = sys.stdin.isatty
        try:
            sys.stdin.isatty = lambda: False
            self.assertTrue(xtm.confirm("q", False))
        finally:
            sys.stdin.isatty = stdin_original


class TestRunSetup(unittest.TestCase):
    """run_setup's warnings: none of them stops the session from being used."""

    def setUp(self):
        context = xtm.scoped_context()
        context.__enter__()
        self.addCleanup(context.__exit__, None, None, None)
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp)
        self.out = os.path.join(self.tmp, "out")

    def setup(self, line, cwd=None):
        with self.assertLogs(xtm.logger, "DEBUG") as logs:
            xtm.run_setup("s", {"setup": line, "cwd": cwd}, "slot", None)
        return "\n".join(logs.output)

    def test_a_missing_cwd_runs_it_in_the_home_directory(self):
        os.environ["HOME"], home = self.tmp, os.environ["HOME"]
        self.addCleanup(os.environ.__setitem__, "HOME", home)
        self.setup('printf "%%s %%s" "$PWD" "$XTM_PROFILE" > %s' % self.out,
                   cwd=os.path.join(self.tmp, "gone"))
        with open(self.out) as f:
            self.assertEqual(f.read(), "%s " % self.tmp)

    def test_a_setup_that_runs_too_long_is_stopped(self):
        original = xtm.SETUP_TIMEOUT_SECONDS
        xtm.SETUP_TIMEOUT_SECONDS = 0.2
        self.addCleanup(setattr, xtm, "SETUP_TIMEOUT_SECONDS", original)
        self.assertIn("WARNING:%s:s: setup did not finish within 0s and was stopped."
                      % xtm.LOGGER_NAME,
                      self.setup("exec sleep 5", cwd=self.tmp))

    def test_a_setup_that_cannot_start_is_a_warning(self):
        with mock.patch.object(xtm.subprocess, "run", side_effect=OSError("no sh")):
            self.assertIn("WARNING:%s:s: could not run setup: no sh" % xtm.LOGGER_NAME,
                          self.setup("true", cwd=self.tmp))
