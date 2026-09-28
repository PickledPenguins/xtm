"""Malformed configs and documented behaviors."""

import json
import textwrap

from tests.support import (
    IntegrationBase,
    socket_hostname,
)


class TestMalformedConfig(IntegrationBase):
    """Every way a hand-edited config can be wrong should produce a clear
    message and exit 1, never a traceback."""

    def assert_clean_failure(self, *argv):
        result = self.run_xtm(*argv)
        self.assertEqual(result.returncode, 1,
                         "expected exit 1, got %d: %s"
                         % (result.returncode, result.stderr))
        self.assertNotIn("Traceback", result.stderr)
        self.assertNotIn("Traceback", result.stdout)
        return result

    def test_empty_file(self):
        self.write_config("")
        self.assert_clean_failure("--list")

    def test_file_of_garbage(self):
        self.write_config("\x00\x01 not yaml at all ][}{\n")
        self.assert_clean_failure("--list")

    def test_top_level_is_a_list(self):
        self.write_config("- one\n- two\n")
        self.assert_clean_failure("--list")

    def test_missing_profiles_key(self):
        self.write_config("something_else: {}\n")
        result = self.assert_clean_failure("--list")
        self.assertIn("profiles", result.stderr)

    def test_profiles_is_not_a_mapping(self):
        self.write_config("profiles: 42\n")
        self.assert_clean_failure("--list")

    def test_profile_is_not_a_mapping(self):
        self.write_config("profiles:\n  desk: 42\n")
        result = self.assert_clean_failure("--profile", "desk", "--list")
        self.assertIn("desk", result.stderr)

    def test_session_entry_is_not_a_mapping(self):
        self.write_config("""\
            profiles:
              desk:
                sessions:
                  work1: 7
            """)
        result = self.assert_clean_failure("--profile", "desk", "--list")
        self.assertIn("work1", result.stderr)

    def test_geometry_field_is_a_string(self):
        self.write_config("""\
            profiles:
              desk:
                sessions:
                  work1: {x: wide, y: 0, width: 900, height: 700}
            """)
        result = self.assert_clean_failure("--profile", "desk", "--list")
        self.assertIn("'x'", result.stderr)
        self.assertIn("work1", result.stderr)

    def test_geometry_field_is_missing(self):
        self.write_config("""\
            profiles:
              desk:
                sessions:
                  work1: {x: 0, y: 0, width: 900}
            """)
        result = self.assert_clean_failure("--profile", "desk", "--list")
        self.assertIn("height", result.stderr)

    def test_negative_size_is_rejected(self):
        self.write_config("""\
            profiles:
              desk:
                sessions:
                  work1: {x: 0, y: 0, width: -900, height: 700}
            """)
        result = self.assert_clean_failure("--profile", "desk", "--list")
        self.assertIn("positive", result.stderr)

    def test_session_name_with_a_colon_is_rejected_on_load(self):
        # A name like this could never round-trip through the built-in
        # writer, so it is refused when the file is read rather than
        # producing an unparseable file on the next save.
        self.write_config("""\
            profiles:
              desk:
                sessions:
                  "bad:name": {x: 0, y: 0, width: 900, height: 700}
            """)
        result = self.assert_clean_failure("--profile", "desk", "--list")
        # Assert the invariant (the name is refused), not the exact name in
        # the message, because the two YAML backends recover a different key
        # from this line. PyYAML honors the quotes and reports 'bad:name';
        # the built-in parser splits on the first ':' without regard to
        # quoting, so it reports '"bad'. Both reject the config, which is
        # what this test is for. The parser gap itself is tracked separately
        # -- a quoted key containing ':' cannot occur in a valid config,
        # since session and profile names exclude ':' by charset.
        self.assertIn("Invalid session name", result.stderr)

    def test_profile_name_with_a_space_is_rejected_on_load(self):
        self.write_config('profiles:\n  "bad name":\n    sessions: {}\n')
        self.assert_clean_failure("--list")

    def test_match_block_with_an_unknown_key(self):
        self.write_config("""\
            profiles:
              desk:
                match: {platform: linux}
                sessions: {}
            """)
        result = self.assert_clean_failure("--profile", "desk", "--list")
        self.assertIn("match", result.stderr)
        self.assertIn("hostname", result.stderr)

    def test_match_value_is_not_a_string(self):
        self.write_config("""\
            profiles:
              desk:
                match: {hostname: 7}
                sessions: {}
            """)
        self.assert_clean_failure("--profile", "desk", "--list")

    def test_xterm_args_is_not_a_list(self):
        self.write_config("""\
            profiles:
              desk:
                sessions:
                  work1: {x: 0, y: 0, width: 900, height: 700, xterm_args: "-fa Mono"}
            """)
        result = self.assert_clean_failure("--profile", "desk", "--list")
        self.assertIn("xterm_args", result.stderr)

    def test_command_is_not_a_string(self):
        self.write_config("""\
            profiles:
              desk:
                sessions:
                  work1: {x: 0, y: 0, width: 900, height: 700, command: 12}
            """)
        result = self.assert_clean_failure("--profile", "desk", "--list")
        self.assertIn("command", result.stderr)

    def test_stack_missing_its_offsets(self):
        self.write_config("""\
            profiles:
              desk:
                stack: {x: 40, y: 40, width: 900, height: 650}
                sessions: {}
            """)
        result = self.assert_clean_failure("--profile", "desk", "--list")
        self.assertIn("offset_x", result.stderr)

    def test_validate_reports_every_broken_profile_and_exits_one(self):
        self.write_config("""\
            profiles:
              good:
                sessions: {}
              broken:
                sessions:
                  work1: {x: 0, y: 0, width: 0, height: 700}
            """)
        result = self.run_xtm("--validate")
        self.assertEqual(result.returncode, 1)
        self.assertIn("good", result.stdout)
        self.assertIn("broken", result.stdout)
        self.assertNotIn("Traceback", result.stderr)

    def test_bad_geometry_argument_is_rejected_without_touching_the_config(self):
        self.write_config("""\
            profiles:
              desk:
                sessions: {}
            """)
        # Warm up with a command that WRITES: the reserved "default" profile
        # is persisted by the first such command, and that legitimate change
        # must not be mistaken for a failing --set having touched the file.
        # A reporting command no longer serves here, because reading a config
        # deliberately does not rewrite it.
        self.run_xtm("--profile", "desk", "--set", "warmup", "0,0,100,100")
        before = self.config_text()
        for spec in ("1,2,3", "a,b,c,d", "1,2,0,400", "1,2,300", ""):
            result = self.run_xtm("--profile", "desk", "--set", "s", spec)
            self.assertEqual(result.returncode, 1,
                             "spec %r should have failed" % (spec,))
            self.assertNotIn("Traceback", result.stderr)
        self.assertEqual(before, self.config_text())


