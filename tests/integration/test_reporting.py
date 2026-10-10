"""Reporting commands and their JSON forms."""

import json
import re

from tests.support import (
    IntegrationBase,
    xtm,
)


class TestReporting(IntegrationBase):

    def test_list_shows_statuses(self):
        self.run_xtm("-s", "work1", "0,0,900,700")
        self.run_xtm("-s", "logs", "900,0,900,700")
        self.add_session("work1")
        self.add_session("detached", attached="0")
        self.add_session("stray")
        result = self.run_xtm("-l")
        self.assertIn("work1", result.stdout)
        self.assertIn("attached", result.stdout)
        self.assertIn("not running", result.stdout)
        self.assertIn("stray", result.stdout)

    def test_tmux_rewriting_control_characters_does_not_hide_sessions(self):
        """xtm asked tmux for tab-separated fields, and runs tmux in the C
        locale, where tmux 3.6 rewrites every control character in format
        output to "_". Every session then parsed as "work1_1", no session
        was ever running, and no window could be placed -- on every real
        machine, while the fake tmux kept printing a real tab. The fake now
        renders the format and damages it the way tmux does."""
        self.run_xtm("-s", "work1", "0,0,900,700")
        self.add_session("work1")
        payload = json.loads(self.run_xtm("-l", "-j").stdout)
        self.assertEqual(payload["sessions"][0]["status"], "attached")
        self.assertEqual(payload["strays"], [])

    def test_list_json_structure(self):
        self.run_xtm("-s", "work1", "0,0,900,700")
        payload = json.loads(self.run_xtm("-l", "-j").stdout)
        self.assertEqual(payload["profile"], "default")
        self.assertEqual(payload["sessions"][0]["name"], "work1")
        self.assertIn("strays", payload)
        self.assertIn("geometry_tool", payload)

    def test_list_shows_live_geometry(self):
        self.run_xtm("-s", "work1", "0,0,900,700")
        self.add_session("work1")
        self.add_window("work1", 111, 222, 800, 600)
        result = self.run_xtm("-l")
        self.assertIn("live: x=111 y=222 800x600", result.stdout)

    def test_list_shows_cwd(self):
        # cwd is part of the JSON view, so the text view must show it too;
        # otherwise the two views disagree about what is configured.
        self.write_config("""\
            profiles:
              desk:
                stack: {x: 40, y: 40, width: 900, height: 650, offset_x: 40, offset_y: 40}
                sessions:
                  cluster: {x: 0, y: 0, width: 900, height: 700, cwd: "~/projects"}
            """)
        result = self.run_xtm("--profile", "desk", "--list")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("cwd:", result.stdout)
        self.assertIn("~/projects", result.stdout)

    def test_list_omits_cwd_when_unset(self):
        self.write_config("""\
            profiles:
              desk:
                stack: {x: 40, y: 40, width: 900, height: 650, offset_x: 40, offset_y: 40}
                sessions:
                  plain: {x: 0, y: 0, width: 900, height: 700}
            """)
        result = self.run_xtm("--profile", "desk", "--list")
        self.assertNotIn("cwd:", result.stdout)

    def test_list_shows_command(self):
        self.write_config("""\
            profiles:
              default:
                stack: {x: 1, y: 1, width: 10, height: 10, offset_x: 1, offset_y: 1}
                sessions:
                  logs: {x: 0, y: 0, width: 100, height: 100, command: htop}
            """)
        self.assertIn("command:    htop", self.run_xtm("-l").stdout)

    def test_current_profile_reports_and_persists(self):
        self.run_xtm("-l")
        self.run_xtm("--copy-profile", "default", "spare")
        result = self.run_xtm("-p", "spare", "-C")
        self.assertEqual(result.stdout.strip(), "spare")
        self.assertEqual(self.run_xtm("-C").stdout.strip(), "spare")

    def test_current_profile_json(self):
        self.run_xtm("-l")
        payload = json.loads(self.run_xtm("-C", "-j").stdout)
        self.assertIn("state_file", payload)

    def test_state_is_per_machine(self):
        self.run_xtm("-l")
        self.assertFalse((self.tmp / "state").exists(),
                         "merely listing must not persist a profile choice")
        self.run_xtm("-p", "default", "-C")
        state_files = [p.name for p in (self.tmp / "state").iterdir()]
        self.assertEqual(state_files,
                         ["current_profile." + xtm.short_hostname()])


