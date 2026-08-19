"""Window actions and layout capture through the real CLI."""

import json
import os
import pty
import subprocess
import sys
import time

from tests.support import (
    IntegrationBase,
    XTM_SCRIPT,
)


class TestWindowActions(IntegrationBase):

    def test_open_launches_xterm_with_expected_arguments(self):
        self.run_xtm("-s", "work1", "10,20,900,700")
        result = self.run_xtm("-o", "work1")
        self.assertEqual(result.returncode, 0, result.stderr)
        launched = json.loads((self.state / "launched.log").read_text().strip())
        self.assertIn("-name", launched)
        self.assertEqual(launched[launched.index("-name") + 1], "xtm-work1")
        self.assertEqual(launched[launched.index("-T") + 1], "xtm:work1")
        self.assertIn("Opened work1", result.stderr)

    def test_open_passes_command_and_cwd(self):
        self.write_config("""\
            profiles:
              default:
                stack: {x: 1, y: 1, width: 10, height: 10, offset_x: 1, offset_y: 1}
                sessions:
                  logs:
                    x: 0
                    y: 0
                    width: 900
                    height: 700
                    command: "tail -f /var/log/messages"
                    cwd: "/var/log"
            """)
        self.assertEqual(self.run_xtm("-o", "logs").returncode, 0)
        launched = json.loads((self.state / "launched.log").read_text().strip())
        self.assertIn("tail -f /var/log/messages", launched)
        self.assertEqual(launched[launched.index("-c") + 1], "/var/log")

    def test_open_uses_stack_slot_for_unconfigured_session(self):
        result = self.run_xtm("-o", "scratch")
        self.assertIn("stacked slot #0", result.stderr)

    def test_open_already_attached_session_exits_one(self):
        self.add_session("work1")
        result = self.run_xtm("-o", "work1")
        self.assertEqual(result.returncode, 1)
        self.assertIn("already attached", result.stderr)

    def test_open_reports_immediate_xterm_failure(self):
        result = self.run_xtm("-o", "work1",
                              env={"XTM_FAKE_XTERM_FAIL": "1"})
        self.assertEqual(result.returncode, 1)
        self.assertIn("xterm exited immediately", result.stderr)

    def test_open_without_display_exits_one(self):
        env = dict(self.env)
        env.pop("DISPLAY")
        result = subprocess.run(
            [sys.executable, str(XTM_SCRIPT), "-o", "work1"],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            universal_newlines=True, env=env, timeout=40)
        self.assertEqual(result.returncode, 1)
        self.assertIn("DISPLAY", result.stderr)

    def test_reset_repositions_running_sessions(self):
        master_fd, slave_fd = pty.openpty()
        self.addCleanup(os.close, master_fd)
        self.addCleanup(os.close, slave_fd)
        tty_path = os.ttyname(slave_fd)
        self.run_xtm("-s", "work1", "100,200,900,700")
        self.add_session("work1", tty=tty_path)
        result = self.run_xtm("-r")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Repositioned work1", result.stderr)
        os.set_blocking(master_fd, False)
        time.sleep(0.1)
        data = os.read(master_fd, 4096).decode("ascii")
        self.assertEqual(data, "\x1b[4;700;900t\x1b[3;100;200t")

    def test_reset_with_no_named_sessions_is_a_no_op(self):
        result = self.run_xtm("-r")
        self.assertEqual(result.returncode, 0)
        self.assertIn("no slots configured", result.stderr)

    def test_reset_all_stacks_strays(self):
        self.add_session("stray1")
        self.add_session("stray2")
        result = self.run_xtm("-R")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Stacked stray session stray1 at slot #0", result.stderr)
        self.assertIn("Stacked stray session stray2 at slot #1", result.stderr)

    def test_reset_all_reports_when_no_strays(self):
        result = self.run_xtm("-R")
        self.assertIn("No stray", result.stderr)

    def test_close_kills_session(self):
        self.add_session("work1")
        result = self.run_xtm("-k", "work1")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.rows("sessions.txt"), [])

    def test_close_missing_session_exits_one(self):
        result = self.run_xtm("-k", "ghost")
        self.assertEqual(result.returncode, 1)
        self.assertIn("No tmux session", result.stderr)

    def test_close_all_is_scoped_to_the_profile(self):
        self.run_xtm("-s", "work1", "0,0,900,700")
        self.add_session("work1")
        self.add_session("unmanaged")
        result = self.run_xtm("-y", "-K")
        self.assertEqual(result.returncode, 0, result.stderr)
        remaining = [row[0] for row in self.rows("sessions.txt")]
        self.assertEqual(remaining, ["unmanaged"])

    def test_close_all_with_nothing_running(self):
        self.run_xtm("-s", "work1", "0,0,900,700")
        result = self.run_xtm("-y", "-K")
        self.assertIn("No sessions from profile", result.stderr)

    def test_focus_activates_window(self):
        self.add_session("work1")
        win_id = self.add_window("work1", 0, 0, 900, 700)
        result = self.run_xtm("-f", "work1")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual((self.state / "focus.log").read_text().strip(), win_id)

    def test_focus_missing_window_exits_one(self):
        self.assertEqual(self.run_xtm("-f", "ghost").returncode, 1)


