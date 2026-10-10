"""Window actions and layout capture through the real CLI."""

import json
import os
import pty
import subprocess
import sys
import time

import yaml

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
        self.assertIn("already has an xtm window", result.stderr)

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

    def test_verify_reports_the_delta(self):
        self.run_xtm("-s", "work1", "100,200,900,700")
        self.add_session("work1")
        self.add_window("work1", 105, 233, 900, 700)
        result = self.run_xtm("--verify", "-r")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("xtm: warning: work1: placed at x=105 y=233 900x700, "
                      "requested x=100 y=200 900x700 "
                      "(delta x=+5 y=+33 w=+0 h=+0)\n", result.stderr)


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
        slots = yaml.safe_load(self.config_text())["profiles"]["desk"]["sessions"]
        self.assertEqual(slots["other-thing"], {
            "x": 7, "y": 8, "width": 400, "height": 300, "session": "other-thing"})

    def test_capture_new_keeps_a_slot_whose_name_the_captured_session_has(self):
        # Slot work1 is session desk-work1; a stray session named work1 used to
        # be written over it, losing its command and cwd.
        self.write_config("""\
            profiles:
              default:
                sessions: {}
              desk:
                prefix: desk-
                sessions:
                  work1: {x: 0, y: 0, width: 960, height: 1180, command: htop, cwd: /tmp}
                  work1-2: {x: 0, y: 0, width: 100, height: 100}
            """)
        self.add_session("desk-work1")
        self.add_window("desk-work1", 5, 6, 960, 1180)
        self.add_session("work1")
        self.add_window("work1", 900, 0, 500, 500)
        result = self.run_xtm("desk", "--update-profile", "--capture-new")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("xtm: warning: Slot work1 already names session desk-work1; "
                      "captured session work1 recorded as slot work1-3.\n"
                      "Profile desk updated: 1 slot(s) updated, 1 added.",
                      result.stderr)
        slots = yaml.safe_load(self.config_text())["profiles"]["desk"]["sessions"]
        self.assertEqual(slots, {
            "work1": {"x": 5, "y": 6, "width": 960, "height": 1180,
                      "command": "htop", "cwd": "/tmp"},
            "work1-2": {"x": 0, "y": 0, "width": 100, "height": 100},
            "work1-3": {"x": 900, "y": 0, "width": 500, "height": 500,
                        "session": "work1"}})

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
        # only to reopen it would throw the window away for nothing. The
        # switch's own report is the evidence: checking `attached` afterwards
        # proved nothing, because bringing lab up reopens a detached notes.
        self.two_profiles()
        self.run_xtm("desk", "--reset")
        self.assertTrue(self.attached("notes"))
        result = self.run_xtm("lab")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Detached 1 window(s) from profile desk: desk-work1\n",
                      result.stderr)
        self.assertTrue(self.attached("notes"))

    def test_switching_with_kill_spares_a_shared_session(self):
        # Under kill, losing the keep set destroys the shared session, and
        # its jobs and scrollback with it, on every switch.
        self.two_profiles(on_switch="kill")
        self.run_xtm("desk", "--reset")
        self.clear_calls()
        result = self.run_xtm("lab")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Closed 1 window(s) from profile desk: desk-work1\n",
                      result.stderr)
        killed = [call[-1] for call in self.calls("tmux")
                  if call[1] == "kill-session"]
        self.assertEqual(killed, ["desk-work1"])

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


