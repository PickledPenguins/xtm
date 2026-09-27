"""Window geometry: placement sequences, read-back, frame compensation."""

import logging
import os
import pty
import select
import time
import unittest

from tests.support import (
    FakeResult,
    RunPatchMixin,
    xtm,
)


class TestStackSlot(unittest.TestCase):

    STACK = {"x": 40, "y": 50, "width": 900, "height": 650,
             "offset_x": 30, "offset_y": 20}

    def test_first_slot_is_the_base_position(self):
        self.assertEqual(xtm.stack_slot(self.STACK, 0),
                         {"x": 40, "y": 50, "width": 900, "height": 650})

    def test_offsets_accumulate(self):
        self.assertEqual(xtm.stack_slot(self.STACK, 3),
                         {"x": 130, "y": 110, "width": 900, "height": 650})

    def test_zero_offsets_stack_in_place(self):
        stack = dict(self.STACK, offset_x=0, offset_y=0)
        self.assertEqual(xtm.stack_slot(stack, 5)["x"], 40)


class TestPlacementSequences(unittest.TestCase):
    """xtm's escape sequences are the whole placement mechanism, so they
    are asserted byte for byte against a genuine pty."""

    def setUp(self):
        self.master_fd, self.slave_fd = pty.openpty()
        self.slave_path = os.ttyname(self.slave_fd)
        self._original = xtm.tmux_client_tty_for_pid
        xtm.tmux_client_tty_for_pid = lambda session, pid: self.slave_path
        self.addCleanup(self._restore)

    def _restore(self):
        xtm.tmux_client_tty_for_pid = self._original
        os.close(self.master_fd)
        os.close(self.slave_fd)

    def read_all(self):
        time.sleep(0.05)
        chunks = []
        while True:
            ready, _, _ = select.select([self.master_fd], [], [], 0.1)
            if not ready:
                break
            chunks.append(os.read(self.master_fd, 4096))
        return b"".join(chunks).decode("ascii", "replace")

    def test_resize_precedes_move(self):
        xtm.place_window("s", {"x": 100, "y": 200, "width": 900, "height": 700})
        self.assertEqual(self.read_all(), "\x1b[4;700;900t\x1b[3;100;200t")

    def test_float_geometry_is_coerced_to_int(self):
        xtm.place_window("s", {"x": 10.0, "y": 20.9, "width": 800.5,
                               "height": 600.0})
        self.assertEqual(self.read_all(), "\x1b[4;600;800t\x1b[3;10;20t")

    def test_negative_coordinates(self):
        xtm.place_window("s", {"x": -1920, "y": -50, "width": 800, "height": 600})
        self.assertIn("\x1b[3;-1920;-50t", self.read_all())

    def test_dry_run_writes_nothing(self):
        xtm.CONTEXT.dry_run = True
        try:
            xtm.place_window("s", {"x": 1, "y": 2, "width": 3, "height": 4})
        finally:
            xtm.CONTEXT.dry_run = False
        self.assertEqual(self.read_all(), "")

    def test_missing_client_is_an_error(self):
        xtm.tmux_client_tty_for_pid = lambda session, pid: None
        with self.assertRaises(xtm.XtmError):
            xtm.place_window("s", {"x": 1, "y": 2, "width": 3, "height": 4})

    def test_unwritable_tty_is_an_error(self):
        xtm.tmux_client_tty_for_pid = lambda session, pid: "/nonexistent/tty"
        with self.assertRaises(xtm.XtmError):
            xtm.place_window("s", {"x": 1, "y": 2, "width": 3, "height": 4})