class TestCapture(IntegrationBase):

    def test_update_profile_captures_positions(self):
        self.run_xtm("-l")
        self.add_session("work1")
        self.add_window("work1", 100, 200, 900, 700)
        result = self.run_xtm("-u", "--capture-new")
        self.assertEqual(result.returncode, 0, result.stderr)
        config = self.config_text()
        self.assertIn("work1", config)
        self.assertIn("100", config)

    def test_update_profile_applies_frame_compensation(self):
        self.run_xtm("-l")
        self.add_window("work1", 105, 233, 900, 700)
        result = self.run_xtm("-u", "--capture-new",
                              env={"XTM_FAKE_FRAME_EXTENTS": "5, 5, 33, 5"})
        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(self.run_xtm("-l", "-j").stdout)
        entry = [s for s in payload["sessions"] if s["name"] == "work1"][0]
        self.assertEqual(entry["configured"]["x"], 100)
        self.assertEqual(entry["configured"]["y"], 200)

    def test_frame_compensation_can_be_disabled(self):
        self.run_xtm("-l")
        self.add_window("work1", 105, 233, 900, 700)
        self.run_xtm("-u", "--capture-new", "--no-frame-compensation",
                     env={"XTM_FAKE_FRAME_EXTENTS": "5, 5, 33, 5"})
        payload = json.loads(self.run_xtm("-l", "-j").stdout)
        entry = [s for s in payload["sessions"] if s["name"] == "work1"][0]
        self.assertEqual(entry["configured"]["y"], 233)

    def test_update_profile_preserves_command(self):
        self.write_config("""\
            profiles:
              default:
                stack: {x: 1, y: 1, width: 10, height: 10, offset_x: 1, offset_y: 1}
                sessions:
                  logs: {x: 0, y: 0, width: 100, height: 100, command: htop}
            """)
        self.add_window("logs", 400, 500, 900, 700)
        self.run_xtm("-u", "--capture-new")
        self.assertIn("htop", self.config_text())
        self.assertIn("400", self.config_text())

    def test_update_profile_with_no_windows(self):
        self.run_xtm("-l")
        result = self.run_xtm("-u", "--capture-new")
        self.assertEqual(result.returncode, 0)
        self.assertIn("nothing to update", result.stderr)

    def test_new_profile_captures_layout(self):
        self.run_xtm("-l")
        self.add_window("work1", 10, 20, 900, 700)
        self.add_window("logs", 910, 20, 900, 700)
        result = self.run_xtm("-n", "captured")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("captured", self.config_text())
        payload = json.loads(self.run_xtm("-p", "captured", "-l", "-j").stdout)
        self.assertEqual(sorted(s["name"] for s in payload["sessions"]),
                         ["logs", "work1"])

    def test_new_profile_rejects_existing_name(self):
        self.run_xtm("-l")
        self.assertEqual(self.run_xtm("-n", "default").returncode, 1)

    def test_new_profile_rejects_invalid_name(self):
        self.run_xtm("-l")
        self.assertEqual(self.run_xtm("-n", "bad name").returncode, 1)

    def test_windows_from_older_versions_are_found_by_title(self):
        self.run_xtm("-l")
        rows = self.rows("windows.txt")
        rows.append(["0x900", "xtm:legacy", "", 10, 10, 800, 600])
        self.write_rows("windows.txt", rows)
        self.run_xtm("-u", "--capture-new")
        self.assertIn("legacy", self.config_text())

    def test_unrelated_windows_are_ignored(self):
        self.run_xtm("-l")
        rows = self.rows("windows.txt")
        rows.append(["0x901", "Firefox", "Navigator", 0, 0, 800, 600])
        self.write_rows("windows.txt", rows)
        result = self.run_xtm("-u", "--capture-new")
        self.assertNotIn("Navigator", self.config_text())
        self.assertIn("nothing to update", result.stderr)