class TestFocusTargets(IntegrationBase):
    """--focus takes a slot name or a session name, like every other action.

    It used to take only the session name, which made it the one exception
    among --open, --close, --detach and --delete-session, and made the
    "did you mean 'xtm --focus NAME'" hint wrong for any profile with a
    prefix: the hint names a slot, and the window is named after the
    session that slot resolves to.
    """

    def prefixed_profile(self):
        self.write_config("""\
            profiles:
              default:
                stack: {x: 40, y: 40, width: 900, height: 650, offset_x: 40, offset_y: 40}
                sessions: {}
              desk:
                prefix: desk-
                stack: {x: 40, y: 40, width: 900, height: 650, offset_x: 40, offset_y: 40}
                sessions:
                  work1: {x: 0, y: 0, width: 900, height: 700}
            """)
        self.run_xtm("desk", "--quiet", "--current-profile")
        self.add_session("desk-work1")
        self.add_window("desk-work1", 0, 0, 900, 700)

    def test_focus_accepts_the_slot_name(self):
        self.prefixed_profile()
        result = self.run_xtm("--focus", "work1")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Focused desk-work1", result.stderr)

    def test_focus_accepts_the_session_name(self):
        self.prefixed_profile()
        result = self.run_xtm("--focus", "desk-work1")
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_the_suggested_focus_command_actually_works(self):
        """The hint and the command it names must agree.

        `xtm work1` on a profile that has a slot but no such profile
        suggests --focus; that suggestion has to be runnable as printed.
        """
        self.prefixed_profile()
        hint = self.run_xtm("work1")
        self.assertEqual(hint.returncode, 1)
        self.assertIn("--focus work1", hint.stderr)
        suggested = self.run_xtm("--focus", "work1")
        self.assertEqual(suggested.returncode, 0, suggested.stderr)

    def test_focus_with_only_xwininfo_says_so(self):
        """The reason must be the missing capability, not a missing window."""
        self.prefixed_profile()
        for name in ("xdotool", "wmctrl"):
            (self.bin / name).unlink()
        result = self.run_xtm("--focus", "work1")
        self.assertEqual(result.returncode, 1)
        self.assertIn("xdotool or wmctrl", result.stderr)

    def test_focus_with_only_xwininfo_says_so_even_with_no_window(self):
        """The case that used to be misreported as 'could not find a window'."""
        self.prefixed_profile()
        self.write_rows("windows.txt", [])
        for name in ("xdotool", "wmctrl"):
            (self.bin / name).unlink()
        result = self.run_xtm("--focus", "work1")
        self.assertEqual(result.returncode, 1)
        self.assertIn("xdotool or wmctrl", result.stderr)
        self.assertNotIn("Could not find or focus", result.stderr)