class TestFrameCompensation(unittest.TestCase):
    """Captured positions must be corrected for window decorations, or a
    capture/restore cycle walks every window down and right."""

    def setUp(self):
        # The context manager restores every RunContext field, so this class
        # only has to save the module functions it monkeypatches.
        context = xtm.scoped_context()
        context.__enter__()
        self.addCleanup(context.__exit__, None, None, None)
        self.saved = (xtm._read_raw_geometry, xtm.find_window_id,
                      xtm.get_frame_extents)
        xtm._read_raw_geometry = lambda s, t: {"x": 105, "y": 133,
                                               "width": 900, "height": 700}
        xtm.find_window_id = lambda s, t: "0x100"
        self.addCleanup(self._restore)

    def _restore(self):
        (xtm._read_raw_geometry, xtm.find_window_id,
         xtm.get_frame_extents) = self.saved

    def test_extents_are_subtracted(self):
        xtm.get_frame_extents = lambda win_id: (5, 5, 33, 5)
        xtm.CONTEXT.frame_compensation = True
        self.assertEqual(xtm.get_window_geometry("s", "xdotool"),
                         {"x": 100, "y": 100, "width": 900, "height": 700})

    def test_missing_extents_leaves_position_alone(self):
        xtm.get_frame_extents = lambda win_id: None
        xtm.CONTEXT.frame_compensation = True
        self.assertEqual(xtm.get_window_geometry("s", "xdotool")["x"], 105)

    def test_compensation_can_be_disabled_globally(self):
        xtm.get_frame_extents = lambda win_id: (5, 5, 33, 5)
        xtm.CONTEXT.frame_compensation = False
        self.assertEqual(xtm.get_window_geometry("s", "xdotool")["y"], 133)

    def test_compensation_can_be_disabled_per_call(self):
        xtm.get_frame_extents = lambda win_id: (5, 5, 33, 5)
        xtm.CONTEXT.frame_compensation = True
        self.assertEqual(
            xtm.get_window_geometry("s", "xdotool", compensate=False)["y"], 133)

    def test_missing_window_returns_none(self):
        xtm._read_raw_geometry = lambda s, t: None
        self.assertIsNone(xtm.get_window_geometry("s", "xdotool"))


class TestIdentifierRecovery(unittest.TestCase):

    def test_instance_name_preferred(self):
        self.assertEqual(
            xtm._session_from_identifiers("some other title", "xtm-work1"), "work1")

    def test_title_used_as_fallback(self):
        self.assertEqual(xtm._session_from_identifiers("xtm:logs", ""), "logs")

    def test_unrelated_window_ignored(self):
        self.assertIsNone(xtm._session_from_identifiers("Firefox", "Navigator"))

    def test_invalid_session_name_ignored(self):
        self.assertIsNone(xtm._session_from_identifiers("xtm:bad name", ""))


class TestVerifyPlacement(unittest.TestCase):

    REQUESTED = {"x": 100, "y": 200, "width": 900, "height": 700}

    def setUp(self):
        self.saved = (xtm.detect_geometry_tool, xtm.get_window_geometry)
        self.addCleanup(self._restore)
        self.records = []
        handler = logging.Handler()
        handler.emit = lambda record: self.records.append(record)
        xtm.logger.addHandler(handler)
        self.addCleanup(xtm.logger.removeHandler, handler)
        # Set the level here rather than relying on it. xtm.logger only gets
        # a level from setup_logging(), so without this the logger inherits
        # WARNING from the root and every INFO record is dropped before it
        # reaches the handler above -- which made this class quietly depend
        # on some earlier test having called setup_logging() first.
        saved_level = xtm.logger.level
        xtm.logger.setLevel(logging.DEBUG)
        self.addCleanup(xtm.logger.setLevel, saved_level)

    def _restore(self):
        xtm.detect_geometry_tool, xtm.get_window_geometry = self.saved

    def messages(self):
        return " ".join(r.getMessage() for r in self.records)

    def test_no_tool_warns(self):
        xtm.detect_geometry_tool = lambda: None
        xtm.verify_placement("s", self.REQUESTED, settle_seconds=0)
        self.assertIn("skipping verification", self.messages())

    def test_unreadable_window_warns(self):
        xtm.detect_geometry_tool = lambda: "xdotool"
        xtm.get_window_geometry = lambda s, t, compensate=True: None
        xtm.verify_placement("s", self.REQUESTED, settle_seconds=0)
        self.assertIn("could not read back", self.messages())

    def test_exact_match_reports_success(self):
        xtm.detect_geometry_tool = lambda: "xdotool"
        xtm.get_window_geometry = lambda s, t, compensate=True: dict(self.REQUESTED)
        xtm.verify_placement("s", self.REQUESTED, settle_seconds=0)
        self.assertIn("verified at", self.messages())

    def test_mismatch_reports_the_delta(self):
        xtm.detect_geometry_tool = lambda: "xdotool"
        xtm.get_window_geometry = lambda s, t, compensate=True: dict(
            self.REQUESTED, x=110, height=690)
        xtm.verify_placement("s", self.REQUESTED, settle_seconds=0)
        self.assertIn("delta x=+10", self.messages())
        self.assertIn("h=-10", self.messages())