class TestCaptureScope(IntegrationBase):
    """--update-profile no longer grafts other profiles' sessions on."""

    def setup_two(self):
        self.write_config("""\
            profiles:
              default:
                sessions: {}
              desk:
                prefix: desk-
                stack: {x: 40, y: 40, width: 900, height: 650, offset_x: 40, offset_y: 40}
                sessions:
                  work1: {x: 0, y: 0, width: 960, height: 1180}
            """)

    def test_foreign_windows_are_skipped_by_default(self):
        self.setup_two()
        self.add_session("desk-work1", tty="/dev/null", attached="1", pid=1)
        self.add_window("desk-work1", 5, 6, 960, 1180)
        self.add_session("other-thing", tty="/dev/null", attached="1", pid=2)
        self.add_window("other-thing", 7, 8, 400, 300)
        result = self.run_xtm("desk", "--update-profile")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn("other-thing", self.config_text())
        self.assertIn("left out", result.stderr)

    def test_capture_new_adds_them(self):
        self.setup_two()
        self.add_session("other-thing", tty="/dev/null", attached="1", pid=2)
        self.add_window("other-thing", 7, 8, 400, 300)
        result = self.run_xtm("desk", "--update-profile", "--capture-new")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("other-thing", self.config_text())

    def test_known_slots_are_still_updated(self):
        self.setup_two()
        self.add_session("desk-work1", tty="/dev/null", attached="1", pid=1)
        self.add_window("desk-work1", 11, 22, 500, 400)
        self.run_xtm("desk", "--update-profile")
        payload = json.loads(self.run_xtm("--list", "--json").stdout)
        entry = next(e for e in payload["sessions"] if e["name"] == "work1")
        self.assertEqual(entry["configured"]["x"], 11)

    def test_strays_are_attributed_to_their_owning_profile(self):
        self.write_config("""\
            profiles:
              default:
                sessions: {}
              desk:
                prefix: desk-
                stack: {x: 40, y: 40, width: 900, height: 650, offset_x: 40, offset_y: 40}
                sessions:
                  work1: {x: 0, y: 0, width: 900, height: 700}
              lab:
                prefix: lab-
                sessions:
                  work1: {x: 0, y: 0, width: 900, height: 700}
            """)
        self.add_session("lab-work1", tty="/dev/null", attached="1", pid=3)
        result = self.run_xtm("desk", "--list")
        self.assertIn("lab-work1", result.stdout)
        self.assertIn("belongs to profile lab", result.stdout)

    def test_verbose_profile_listing_shows_slots_and_state(self):
        self.setup_two()
        self.add_session("desk-work1", tty="/dev/null", attached="1", pid=1)
        result = self.run_xtm("desk", "--list-profiles", "--verbose")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("desk-work1", result.stdout)
        self.assertIn("attached", result.stdout)

    def test_verbose_profile_listing_json(self):
        self.setup_two()
        payload = json.loads(
            self.run_xtm("--list-profiles", "--verbose", "--json").stdout)
        desk = next(p for p in payload["profiles"] if p["name"] == "desk")
        self.assertEqual(desk["prefix"], "desk-")
        self.assertEqual(desk["slots"][0]["session"], "desk-work1")


