"""Reporting paths that only appear when the screen is untidy.

An empty listing, a session nobody claims, a partial failure: each prints
something specific, and each was reachable only by arranging the world
first.
"""

import json
import unittest

from tests.support import IntegrationBase


class TestUnclaimedSessions(IntegrationBase):
    """Sessions that belong to no profile are reported, never touched."""

    def setUp(self):
        IntegrationBase.setUp(self)
        self.run_xtm("-s", "work1", "0,0,900,700")
        # A session nothing in the config names.
        self.run_xtm("-o", "orphan")

    def test_the_verbose_listing_names_them(self):
        result = self.run_xtm("-P", "-v")
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertIn("orphan", result.stdout)
        self.assertIn("claimed by no profile", result.stdout)

    def test_the_verbose_json_carries_them(self):
        payload = json.loads(self.run_xtm("-P", "-v", "-j").stdout)
        self.assertIn("orphan", payload["unclaimed"])

    def test_close_all_leaves_them_running(self):
        """--close-all is scoped to the profile, deliberately."""
        self.run_xtm("-K", "-y")
        payload = json.loads(self.run_xtm("-l", "-j").stdout)
        self.assertIn("orphan", payload["strays"],
                      "--close-all killed a session belonging to no profile")


class TestPartialFailures(IntegrationBase):
    """Some of it worked. Say which part did not."""

    def test_close_all_reports_the_sessions_it_could_not_kill(self):
        self.run_xtm("-s", "work1", "0,0,900,700")
        self.run_xtm("-s", "logs", "900,0,900,700")
        self.run_xtm("-o", "work1")
        self.run_xtm("-o", "logs")
        result = self.run_xtm("-K", "-y",
                              env={"XTM_FAKE_TMUX_REFUSE": "kill-session"})
        self.assertNotEqual(0, result.returncode)
        self.assertIn("work1", result.stderr)
        self.assertIn("logs", result.stderr)
        self.assertNotIn("Traceback", result.stderr)

    def test_reset_reports_every_session_it_could_not_place(self):
        """A failure per session, and the others still attempted."""
        self.run_xtm("-s", "work1", "0,0,900,700")
        self.run_xtm("-s", "logs", "900,0,900,700")
        result = self.run_xtm("-r", env={"XTM_FAKE_XTERM_FAIL": "1"})
        self.assertNotEqual(0, result.returncode)
        self.assertIn("work1", result.stderr)
        self.assertIn("logs", result.stderr)


class TestAttachTimeout(IntegrationBase):
    """A terminal that starts and never attaches is not a hang."""

    def test_opening_gives_up_with_an_explanation(self):
        self.run_xtm("-s", "work1", "0,0,900,700")
        result = self.run_xtm(
            "-o", "work1",
            env={"XTM_FAKE_XTERM_NO_ATTACH": "1",
                 # Outlive xtm's own wait, so what is measured is xtm giving
                 # up rather than the fake terminal exiting first.
                 "XTM_FAKE_XTERM_LIFETIME": "12"})
        self.assertNotEqual(0, result.returncode)
        self.assertNotIn("Traceback", result.stderr)
        self.assertIn("work1", result.stderr)


if __name__ == "__main__":
    unittest.main()