class TestDocumentedBehaviors(IntegrationBase):
    """Assertions made in the README that are not covered elsewhere."""

    DESK_HEADER = """\
        profiles:
          desk:
            stack: {x: 40, y: 40, width: 900, height: 650, offset_x: 40, offset_y: 40}
            sessions:
              work1: {x: 0, y: 0, width: 960, height: 1180}
"""

    def desk(self, extra_sessions=""):
        """Write a one-session 'desk' profile, plus any extra session lines.

        `extra_sessions` is appended at the indentation the sessions block
        uses, so callers pass bare 'name: {...}' lines.
        """
        body = textwrap.dedent(self.DESK_HEADER)
        for line in textwrap.dedent(extra_sessions).splitlines():
            if line.strip():
                body += "      " + line.strip() + "\n"
        self.write_config(body)

    def test_detached_status_is_reported(self):
        self.desk()
        self.add_session("work1", tty="/dev/null", attached="0", pid=1)
        result = self.run_xtm("--profile", "desk", "--list")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("detached", result.stdout)

    def test_all_three_statuses_appear_together(self):
        self.write_config("""\
            profiles:
              desk:
                stack: {x: 40, y: 40, width: 900, height: 650, offset_x: 40, offset_y: 40}
                sessions:
                  live: {x: 0, y: 0, width: 900, height: 700}
                  idle: {x: 10, y: 0, width: 900, height: 700}
                  gone: {x: 20, y: 0, width: 900, height: 700}
            """)
        self.add_session("live", tty="/dev/null", attached="1", pid=1)
        self.add_session("idle", tty="/dev/null", attached="0", pid=2)
        result = self.run_xtm("--profile", "desk", "--json", "--list")
        statuses = dict((e["name"], e["status"])
                        for e in json.loads(result.stdout)["sessions"])
        self.assertEqual(statuses, {"live": "attached", "idle": "detached",
                                    "gone": "not running"})

    def test_command_is_not_used_when_attaching_to_an_existing_session(self):
        # tmux new-session -A ignores the command when the session already
        # exists, so the documented behavior is that `command` applies at
        # creation only.
        self.desk(
            "logs: {x: 0, y: 0, width: 900, height: 700, "
            "command: journalctl -f}")
        self.add_session("logs", tty="/dev/null", attached="0", pid=5)
        self.clear_calls()
        result = self.run_xtm("--profile", "desk", "--open", "logs")
        self.assertEqual(result.returncode, 0, result.stderr)
        launched = [json.loads(line) for line in
                    (self.state / "launched.log").read_text().splitlines()
                    if line.strip()]
        self.assertEqual(len(launched), 1)
        # The command is still handed to tmux; tmux is what ignores it when
        # -A attaches. What matters is that xtm does not refuse to attach.
        self.assertIn("new-session", launched[0])
        self.assertIn("-A", launched[0])

    def test_update_profile_keeps_sessions_that_are_not_on_screen(self):
        self.desk("offscreen: {x: 700, y: 800, width: 640, height: 480}")
        self.add_session("work1", tty="/dev/null", attached="1", pid=1)
        self.add_window("work1", 5, 6, 960, 1180)
        result = self.run_xtm("--profile", "desk", "--update-profile", "--capture-new")
        self.assertEqual(result.returncode, 0, result.stderr)
        text = self.config_text()
        self.assertIn("offscreen", text)
        self.assertIn("640", text)   # its recorded size is untouched

    def test_new_profile_inherits_stack_settings(self):
        self.desk()
        self.add_session("work1", tty="/dev/null", attached="1", pid=1)
        self.add_window("work1", 0, 0, 960, 1180)
        result = self.run_xtm("--profile", "desk", "--new-profile", "copy")
        self.assertEqual(result.returncode, 0, result.stderr)
        listing = self.run_xtm("--profile", "copy", "--json", "--list")
        self.assertEqual(listing.returncode, 0, listing.stderr)
        # The inherited stack is the one from desk, not the built-in default.
        self.assertIn("offset_x: 40", self.config_text())
        self.assertIn("copy", self.config_text())

    def test_edit_does_not_revert_an_invalid_result(self):
        self.desk()
        broken = "profiles:\n  desk:\n    sessions:\n      w: {x: 0}\n"
        editor = self.make_editor("breaker.sh", broken)
        result = self.run_xtm("--profile", "desk", "--edit",
                              env={"EDITOR": str(editor)})
        self.assertEqual(result.returncode, 1)
        # The edit is preserved rather than rolled back, so the work is not
        # lost and the file can be corrected on a second pass. The reserved
        # "default" profile is re-added, because the edit removed it.
        text = self.config_text()
        self.assertIn("desk", text)
        self.assertIn("x: 0", text)
        self.assertIn("default", text)

    def test_delete_session_leaves_a_running_session_alone(self):
        self.desk()
        self.add_session("work1", tty="/dev/null", attached="1", pid=1)
        result = self.run_xtm("--profile", "desk", "--delete-session", "work1")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn("work1", self.config_text())
        self.assertEqual([r[0] for r in self.rows("sessions.txt")], ["work1"])

    def test_close_leaves_the_profile_entry_alone(self):
        self.desk()
        self.add_session("work1", tty="/dev/null", attached="1", pid=1)
        result = self.run_xtm("--profile", "desk", "--close", "work1")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("work1", self.config_text())
        self.assertEqual(self.rows("sessions.txt"), [])

    def test_copy_profile_is_a_deep_copy(self):
        self.desk()
        self.run_xtm("--profile", "desk", "--copy-profile", "desk", "clone")
        self.run_xtm("--profile", "clone", "--set", "work1", "9,9,100,100")
        original = self.run_xtm("--profile", "desk", "--json", "--list")
        entry = next(e for e in json.loads(original.stdout)["sessions"]
                     if e["name"] == "work1")
        self.assertEqual(entry["configured"]["x"], 0)

    def test_exit_code_two_is_reserved_for_usage_errors(self):
        self.desk()
        self.assertEqual(self.run_xtm("--nonsuch").returncode, 2)
        self.assertEqual(self.run_xtm("--set", "onlyone").returncode, 2)

    def test_data_goes_to_stdout_and_logs_go_to_stderr(self):
        self.desk()
        result = self.run_xtm("--profile", "desk", "--debug", "--list-profiles")
        self.assertIn("desk", result.stdout)
        self.assertIn("[DEBUG", result.stderr)
        self.assertNotIn("[DEBUG", result.stdout)