class TestOrderingGuarantees(IntegrationBase):
    """Documented processing order for the two bulk commands."""

    def test_named_sessions_are_processed_in_config_order(self):
        self.write_config("""\
            profiles:
              desk:
                stack: {x: 40, y: 40, width: 900, height: 650, offset_x: 40, offset_y: 40}
                sessions:
                  zeta: {x: 0, y: 0, width: 900, height: 700}
                  alpha: {x: 10, y: 10, width: 900, height: 700}
                  middle: {x: 20, y: 20, width: 900, height: 700}
            """)
        for name in ("zeta", "alpha", "middle"):
            self.add_session(name, tty="/dev/null", attached="1", pid=1)
        self.clear_calls()
        result = self.run_xtm("--profile", "desk", "--reset")
        self.assertEqual(result.returncode, 0, result.stderr)
        order = re.findall(r"Repositioned (\S+) to", result.stderr)
        self.assertEqual(order, ["zeta", "alpha", "middle"])

    def test_strays_are_stacked_in_alphabetical_order(self):
        self.write_config("""\
            profiles:
              desk:
                stack: {x: 40, y: 40, width: 900, height: 650, offset_x: 40, offset_y: 40}
                sessions: {}
            """)
        for name in ("delta", "bravo", "charlie"):
            self.add_session(name, tty="/dev/null", attached="1", pid=1)
        self.clear_calls()
        result = self.run_xtm("--profile", "desk", "--reset-all")
        self.assertEqual(result.returncode, 0, result.stderr)
        order = re.findall(r"Stacked stray session (\S+) at", result.stderr)
        self.assertEqual(order, ["bravo", "charlie", "delta"])


