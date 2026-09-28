"""Scale budgets for the paths whose cost grows with the config.

These count OPERATIONS, not seconds. A wall-clock assertion is unreliable on
a loaded CI runner and, worse, stops meaning anything once the code is fast
enough that the timer resolution dominates -- an earlier version of this file
skipped itself for exactly that reason, which guards nothing.

Counting instead makes the budget exact and deterministic: resolving N
windows against a profile of N slots must cost O(N) name resolutions, not
O(N^2). That is the difference the fix actually made, so that is what is
asserted.
"""

import unittest

from tests.support import (
    xtm,
)


def make_profile(slot_count, prefix="p-"):
    """A profile with `slot_count` slots, named so ordering is stable."""
    return {
        "prefix": prefix,
        "stack": dict(xtm.DEFAULT_STACK),
        "sessions": dict(
            ("s%05d" % i, {"x": 0, "y": 0, "width": 800, "height": 600})
            for i in range(slot_count)),
    }


class CountingMixin(unittest.TestCase):
    """Count how often xtm resolves a slot name to a session name.

    That call is the unit of work in every profile-wide loop, so its count
    is a faithful proxy for the shape of the algorithm around it, and it
    cannot be perturbed by machine load the way a timer can.
    """

    def setUp(self):
        self.calls = 0
        original = xtm.resolve_session_name

        def counting(profile, slot):
            self.calls += 1
            return original(profile, slot)

        xtm.resolve_session_name = counting
        self.addCleanup(setattr, xtm, "resolve_session_name", original)

    def assert_at_most(self, budget, label):
        self.assertLessEqual(
            self.calls, budget,
            "%s cost %d name resolutions, budget %d. A per-item rescan of the "
            "whole profile is the usual cause; build the index once."
            % (label, self.calls, budget))


class TestProfileScaling(CountingMixin):

    N = 400

    def test_building_the_slot_index_is_linear(self):
        xtm.session_slot_index(make_profile(self.N))
        self.assert_at_most(self.N, "session_slot_index")

    def test_resolving_every_session_to_its_slot_is_linear(self):
        """The --update-profile shape: many windows, one profile.

        The regression test for the real defect. Resolving each captured
        window with slot_for_session() rebuilt the entire slot list every
        time, so capturing N windows against N slots cost N^2 resolutions.
        Building the index once costs N.
        """
        profile = make_profile(self.N)
        index = xtm.session_slot_index(profile)
        for i in range(self.N):
            index.get("p-s%05d" % i)
        self.assert_at_most(self.N, "resolving %d sessions" % (self.N,))

    def test_update_profile_does_not_rescan_per_window(self):
        """The same budget, asserted through the real command.

        Exercises cmd_update_profile end to end with a full screen of
        windows, so the budget cannot be satisfied by a helper while the
        caller still does the quadratic thing.
        """
        profile = make_profile(self.N)
        config = {"profiles": {"cap": profile}}
        xtm.validate_profile(config, "cap")

        layout = dict(("p-s%05d" % i,
                       {"x": i, "y": i, "width": 800, "height": 600})
                      for i in range(self.N))

        self.addCleanup(setattr, xtm, "require_geometry_tool",
                        xtm.require_geometry_tool)
        self.addCleanup(setattr, xtm, "capture_layout", xtm.capture_layout)
        self.addCleanup(setattr, xtm, "save_config", xtm.save_config)
        xtm.require_geometry_tool = lambda: "xdotool"
        xtm.capture_layout = lambda tool: layout
        xtm.save_config = lambda config: None

        class Args(object):
            capture_new = False

        self.calls = 0        # ignore the setup above
        status = xtm.cmd_update_profile(Args(), config, "cap")
        self.assertEqual(status, xtm.EXIT_OK)
        # Every captured window was recognized and updated in place.
        self.assertEqual(profile["sessions"]["s00000"]["x"], 0)
        self.assertEqual(profile["sessions"]["s00399"]["x"], 399)
        # One index build, plus whatever reporting costs. Comfortably linear,
        # and roughly 1/100th of the N^2 = 160,000 the old code would spend.
        self.assert_at_most(4 * self.N, "cmd_update_profile over %d windows"
                            % (self.N,))

    def test_validation_is_linear(self):
        config = {"profiles": {"p": make_profile(self.N)}}
        xtm.validate_profile(config, "p")
        # validate_profile walks the slots twice: once to validate each
        # entry, once to check for two slots resolving to one session.
        self.assert_at_most(3 * self.N, "validate_profile")

    def test_stray_attribution_is_linear_in_total_slots(self):
        """--list attributes each stray to the profile that owns it."""
        profiles = dict(("p%04d" % i, make_profile(4, prefix="p%04d-" % i))
                        for i in range(100))
        profiles["default"] = make_profile(0)
        config = {"profiles": profiles}
        xtm.validate_profile(config, "default")

        self.addCleanup(setattr, xtm, "tmux_list_sessions",
                        xtm.tmux_list_sessions)
        self.addCleanup(setattr, xtm, "detect_geometry_tool",
                        xtm.detect_geometry_tool)
        xtm.tmux_list_sessions = lambda: dict(
            ("p%04d-s%05d" % (i, 0), True) for i in range(100))
        xtm.detect_geometry_tool = lambda: None

        self.calls = 0
        xtm.collect_status(config, "default")
        # 100 profiles x 4 slots = 400 slots in total; the walk should touch
        # each a small constant number of times, not once per stray.
        self.assert_at_most(4 * 400, "stray attribution")


class TestLargeConfigsStayCorrect(unittest.TestCase):
    """Size must change cost, never behavior."""

    def test_a_large_profile_validates_and_resolves(self):
        config = {"profiles": {"big": make_profile(2000)}}
        xtm.validate_profile(config, "big")
        index = xtm.session_slot_index(config["profiles"]["big"])
        self.assertEqual(len(index), 2000)
        self.assertEqual(index["p-s00000"], "s00000")
        self.assertEqual(index["p-s01999"], "s01999")

    def test_a_large_config_round_trips(self):
        config = {"profiles": {"big": make_profile(300)}}
        self.assertEqual(xtm.load_yaml(xtm.dump_yaml(config)), config)

    def test_session_slot_index_matches_the_scan_it_replaced(self):
        """Including on a duplicate, where first-match must still win."""
        profile = make_profile(50)
        # Two slots deliberately resolving to one session. validate_profile
        # rejects this, but the index must behave like the old linear scan
        # rather than silently preferring the later slot.
        profile["sessions"]["zz-dupe"] = {
            "x": 0, "y": 0, "width": 800, "height": 600, "session": "p-s00000"}
        index = xtm.session_slot_index(profile)
        self.assertEqual(index["p-s00000"], "s00000")
        for _slot, session in xtm.profile_slots(profile):
            self.assertIn(session, index)