class TestSlotIntegration(IntegrationBase):
    """Prefixes, sharing and titles end to end."""

    def two_profiles(self):
        self.write_config("""\
            profiles:
              default:
                sessions: {}
              desk:
                prefix: desk-
                stack: {x: 40, y: 40, width: 900, height: 650, offset_x: 40, offset_y: 40}
                sessions:
                  work1: {x: 0, y: 0, width: 960, height: 1180}
                  notes: {session: notes, x: 960, y: 0, width: 960, height: 580}
              lab:
                prefix: lab-
                stack: {x: 40, y: 40, width: 900, height: 650, offset_x: 40, offset_y: 40}
                sessions:
                  work1: {x: 0, y: 0, width: 900, height: 700}
                  notes: {session: notes, x: 900, y: 0, width: 900, height: 700}
            """)

    def launched_argv(self):
        path = self.state / "launched.log"
        if not path.exists():
            return []
        return [json.loads(l) for l in path.read_text().splitlines() if l.strip()]

    def test_reset_opens_prefixed_sessions(self):
        self.two_profiles()
        result = self.run_xtm("desk", "--reset")
        self.assertEqual(result.returncode, 0, result.stderr)
        names = [r[0] for r in self.rows("sessions.txt")]
        self.assertIn("desk-work1", names)
        self.assertIn("notes", names)
        self.assertNotIn("work1", names)

    def test_the_same_slot_in_two_profiles_is_two_sessions(self):
        self.two_profiles()
        self.run_xtm("desk", "--reset")
        self.run_xtm("lab", "--on-switch", "leave", "--reset")
        names = [r[0] for r in self.rows("sessions.txt")]
        self.assertIn("desk-work1", names)
        self.assertIn("lab-work1", names)

    def test_a_shared_session_is_reused_not_duplicated(self):
        self.two_profiles()
        self.run_xtm("desk", "--reset")
        self.run_xtm("lab", "--reset")
        names = [r[0] for r in self.rows("sessions.txt")]
        self.assertEqual(names.count("notes"), 1)

    def test_title_template_reaches_the_xterm_command(self):
        self.write_config("""\
            profiles:
              default:
                sessions: {}
              desk:
                prefix: desk-
                title: "{profile} :: {slot}"
                stack: {x: 40, y: 40, width: 900, height: 650, offset_x: 40, offset_y: 40}
                sessions:
                  work1: {x: 0, y: 0, width: 900, height: 700}
            """)
        self.run_xtm("desk", "--reset")
        argv = self.launched_argv()[0]
        self.assertIn("desk :: work1", argv)
        # The instance name stays derived from the session, so a custom
        # title cannot break window discovery.
        self.assertIn("xtm-desk-work1", argv)

    def test_two_slots_resolving_to_one_session_is_rejected(self):
        self.write_config("""\
            profiles:
              default:
                sessions: {}
              desk:
                sessions:
                  a: {session: same, x: 0, y: 0, width: 900, height: 700}
                  b: {session: same, x: 1, y: 1, width: 900, height: 700}
            """)
        result = self.run_xtm("desk", "--validate")
        self.assertEqual(result.returncode, 1)
        self.assertIn("both resolve", result.stdout + result.stderr)

    def test_close_accepts_a_slot_name_or_a_session_name(self):
        self.two_profiles()
        self.run_xtm("desk", "--reset")
        result = self.run_xtm("--close", "work1")     # the slot name
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn("desk-work1", [r[0] for r in self.rows("sessions.txt")])
        self.run_xtm("--reset")
        result = self.run_xtm("--close", "desk-work1")  # the session name
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_list_reports_slot_and_session(self):
        self.two_profiles()
        payload = json.loads(self.run_xtm("desk", "--list", "--json").stdout)
        entries = dict((e["name"], e) for e in payload["sessions"])
        self.assertEqual(entries["work1"]["session"], "desk-work1")
        self.assertFalse(entries["work1"]["shared"])
        self.assertEqual(entries["notes"]["session"], "notes")
        self.assertTrue(entries["notes"]["shared"])

    def test_set_creates_a_slot_using_the_prefix(self):
        self.two_profiles()
        self.run_xtm("desk", "--set", "extra", "1,2,300,400")
        payload = json.loads(self.run_xtm("--list", "--json").stdout)
        entry = next(e for e in payload["sessions"] if e["name"] == "extra")
        self.assertEqual(entry["session"], "desk-extra")