class TestGeometryReadFailures(RunPatchMixin):

    def setUp(self):
        original = xtm.find_window_id
        xtm.find_window_id = lambda session, tool: "0x1"
        self.addCleanup(lambda: setattr(xtm, "find_window_id", original))

    def test_unknown_tool_returns_none(self):
        self.assertIsNone(xtm._read_raw_geometry("s", "nosuchtool"))

    def test_xdotool_command_failure(self):
        self.patch_run(lambda cmd: FakeResult(1))
        self.assertIsNone(xtm._read_raw_geometry("s", "xdotool"))

    def test_xdotool_garbage_output(self):
        self.patch_run(lambda cmd: FakeResult(0, "X=nope\nY=1\n"))
        self.assertIsNone(xtm._read_raw_geometry("s", "xdotool"))

    def test_xdotool_missing_fields(self):
        self.patch_run(lambda cmd: FakeResult(0, "X=1\n"))
        self.assertIsNone(xtm._read_raw_geometry("s", "xdotool"))

    def test_wmctrl_command_failure(self):
        self.patch_run(lambda cmd: FakeResult(1))
        self.assertIsNone(xtm._read_raw_geometry("s", "wmctrl"))

    def test_wmctrl_window_not_listed(self):
        self.patch_run(lambda cmd: FakeResult(0, "0x9 0 1 2 3 4 host title\n"))
        self.assertIsNone(xtm._read_raw_geometry("s", "wmctrl"))

    def test_wmctrl_non_numeric_columns(self):
        self.patch_run(lambda cmd: FakeResult(0, "0x1 0 a b c d host title\n"))
        self.assertIsNone(xtm._read_raw_geometry("s", "wmctrl"))

    def test_xwininfo_command_failure(self):
        self.patch_run(lambda cmd: FakeResult(1))
        self.assertIsNone(xtm._read_raw_geometry("s", "xwininfo"))

    def test_xwininfo_incomplete_output(self):
        self.patch_run(lambda cmd: FakeResult(0, "Absolute upper-left X:  5\n"))
        self.assertIsNone(xtm._read_raw_geometry("s", "xwininfo"))

    def test_missing_window_short_circuits(self):
        xtm.find_window_id = lambda session, tool: None
        self.assertIsNone(xtm._read_raw_geometry("s", "xdotool"))


class TestFindWindowIdFailures(RunPatchMixin):

    def test_unknown_tool(self):
        self.assertIsNone(xtm.find_window_id("s", "nosuchtool"))

    def test_xdotool_no_match(self):
        self.patch_run(lambda cmd: FakeResult(1))
        self.assertIsNone(xtm.find_window_id("s", "xdotool"))

    def test_wmctrl_no_match(self):
        self.patch_run(lambda cmd: FakeResult(0, "0x1 0 other.XTerm host Firefox\n"))
        self.assertIsNone(xtm.find_window_id("s", "wmctrl"))

    def test_wmctrl_failure(self):
        self.patch_run(lambda cmd: FakeResult(1))
        self.assertIsNone(xtm.find_window_id("s", "wmctrl"))

    def test_xwininfo_no_match(self):
        self.patch_run(lambda cmd: FakeResult(1))
        self.assertIsNone(xtm.find_window_id("s", "xwininfo"))

    def test_enumeration_handles_tool_failure(self):
        self.patch_run(lambda cmd: FakeResult(1))
        for tool in ("xdotool", "wmctrl", "xwininfo", "nosuchtool"):
            self.assertEqual(xtm.find_all_xtm_sessions(tool), [], tool)


