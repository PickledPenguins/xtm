"""Command helpers exercised in-process with a scripted run()."""

import builtins
import sys
import unittest

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
        self.assertEqual(xtm.session_status("a", {"a": True}), "attached")
        self.assertEqual(xtm.session_status("a", {"a": False}), "detached")
        self.assertEqual(xtm.session_status("a", {}), "not running")

    def test_count_unnamed_open(self):
        profile = {"sessions": {"named": {}}}
        live = {"named": True, "stray1": True, "stray2": True, "detached": False}
        self.assertEqual(xtm.count_unnamed_open(profile, live), 2)


class TestBulkFailureHandling(unittest.TestCase):
    """Bulk commands must keep going and then report a non-zero status."""

    def setUp(self):
        self.args = xtm.build_parser().parse_args([])
        self.config = {"profiles": {"p": {
            "stack": dict(xtm.DEFAULT_STACK),
            "sessions": {"a": {"x": 0, "y": 0, "width": 8, "height": 6},
                         "b": {"x": 1, "y": 1, "width": 8, "height": 6}}}}}
        self.saved = (xtm.tmux_list_sessions, xtm.place_window)
        self.addCleanup(self._restore)
        self.placed = []

    def _restore(self):
        xtm.tmux_list_sessions, xtm.place_window = self.saved

    def _fail_on(self, failing):
        def place(session, geometry, xterm_pid=None):
            if session in failing:
                raise xtm.XtmError("simulated placement failure")
            self.placed.append(session)
        return place

    def test_reset_continues_past_a_failure(self):
        xtm.tmux_list_sessions = lambda: {"a": True, "b": True}
        xtm.place_window = self._fail_on({"a"})
        self.assertEqual(xtm.cmd_reset(self.args, self.config, "p"),
                         xtm.EXIT_ERROR)
        self.assertEqual(self.placed, ["b"])

    def test_reset_all_reports_stray_failures(self):
        xtm.tmux_list_sessions = lambda: {"a": True, "b": True, "stray": True}
        xtm.place_window = self._fail_on({"stray"})
        self.assertEqual(xtm.cmd_reset_all(self.args, self.config, "p"),
                         xtm.EXIT_ERROR)
        self.assertEqual(sorted(self.placed), ["a", "b"])

    def test_reset_all_succeeds_when_everything_places(self):
        xtm.tmux_list_sessions = lambda: {"a": True, "b": True, "stray": True}
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
