"""Commands that only report must not switch the profile they are pointed at.

`xtm -p lab --check` is how a user looks at a profile before switching to it.
Until 2026-10-09 every command resolved -p the same way: it saved `lab` as the
current profile and ran the old profile's on_switch, detaching its windows,
although `--check` is documented to change nothing.
"""

from tests.support import IntegrationBase

CONFIG = """\
    profiles:
      default:
        sessions: {}
      desk:
        prefix: desk-
        sessions:
          a: {x: 0, y: 0, width: 800, height: 600}
      lab:
        prefix: lab-
        sessions:
          a: {x: 0, y: 0, width: 800, height: 600}
    """


class TestReportingCommandsDoNotSwitch(IntegrationBase):

    def setUp(self):
        super().setUp()
        self.write_config(CONFIG)
        self.add_session("desk-a")
        self.add_window("desk-a", 0, 0, 800, 600)
        result = self.run_xtm("desk")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.clear_calls()

    def disposals(self):
        return [call for call in self.calls("tmux")
                if call[1] in ("detach-client", "kill-session")]

    def test_each_reporting_command_leaves_profile_and_windows_alone(self):
        before = self.fs_snapshot(self.tmp / "cfg", self.tmp / "state")
        for action in ("--check", "--list", "--validate", "--list-profiles"):
            with self.subTest(action=action):
                result = self.run_xtm("-p", "lab", action)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertNotIn("Detached", result.stderr)
                self.assertEqual(self.disposals(), [])
                self.assertEqual(
                    self.fs_snapshot(self.tmp / "cfg", self.tmp / "state"), before)
        self.assertEqual(self.run_xtm("-C").stdout, "desk\n")

    def test_the_named_profile_is_the_one_reported(self):
        listing = self.run_xtm("-p", "lab", "--list")
        self.assertIn("Profile: lab\n", listing.stdout)
        check = self.run_xtm("-p", "lab", "--check", "--json")
        self.assertIn('"profile": "lab"', check.stdout)

    def test_current_profile_still_selects_and_confirms(self):
        result = self.run_xtm("-p", "lab", "-C")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "lab\n")
        self.assertEqual(result.stderr, "Detached 1 window(s) from profile desk: desk-a\n")
        self.assertEqual(self.disposals(), [["tmux", "detach-client", "-t", "/dev/null"]])