class TestFrameExtentsFailures(RunPatchMixin):

    def setUp(self):
        # xprop is optional and may not exist on the machine running the
        # tests, so its presence is simulated rather than assumed.
        original = xtm.which_or_none
        xtm.which_or_none = lambda name: "/usr/bin/" + name
        self.addCleanup(lambda: setattr(xtm, "which_or_none", original))

    def test_no_xprop_returns_none(self):
        xtm.which_or_none = lambda name: None
        self.assertIsNone(xtm.get_frame_extents("0x1"))
        self.assertEqual(xtm._xdotool_window_classname("0x1"), "")

    def test_property_absent_returns_none(self):
        self.patch_run(lambda cmd: FakeResult(1))
        self.assertIsNone(xtm.get_frame_extents("0x1"))

    def test_unparsable_property_returns_none(self):
        self.patch_run(lambda cmd: FakeResult(0, "_NET_FRAME_EXTENTS: nonsense"))
        self.assertIsNone(xtm.get_frame_extents("0x1"))

    def test_parses_property(self):
        self.patch_run(lambda cmd: FakeResult(
            0, "_NET_FRAME_EXTENTS(CARDINAL) = 1, 2, 3, 4\n"))
        self.assertEqual(xtm.get_frame_extents("0x1"), (1, 2, 3, 4))

    def test_classname_parsing(self):
        self.patch_run(lambda cmd: FakeResult(
            0, 'WM_CLASS(STRING) = "xtm-work1", "XTerm"\n'))
        self.assertEqual(xtm._xdotool_window_classname("0x1"), "xtm-work1")


class TestClientSelection(RunPatchMixin):

    def test_prefers_own_descendant_client(self):
        self.patch_run(lambda cmd: FakeResult(
            0, "/dev/pts/1:999999999\n/dev/pts/2:%d\n" % (os.getpid(),)))
        self.assertEqual(xtm.tmux_client_tty_for_pid("s", os.getppid()),
                         "/dev/pts/2")

    def test_falls_back_to_first_client(self):
        self.patch_run(lambda cmd: FakeResult(0, "/dev/pts/1:123\n/dev/pts/2:456\n"))
        self.assertEqual(xtm.tmux_client_tty_for_pid("s", 999999999), "/dev/pts/1")

    def test_no_pid_uses_first_client(self):
        self.patch_run(lambda cmd: FakeResult(0, "/dev/pts/7:123\n"))
        self.assertEqual(xtm.tmux_client_tty_for_pid("s", None), "/dev/pts/7")

    def test_unparsable_pid_is_tolerated(self):
        self.patch_run(lambda cmd: FakeResult(0, "/dev/pts/7:notanumber\n"))
        self.assertEqual(xtm.tmux_client_tty_for_pid("s", 1), "/dev/pts/7")

    def test_no_clients_returns_none(self):
        self.patch_run(lambda cmd: FakeResult(0, "\n"))
        self.assertIsNone(xtm.tmux_client_tty_for_pid("s", None))

    def test_command_failure_returns_none(self):
        self.patch_run(lambda cmd: FakeResult(1))
        self.assertIsNone(xtm.tmux_client_tty_for_pid("s", None))
        self.assertIsNone(xtm.tmux_client_tty("s"))

    def test_client_tty_skips_blank_lines(self):
        self.patch_run(lambda cmd: FakeResult(0, "\n\n/dev/pts/3\n"))
        self.assertEqual(xtm.tmux_client_tty("s"), "/dev/pts/3")

    def test_wait_for_client_tty_times_out(self):
        self.patch_run(lambda cmd: FakeResult(1))
        self.assertIsNone(xtm.wait_for_client_tty("s", None, timeout=0.3))

    def test_session_list_parsing(self):
        self.patch_run(lambda cmd: FakeResult(0, "a:1\nb:0\nc:2\n\n"))
        self.assertEqual(xtm.tmux_list_sessions(),
                         {"a": True, "b": False, "c": True})


class TestProcessAncestry(unittest.TestCase):

    def test_ppid_of_current_process(self):
        self.assertEqual(xtm._read_proc_ppid(os.getpid()), os.getppid())

    def test_ppid_of_missing_process(self):
        self.assertIsNone(xtm._read_proc_ppid(999999999))

    def test_descendant_of_self(self):
        self.assertTrue(xtm._is_descendant_of(os.getpid(), os.getpid()))

    def test_descendant_of_parent(self):
        self.assertTrue(xtm._is_descendant_of(os.getpid(), os.getppid()))

    def test_not_a_descendant(self):
        self.assertFalse(xtm._is_descendant_of(os.getpid(), 999999999))

    def test_depth_limit_stops_the_walk(self):
        self.assertFalse(xtm._is_descendant_of(os.getpid(), 1, max_depth=1))
