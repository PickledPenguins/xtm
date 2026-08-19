"""Cost budgets: how much work discovery does per window."""

import subprocess
import unittest

from tests.support import (
    xtm,
)


class TestEnumerationCost(unittest.TestCase):
    """Pin the number of subprocess calls each geometry tool needs.

    Enumeration cost is documented in the README, and it is the reason
    the tools are probed in the order they are. Recording it here means a
    refactor that quietly makes discovery O(N) in extra processes shows
    up as a failing test rather than as a slow --update-profile.
    """

    def setUp(self):
        self.calls = []
        self.original_run = xtm.run
        self.original_which = xtm.which_or_none

        def fake_run(cmd, **kwargs):
            self.calls.append(list(cmd))
            return self._respond(list(cmd))

        xtm.run = fake_run
        xtm.which_or_none = lambda name: "/usr/bin/" + name
        self.addCleanup(setattr, xtm, "run", self.original_run)
        self.addCleanup(setattr, xtm, "which_or_none", self.original_which)
        self.window_count = 3

    def _result(self, stdout, returncode=0):
        return subprocess.CompletedProcess([], returncode, stdout, "")

    def _respond(self, cmd):
        ids = ["0x%d" % (100 + i) for i in range(self.window_count)]
        if cmd[0] == "xdotool" and cmd[1] == "search":
            # Only the --classname search matches; the --name search is
            # the compatibility path for windows from older versions.
            if "--classname" in cmd:
                return self._result("\n".join(ids) + "\n")
            return self._result("")
        if cmd[0] == "xdotool" and cmd[1] == "getwindowname":
            return self._result("xtm:s%s" % cmd[2][-1])
        if cmd[0] == "xprop" and cmd[-1] == "WM_CLASS":
            return self._result('WM_CLASS(STRING) = "xtm-s%s", "XTerm"'
                                % cmd[2][-1])
        if cmd[0] == "wmctrl":
            lines = ["%s 0 xtm-s%d.XTerm host xtm:s%d" % (ids[i], i, i)
                     for i in range(self.window_count)]
            return self._result("\n".join(lines) + "\n")
        if cmd[0] == "xwininfo":
            lines = ['     %s "xtm:s%d" ("xtm-s%d" "XTerm")  900x650+0+0  +0+0'
                     % (ids[i], i, i) for i in range(self.window_count)]
            return self._result("\n".join(lines) + "\n")
        return self._result("")

    def test_wmctrl_enumerates_in_one_call(self):
        found = xtm.find_all_xtm_sessions("wmctrl")
        self.assertEqual(found, ["s0", "s1", "s2"])
        self.assertEqual(len(self.calls), 1)

    def test_xwininfo_enumerates_in_one_call(self):
        found = xtm.find_all_xtm_sessions("xwininfo")
        self.assertEqual(found, ["s0", "s1", "s2"])
        self.assertEqual(len(self.calls), 1)

    def test_xdotool_costs_two_searches_plus_two_calls_per_window(self):
        found = xtm.find_all_xtm_sessions("xdotool")
        self.assertEqual(found, ["s0", "s1", "s2"])
        self.assertEqual(len(self.calls), 2 + 2 * self.window_count)

    def test_xdotool_cost_scales_with_the_number_of_windows(self):
        self.window_count = 5
        xtm.find_all_xtm_sessions("xdotool")
        self.assertEqual(len(self.calls), 2 + 2 * 5)

    def test_xdotool_without_xprop_skips_the_class_lookup(self):
        # With no xprop, the instance name is unavailable and the title
        # alone identifies the window, so the per-window cost halves.
        xtm.which_or_none = lambda name: None
        xtm.find_all_xtm_sessions("xdotool")
        self.assertEqual(len(self.calls), 2 + self.window_count)