class TestCliCombinations(IntegrationBase):
    """Option ordering, precedence and combination behavior.

    Options interact: some override each other, some are scoped to only
    a subset of commands, and none of them should depend on the order
    they were typed in. Each of those relationships is asserted here.
    """

    def basic_config(self):
        self.write_config("""\
            profiles:
              desk:
                stack: {x: 40, y: 40, width: 900, height: 650, offset_x: 40, offset_y: 40}
                sessions:
                  work1: {x: 0, y: 0, width: 960, height: 1180}
              other:
                stack: {x: 10, y: 10, width: 800, height: 600, offset_x: 20, offset_y: 20}
                sessions: {}
            """)

    def test_option_order_does_not_matter(self):
        self.basic_config()
        first = self.run_xtm("--profile", "desk", "--list")
        second = self.run_xtm("--list", "--profile", "desk")
        self.assertEqual(first.returncode, 0, first.stderr)
        self.assertEqual(first.stdout, second.stdout)

    def test_short_and_long_forms_produce_identical_output(self):
        self.basic_config()
        long_form = self.run_xtm("--profile", "desk", "--list")
        short_form = self.run_xtm("-p", "desk", "-l")
        self.assertEqual(long_form.stdout, short_form.stdout)

    def test_clustered_short_flags_are_accepted(self):
        self.basic_config()
        result = self.run_xtm("-p", "desk", "-qjl")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)["profile"], "desk")

    def test_two_actions_at_once_is_a_usage_error(self):
        self.basic_config()
        result = self.run_xtm("--list", "--reset")
        self.assertEqual(result.returncode, 2)
        self.assertIn("not allowed with", result.stderr)

    def test_explicit_config_beats_the_environment_variable(self):
        self.basic_config()
        elsewhere = self.tmp / "elsewhere.yaml"
        elsewhere.write_text(
            "profiles:\n  fromflag:\n    sessions: {}\n")
        result = self.run_xtm("--config", str(elsewhere), "--list-profiles")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("fromflag", result.stdout)
        self.assertNotIn("desk", result.stdout)

    def test_explicit_state_dir_beats_the_environment_variable(self):
        self.basic_config()
        other_state = self.tmp / "otherstate"
        self.run_xtm("--profile", "desk", "--state-dir", str(other_state),
                     "--current-profile")
        self.assertTrue(any(other_state.glob("current_profile.*")))
        # The environment-variable location must be untouched, proving the
        # flag replaced it rather than being written to as well.
        env_state = self.tmp / "state"
        self.assertFalse(env_state.exists() and
                         any(env_state.glob("current_profile.*")))

    def test_explicit_profile_beats_auto(self):
        self.write_config("""\
            profiles:
              desk:
                match: {hostname: "*"}
                stack: {x: 40, y: 40, width: 900, height: 650, offset_x: 40, offset_y: 40}
                sessions: {}
              chosen:
                stack: {x: 10, y: 10, width: 800, height: 600, offset_x: 20, offset_y: 20}
                sessions: {}
            """)
        result = self.run_xtm("--auto", "--profile", "chosen", "--current-profile")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), "chosen")

    def test_auto_overrides_a_saved_profile(self):
        self.write_config("""\
            profiles:
              desk:
                stack: {x: 40, y: 40, width: 900, height: 650, offset_x: 40, offset_y: 40}
                sessions: {}
              matched:
                match: {hostname: "*"}
                stack: {x: 10, y: 10, width: 800, height: 600, offset_x: 20, offset_y: 20}
                sessions: {}
            """)
        self.run_xtm("--profile", "desk", "--current-profile")
        saved = self.run_xtm("--current-profile")
        self.assertEqual(saved.stdout.strip(), "desk")
        auto = self.run_xtm("--auto", "--list")
        self.assertIn("Profile: matched", auto.stdout)

    def test_log_level_overrides_both_debug_and_quiet(self):
        self.basic_config()
        result = self.run_xtm("-p", "desk", "--debug", "--quiet",
                              "--log-level", "warning", "--list")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn("run:", result.stderr)
        self.assertEqual(result.stderr.strip(), "")

    def test_debug_wins_over_quiet_when_no_explicit_level(self):
        self.basic_config()
        result = self.run_xtm("-p", "desk", "--quiet", "--debug", "--list")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("run:", result.stderr)

    def test_json_and_quiet_leave_stdout_purely_machine_readable(self):
        self.basic_config()
        result = self.run_xtm("-p", "desk", "--json", "--quiet", "--list")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr.strip(), "")
        json.loads(result.stdout)   # must parse with nothing else mixed in

    def test_json_has_no_effect_on_an_action_command(self):
        self.basic_config()
        result = self.run_xtm("-p", "desk", "--json", "--set",
                              "logs", "1,2,300,400")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), "")
        self.assertIn("logs", self.config_text())

    def test_dry_run_and_verify_combine_without_changing_anything(self):
        self.basic_config()
        self.add_session("work1", tty="/dev/null", attached="1", pid=1)
        self.add_window("work1", 5, 6, 960, 1180)
        before = self.config_text()
        result = self.run_xtm("-p", "desk", "--dry-run", "--verify", "--reset")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(before, self.config_text())
        self.assertIn("[dry-run]", result.stderr)

    def test_yes_skips_the_close_all_confirmation(self):
        self.basic_config()
        self.add_session("work1", tty="/dev/null", attached="1", pid=1)
        result = self.run_xtm("-p", "desk", "--close-all", "--yes")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn("[y/N]", result.stdout)
        self.assertEqual(self.rows("sessions.txt"), [])

    def test_no_frame_compensation_changes_what_is_captured(self):
        self.basic_config()
        self.env["XTM_FAKE_FRAME_EXTENTS"] = "4, 4, 20, 4"
        self.add_session("work1", tty="/dev/null", attached="1", pid=1)
        self.add_window("work1", 104, 120, 960, 1180)
        self.run_xtm("-p", "desk", "--update-profile", "--capture-new")
        compensated = self.config_text()
        self.run_xtm("-p", "desk", "--update-profile",
                     "--no-frame-compensation")
        raw = self.config_text()
        self.assertIn("x: 100", compensated)
        self.assertIn("y: 100", compensated)
        self.assertIn("x: 104", raw)
        self.assertIn("y: 120", raw)