class TestMachineVisibility(IntegrationBase):
    """Profiles bound to other machines are hidden unless --all is given."""

    def mixed_config(self):
        self.write_config("""\
            profiles:
              default:
                stack: {x: 40, y: 40, width: 900, height: 650, offset_x: 40, offset_y: 40}
                sessions: {}
              mine:
                match: {hostname: "%s"}
                stack: {x: 10, y: 10, width: 800, height: 600, offset_x: 20, offset_y: 20}
                sessions: {}
              theirs:
                match: {hostname: nosuchhost-xyz}
                sessions: {}
            """ % (socket_hostname(),))

    def test_close_all_with_all_covers_only_this_machines_profiles(self):
        """Pins the scope --all actually gives --close-all.

        The help text used to promise "every profile", while the code passes
        include_all=False and covers only profiles visible here. The code is
        the safer behavior -- a run must not kill sessions claimed by a
        layout bound to a different host -- so the text was corrected to
        match, and this test stops the two drifting apart again.
        """
        self.write_config("""\
            profiles:
              default:
                stack: {x: 40, y: 40, width: 900, height: 650, offset_x: 40, offset_y: 40}
                sessions: {}
              mine:
                match: {hostname: "%s"}
                sessions:
                  local: {x: 0, y: 0, width: 800, height: 600}
              theirs:
                match: {hostname: nosuchhost-xyz}
                sessions:
                  remote: {x: 0, y: 0, width: 800, height: 600}
            """ % (socket_hostname(),))
        self.add_session("local")
        self.add_session("remote")

        result = self.run_xtm("--all", "--close-all", "--yes")
        self.assertEqual(result.returncode, 0, result.stderr)

        surviving = [row[0] for row in self.rows("sessions.txt")]
        # "local" belongs to a profile visible here, so --all reaches it.
        self.assertNotIn("local", surviving)
        # "remote" belongs to a profile bound to another host, so it does not.
        self.assertIn("remote", surviving,
                      "--all --close-all killed a session claimed only by a "
                      "profile bound to another machine")

    def test_all_help_text_matches_close_all_behavior(self):
        """The help must not re-acquire the promise the code does not keep."""
        help_text = self.run_xtm("--help").stdout
        flat = " ".join(help_text.split())
        self.assertIn("VISIBLE ON THIS MACHINE", flat)

    def test_other_machines_profiles_are_hidden(self):
        self.mixed_config()
        result = self.run_xtm("--list-profiles")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("mine", result.stdout)
        self.assertIn("default", result.stdout)
        self.assertNotIn("theirs", result.stdout)

    def test_hidden_count_is_reported(self):
        self.mixed_config()
        result = self.run_xtm("--list-profiles")
        self.assertIn("hidden", result.stderr)

    def test_all_reveals_them(self):
        self.mixed_config()
        result = self.run_xtm("--all", "--list-profiles")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("theirs", result.stdout)

    def test_hidden_profile_cannot_be_selected(self):
        self.mixed_config()
        result = self.run_xtm("theirs")
        self.assertEqual(result.returncode, 1)
        self.assertIn("another machine", result.stderr)
        self.assertIn("--all", result.stderr)

    def test_all_allows_selecting_a_hidden_profile(self):
        self.mixed_config()
        result = self.run_xtm("--all", "theirs", "--list")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Profile: theirs", result.stdout)

    def test_json_listing_reports_hidden_count_and_global_flag(self):
        self.mixed_config()
        payload = json.loads(self.run_xtm("--list-profiles", "--json").stdout)
        self.assertEqual(payload["hidden"], 1)
        names = dict((p["name"], p) for p in payload["profiles"])
        self.assertTrue(names["default"]["global"])
        self.assertFalse(names["mine"]["global"])

    def test_validate_checks_hidden_profiles_too(self):
        self.write_config("""\
            profiles:
              default:
                sessions: {}
              theirs:
                match: {hostname: nosuchhost-xyz}
                sessions:
                  bad: {x: 0, y: 0, width: 0, height: 10}
            """)
        result = self.run_xtm("--validate")
        self.assertEqual(result.returncode, 1)
        self.assertIn("theirs", result.stdout)

    def test_a_saved_profile_that_became_hidden_is_explained(self):
        self.mixed_config()
        self.run_xtm("mine", "--current-profile")
        # Re-bind the saved profile to a machine that is not this one, as
        # would happen after editing the config or moving it to a new host.
        self.write_config("""\
            profiles:
              default:
                sessions: {}
              mine:
                match: {hostname: nosuchhost-xyz}
                sessions: {}
            """)
        result = self.run_xtm("--list")
        self.assertEqual(result.returncode, 1)
        self.assertIn("another machine", result.stderr)