class TestTerminalsXtmDidNotOpen(IntegrationBase):
    """A session attached in someone else's terminal is not an xtm window.

    Before xtm told its own clients apart, a session attached from an SSH
    login or a Konsole counted as "up": --reset opened no window for it and
    wrote the move and resize escape sequences into that other terminal
    instead; --reset-all stacked every such terminal as a stray; and
    detaching threw the SSH login off along with xtm's window.
    """

    def foreign_pty(self):
        master_fd, slave_fd = pty.openpty()
        self.addCleanup(os.close, master_fd)
        self.addCleanup(os.close, slave_fd)
        os.set_blocking(master_fd, False)
        return master_fd, os.ttyname(slave_fd)

    def written(self, master_fd):
        time.sleep(0.1)
        try:
            return os.read(master_fd, 4096)
        except BlockingIOError:
            return b""

    def launched_sessions(self):
        path = self.state / "launched.log"
        if not path.exists():
            return []
        return [argv[argv.index("-s") + 1]
                for argv in (json.loads(l) for l in path.read_text().splitlines())
                if "-s" in argv]

    def test_reset_opens_a_window_beside_a_foreign_client(self):
        master_fd, foreign_tty = self.foreign_pty()
        self.run_xtm("-s", "work1", "100,200,900,700")
        self.add_session("work1", attached="0")
        self.add_client("work1", tty=foreign_tty)
        # The new xterm attaches after a pause, while the foreign client is
        # already there: xtm must wait for its own, not take the first.
        result = self.run_xtm("-r", env={"XTM_FAKE_XTERM_ATTACH_DELAY": "0.6"})
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.launched_sessions(), ["work1"])
        self.assertIn("Opened work1", result.stderr)
        self.assertEqual(self.written(master_fd), b"",
                         "xtm wrote to a terminal it did not open")

    def test_status_says_attached_elsewhere(self):
        _master, foreign_tty = self.foreign_pty()
        self.run_xtm("-s", "work1", "0,0,900,700")
        self.add_session("work1", attached="0")
        self.add_client("work1", tty=foreign_tty)
        payload = json.loads(self.run_xtm("-l", "-j").stdout)
        self.assertEqual(payload["sessions"][0]["status"], "attached elsewhere")
        self.assertIn("attached elsewhere", self.run_xtm("-l").stdout)

    def test_reset_all_does_not_stack_a_foreign_terminal(self):
        master_fd, foreign_tty = self.foreign_pty()
        self.add_session("mine")
        self.add_session("theirs", attached="0")
        self.add_client("theirs", tty=foreign_tty)
        result = self.run_xtm("-R")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Stacked stray session mine at slot #0", result.stderr)
        self.assertNotIn("theirs", result.stderr)
        self.assertEqual(self.written(master_fd), b"")

    def test_detach_leaves_the_foreign_client_attached(self):
        self.run_xtm("-s", "work1", "0,0,900,700")
        self.add_session("work1")
        self.add_client("work1", tty="/dev/pts/77")
        result = self.run_xtm("--detach", "work1")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual([r[1] for r in self.rows("clients.txt")], ["/dev/pts/77"])
        detaches = [c for c in self.calls("tmux") if "detach-client" in c]
        self.assertTrue(detaches)
        self.assertTrue(all("-s" not in c for c in detaches))

    def test_detach_with_only_a_foreign_client_changes_nothing(self):
        self.run_xtm("-s", "work1", "0,0,900,700")
        self.add_session("work1", attached="0")
        self.add_client("work1", tty="/dev/pts/77")
        result = self.run_xtm("--detach", "work1")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("attached elsewhere", result.stderr)
        self.assertEqual(len(self.rows("clients.txt")), 1)

    def test_open_is_allowed_when_only_a_foreign_client_is_attached(self):
        self.run_xtm("-s", "work1", "0,0,900,700")
        self.add_session("work1", attached="0")
        self.add_client("work1", tty="/dev/pts/77")
        result = self.run_xtm("-o", "work1")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.launched_sessions(), ["work1"])


class TestOffScreenWindows(IntegrationBase):
    """A profile written for another screen must not lose windows silently."""

    LAPTOP = "eDP-1:1440x900+0+0"

    def setUp(self):
        IntegrationBase.setUp(self)
        self.env["XTM_FAKE_MONITORS"] = self.LAPTOP
        master_fd, slave_fd = pty.openpty()
        self.addCleanup(os.close, master_fd)
        self.addCleanup(os.close, slave_fd)
        os.set_blocking(master_fd, False)
        self.master_fd, self.tty = master_fd, os.ttyname(slave_fd)

    def written(self):
        time.sleep(0.1)
        try:
            return os.read(self.master_fd, 4096).decode("ascii")
        except BlockingIOError:
            return ""

    def test_a_window_off_every_monitor_is_reported_and_still_placed(self):
        self.run_xtm("-s", "right", "1920,0,960,580")
        self.add_session("right", tty=self.tty)
        result = self.run_xtm("-r")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("right: x=1920 y=0 960x580 is off screen on this screen "
                      "(eDP-1 1440x900+0+0)", result.stderr)
        self.assertIn("--clamp", result.stderr)
        self.assertEqual(self.written(), "\x1b[4;580;960t\x1b[3;1920;0t")

    def test_clamp_brings_it_onto_the_monitor(self):
        self.run_xtm("-s", "right", "1920,0,960,580")
        self.add_session("right", tty=self.tty)
        result = self.run_xtm("-r", "--clamp")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("placing it at x=480 y=0 960x580 instead", result.stderr)
        self.assertEqual(self.written(), "\x1b[4;580;960t\x1b[3;480;0t")

    def test_a_window_on_screen_says_nothing(self):
        self.run_xtm("-s", "left", "0,0,960,580")
        self.add_session("left", tty=self.tty)
        result = self.run_xtm("-r")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn("screen", result.stderr)

    def test_the_status_view_flags_it(self):
        self.run_xtm("-s", "right", "1920,0,960,580")
        self.run_xtm("-s", "left", "-50,10,960,580")
        text = self.run_xtm("-l").stdout
        self.assertIn("[off screen]", text)
        self.assertIn("[partly off screen]", text)
        payload = json.loads(self.run_xtm("-l", "-j").stdout)
        placements = dict((s["name"], s["placement"]) for s in payload["sessions"])
        self.assertEqual(placements, {"right": "off screen",
                                      "left": "partly off screen"})
        self.assertEqual(payload["monitors"][0]["name"], "eDP-1")

    def test_an_unknown_screen_checks_nothing(self):
        self.env.pop("XTM_FAKE_MONITORS")
        self.run_xtm("-s", "right", "1920,0,960,580")
        payload = json.loads(self.run_xtm("-l", "-j").stdout)
        self.assertIsNone(payload["monitors"])
        self.assertIsNone(payload["sessions"][0]["placement"])