class TestDetachAndSwitch(IntegrationBase):
    """Detaching windows, and what a profile switch does to them."""

    def two_profiles(self, on_switch="detach"):
        self.write_config("""\
            profiles:
              default:
                sessions: {}
              desk:
                prefix: desk-
                on_switch: %s
                stack: {x: 40, y: 40, width: 900, height: 650, offset_x: 40, offset_y: 40}
                sessions:
                  work1: {x: 0, y: 0, width: 960, height: 1180}
                  notes: {session: notes, x: 960, y: 0, width: 960, height: 580}
              lab:
                prefix: lab-
                stack: {x: 40, y: 40, width: 900, height: 650, offset_x: 40, offset_y: 40}
                sessions:
                  work1: {x: 0, y: 0, width: 900, height: 700}
                  notes: {session: notes, x: 900, y: 0, width: 900, height: 700}
            """ % (on_switch,))

    def attached(self, name):
        for row in self.rows("sessions.txt"):
            if row[0] == name:
                return row[1] == "1"
        return None

    def test_detach_keeps_the_session_alive(self):
        self.two_profiles()
        self.run_xtm("desk", "--reset")
        result = self.run_xtm("--detach", "work1")
        self.assertEqual(result.returncode, 0, result.stderr)
        # Still present, but no longer attached: the window is gone and
        # everything inside the session survives.
        self.assertFalse(self.attached("desk-work1"))
        self.assertIn("desk-work1", [r[0] for r in self.rows("sessions.txt")])

    def test_detach_of_a_session_with_no_window_is_not_an_error(self):
        self.two_profiles()
        self.add_session("desk-work1", tty="/dev/null", attached="0", pid=1)
        result = self.run_xtm("desk", "--detach", "work1")
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_detach_of_a_missing_session_exits_one(self):
        self.two_profiles()
        result = self.run_xtm("desk", "--detach", "nosuch")
        self.assertEqual(result.returncode, 1)

    def test_switching_detaches_the_previous_profile(self):
        self.two_profiles()
        self.run_xtm("desk", "--reset")
        self.assertTrue(self.attached("desk-work1"))
        self.run_xtm("lab")
        self.assertFalse(self.attached("desk-work1"))
        self.assertIn("desk-work1", [r[0] for r in self.rows("sessions.txt")])

    def test_switching_leaves_a_shared_session_attached(self):
        # It is about to be repositioned into its new slot, so closing it
        # only to reopen it would throw the window away for nothing.
        self.two_profiles()
        self.run_xtm("desk", "--reset")
        self.assertTrue(self.attached("notes"))
        self.run_xtm("lab")
        self.assertTrue(self.attached("notes"))

    def test_on_switch_leave_keeps_windows(self):
        self.two_profiles(on_switch="leave")
        self.run_xtm("desk", "--reset")
        self.run_xtm("lab")
        self.assertTrue(self.attached("desk-work1"))

    def test_on_switch_kill_destroys_sessions(self):
        self.two_profiles(on_switch="kill")
        self.run_xtm("desk", "--reset")
        self.run_xtm("lab")
        self.assertNotIn("desk-work1", [r[0] for r in self.rows("sessions.txt")])

    def test_on_switch_flag_overrides_the_profile_setting(self):
        self.two_profiles(on_switch="leave")
        self.run_xtm("desk", "--reset")
        self.run_xtm("lab", "--on-switch", "detach")
        self.assertFalse(self.attached("desk-work1"))

    def test_reselecting_the_same_profile_does_nothing(self):
        self.two_profiles()
        self.run_xtm("desk", "--reset")
        self.run_xtm("desk")
        self.assertTrue(self.attached("desk-work1"))

    def test_close_all_detach_mode(self):
        self.two_profiles()
        self.run_xtm("desk", "--reset")
        result = self.run_xtm("--close-all", "--detach-mode", "--yes")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(self.attached("desk-work1"))
        self.assertIn("desk-work1", [r[0] for r in self.rows("sessions.txt")])

    def test_close_all_covers_every_profile_with_all(self):
        self.two_profiles(on_switch="leave")
        self.run_xtm("desk", "--reset")
        self.run_xtm("lab", "--reset")
        result = self.run_xtm("--close-all", "--all", "--yes")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.rows("sessions.txt"), [])

    def test_close_all_without_all_is_scoped_to_the_profile(self):
        self.two_profiles(on_switch="leave")
        self.run_xtm("desk", "--reset")
        self.run_xtm("lab", "--reset")
        self.run_xtm("--close-all", "--yes")
        names = [r[0] for r in self.rows("sessions.txt")]
        self.assertIn("desk-work1", names)
        self.assertNotIn("lab-work1", names)