class TestMakeGlobal(IntegrationBase):
    """Removing a profile's machine binding."""

    def bound_config(self):
        self.write_config("""\
            profiles:
              default:
                sessions: {}
              desk:
                match: {hostname: "%s"}
                stack: {x: 10, y: 10, width: 800, height: 600, offset_x: 20, offset_y: 20}
                sessions: {}
            """ % (socket_hostname(),))

    def test_removes_the_match_block(self):
        self.bound_config()
        result = self.run_xtm("--make-global", "desk")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn("match", self.config_text())
        self.assertNotIn("hostname", self.config_text())

    def test_a_global_profile_is_visible_on_any_machine(self):
        self.write_config("""\
            profiles:
              default:
                sessions: {}
              desk:
                match: {hostname: nosuchhost-xyz}
                sessions: {}
            """)
        self.assertNotIn("desk", self.run_xtm("--list-profiles").stdout)
        self.run_xtm("--all", "--make-global", "desk")
        self.assertIn("desk", self.run_xtm("--list-profiles").stdout)

    def test_already_global_is_not_an_error(self):
        self.bound_config()
        self.run_xtm("--make-global", "desk")
        result = self.run_xtm("--make-global", "desk")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("already global", result.stderr)

    def test_missing_profile_exits_one(self):
        self.bound_config()
        result = self.run_xtm("--make-global", "nosuch")
        self.assertEqual(result.returncode, 1)

    def test_dry_run_changes_nothing(self):
        self.bound_config()
        before = self.config_text()
        result = self.run_xtm("--dry-run", "--make-global", "desk")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(before, self.config_text())

    def test_a_global_profile_is_never_auto_selected(self):
        # Auto-selection needs a positive signal that a profile belongs
        # here; being available everywhere is not one.
        self.write_config("""\
            profiles:
              default:
                sessions: {}
              everywhere:
                sessions: {}
            """)
        result = self.run_xtm("--auto", "--list")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Profile: default", result.stdout)