class TestEffectiveProfileMarker(IntegrationBase):

    def test_auto_marks_the_profile_the_run_acts_on(self):
        """`--auto` ignores the saved profile, but the list still starred
        the saved one: it marked a profile the run was not going to touch."""
        self.write_config("""\
            profiles:
              default:
                sessions: {}
              desk:
                sessions: {}
              laptop:
                match: {hostname: box1}
                sessions: {}
            """)
        self.run_xtm("--hostname", "box1", "desk", "-C")
        result = self.run_xtm("--hostname", "box1", "--auto", "--list-profiles")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("laptop *", result.stdout)
        self.assertNotIn("desk *", result.stdout)
        payload = json.loads(self.run_xtm("--hostname", "box1", "--auto",
                                          "--list-profiles", "--json").stdout)
        self.assertEqual(payload["effective"], "laptop")
        self.assertEqual(payload["current"], "desk")


class TestCheck(IntegrationBase):
    """--check says whether `xtm` would bring the profile up cleanly."""

    def config_with(self, slot):
        self.write_config("""\
            profiles:
              default:
                sessions:
                  %s
            """ % (slot,))

    def checks(self, *extra, **kwargs):
        result = self.run_xtm("--check", "--json", *extra, **kwargs)
        payload = json.loads(result.stdout)
        return result.returncode, payload, dict(
            ((c["check"], c["status"]), c["detail"]) for c in payload["checks"])

    def test_a_healthy_profile_passes(self):
        self.config_with("work: {x: 0, y: 0, width: 900, height: 700, cwd: %s}"
                         % (self.tmp,))
        code, payload, found = self.checks(env={"XTM_FAKE_MONITORS": "eDP-1:1440x900+0+0"})
        self.assertEqual(code, 0)
        self.assertTrue(payload["ok"])
        self.assertIn(("slot work", "ok"), found)
        self.assertIn(("monitors", "ok"), found)

    def test_a_missing_cwd_fails(self):
        self.config_with("work: {x: 0, y: 0, width: 900, height: 700, "
                         "cwd: /no/such/dir}")
        code, payload, found = self.checks()
        self.assertEqual(code, 1)
        self.assertFalse(payload["ok"])
        self.assertIn("does not exist", found[("slot work", "fail")])

    def test_a_command_not_on_path_warns(self):
        self.config_with("logs: {x: 0, y: 0, width: 900, height: 700, "
                         "command: journalctl -f}")
        code, _payload, found = self.checks()
        self.assertEqual(code, 0)
        self.assertIn("journalctl is not on PATH", found[("slot logs", "warn")])

    def test_a_shell_line_is_not_guessed_at(self):
        self.config_with("x: {x: 0, y: 0, width: 900, height: 700, "
                         "command: 'cd /tmp && make'}")
        _code, _payload, found = self.checks()
        self.assertIn(("slot x", "ok"), found)

    def test_an_off_screen_slot_fails(self):
        self.config_with("right: {x: 1920, y: 0, width: 900, height: 700}")
        code, _payload, found = self.checks(
            env={"XTM_FAKE_MONITORS": "eDP-1:1440x900+0+0"})
        self.assertEqual(code, 1)
        self.assertIn("off screen", found[("slot right", "fail")])

    def test_no_display_fails(self):
        self.config_with("work: {x: 0, y: 0, width: 900, height: 700}")
        env = dict(self.env)
        env.pop("DISPLAY")
        self.env = env
        code, _payload, found = self.checks()
        self.assertEqual(code, 1)
        self.assertIn(("display", "fail"), found)

    def test_the_profile_line_says_why_that_profile(self):
        self.write_config("""\
            profiles:
              default:
                sessions: {}
              desk:
                sessions:
                  work: {x: 0, y: 0, width: 900, height: 700}
            """)
        _code, _payload, found = self.checks("-p", "desk")
        self.assertEqual("desk, named on the command line, 1 slot(s)",
                         found[("profile", "ok")])
        self.run_xtm("desk", "-C")
        _code, _payload, found = self.checks()
        self.assertEqual("desk, saved for this machine, 1 slot(s)",
                         found[("profile", "ok")])

    def test_a_profile_matched_to_this_hostname_says_so(self):
        self.write_config("""\
            profiles:
              default:
                sessions: {}
              laptop:
                match: {hostname: box1}
                sessions: {}
            """)
        _code, _payload, found = self.checks("--hostname", "box1")
        self.assertEqual("laptop, matched to this hostname, 0 slot(s)",
                         found[("profile", "ok")])

    def test_a_command_on_path_or_past_splitting_is_not_reported(self):
        self.write_config("""\
            profiles:
              default:
                sessions:
                  there: {x: 0, y: 0, width: 900, height: 700, command: /bin/sh -c true}
                  quoted: {x: 0, y: 0, width: 900, height: 700, command: "vim 'unclosed"}
            """)
        _code, _payload, found = self.checks()
        self.assertIn(("slot there", "ok"), found)
        self.assertIn(("slot quoted", "ok"), found)

    def test_a_tmux_older_than_3_2_warns(self):
        self.config_with("work: {x: 0, y: 0, width: 900, height: 700}")
        _code, _payload, found = self.checks(env={"XTM_FAKE_TMUX_VERSION": "3.1"})
        self.assertEqual("3.1: a failing start command closes its window without "
                         "a trace; 3.2 or newer keeps it on screen",
                         found[("tmux", "warn")])

    def test_a_tmux_that_reports_no_version_fails(self):
        self.config_with("work: {x: 0, y: 0, width: 900, height: 700}")
        code, _payload, found = self.checks(env={"XTM_FAKE_TMUX_REFUSE": "-V"})
        self.assertEqual(1, code)
        self.assertEqual("not found on PATH, or did not report a version",
                         found[("tmux", "fail")])

    def test_the_text_report_counts_problems(self):
        self.config_with("work: {x: 0, y: 0, width: 900, height: 700, "
                         "cwd: /no/such/dir}")
        result = self.run_xtm("--check")
        self.assertEqual(result.returncode, 1)
        self.assertIn("1 problem(s)", result.stdout)


def geometry_check(test):
    """The geometry-tool line of --check, as (status, detail) pairs."""
    result = test.run_xtm("--check", "--json")
    return [(c["status"], c["detail"]) for c in json.loads(result.stdout)["checks"]
            if c["check"] == "geometry tool"]


class TestCheckWithNoGeometryTool(IntegrationBase):
    TOOLS = ["tmux", "xterm"]

    def test_it_warns_what_does_not_work(self):
        self.assertEqual([("warn", "none of xdotool, wmctrl, xwininfo: placement "
                                   "works, but --update-profile, --verify and "
                                   "--focus do not")], geometry_check(self))


class TestCheckWithOnlyXwininfo(IntegrationBase):
    TOOLS = ["tmux", "xterm", "xwininfo"]

    def test_it_warns_that_focus_needs_another_tool(self):
        self.assertEqual([("warn", "xwininfo reads positions but cannot raise a "
                                   "window, so --focus needs xdotool or wmctrl")],
                         geometry_check(self))