class TestFailingStartCommands(IntegrationBase):
    """A slot's command replaces the pane's shell; when it failed, the pane,
    the session and the window all vanished without a trace."""

    def launched(self):
        path = self.state / "launched.log"
        return [json.loads(l) for l in path.read_text().splitlines()]

    def test_the_pane_is_held_on_failure_where_tmux_can(self):
        self.write_config("""\
            profiles:
              default:
                sessions:
                  cluster: {x: 0, y: 0, width: 900, height: 700, command: ssh headnode}
            """)
        result = self.run_xtm("-r")
        self.assertEqual(result.returncode, 0, result.stderr)
        tail = self.launched()[0]
        self.assertEqual(tail[-6:], ["ssh headnode", ";", "set-option", "-p",
                                     "remain-on-exit", "failed"])

    def test_an_older_tmux_gets_the_plain_command(self):
        self.write_config("""\
            profiles:
              default:
                sessions:
                  cluster: {x: 0, y: 0, width: 900, height: 700, command: ssh headnode}
            """)
        result = self.run_xtm("-r", env={"XTM_FAKE_TMUX_VERSION": "3.1c"})
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.launched()[0][-1], "ssh headnode")


class TestSetup(IntegrationBase):
    """A slot's `setup` builds the inside of a session xtm just created."""

    def config(self, setup):
        self.write_config("""\
            profiles:
              default:
                sessions: {}
              desk:
                prefix: desk-
                sessions:
                  dev:
                    x: 0
                    y: 0
                    width: 900
                    height: 700
                    cwd: %s
                    setup: %s
            """ % (self.tmp, setup))
        self.out = self.tmp / "setup-ran"

    def test_runs_once_with_the_session_slot_and_profile(self):
        self.config("""'printf "%s %s %s %s" "$XTM_SESSION" "$XTM_SLOT" "$XTM_PROFILE" "$PWD" >> setup-ran'""")
        result = self.run_xtm("desk")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Ran setup for desk-dev", result.stderr)
        self.assertEqual(self.out.read_text(),
                         "desk-dev dev desk %s" % (self.tmp,))

    def test_not_run_when_the_session_already_existed(self):
        self.config("'printf x >> setup-ran'")
        self.add_session("desk-dev", attached="0")
        result = self.run_xtm("desk")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(self.out.exists())

    def test_a_failing_setup_is_a_warning(self):
        self.config("'printf oops >&2; exit 4'")
        result = self.run_xtm("desk")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("desk-dev: setup exited 4: oops", result.stderr)
        self.assertIn("Opened desk-dev", result.stderr)

    def test_dry_run_only_says_so(self):
        self.config("'printf x >> setup-ran'")
        result = self.run_xtm("desk", "--dry-run")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("[dry-run] would run setup for desk-dev", result.stderr)
        self.assertFalse(self.out.exists())

    def test_the_status_view_shows_it(self):
        self.config("'printf x >> setup-ran'")
        result = self.run_xtm("-p", "desk", "-l")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertRegex(result.stdout, r"\n {42}setup: +printf x >> setup-ran\n")
        self.assertFalse(self.out.exists())
