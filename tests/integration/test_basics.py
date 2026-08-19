"""Running xtm as a subprocess: startup, profiles, validation."""

import json

from tests.support import (
    IntegrationBase,
    xtm,
)


class TestBasicInvocation(IntegrationBase):

    def test_version(self):
        result = self.run_xtm("--version")
        self.assertEqual(result.returncode, 0)
        self.assertIn(xtm.__version__, result.stdout)

    def test_help(self):
        result = self.run_xtm("--help")
        self.assertEqual(result.returncode, 0)
        self.assertIn("--reset-all", result.stdout)

    def test_first_run_creates_config_and_lists(self):
        result = self.run_xtm()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Profile: default", result.stdout)
        self.assertIn("profiles:", self.config_text())

    def test_bare_invocation_uses_default_profile(self):
        result = self.run_xtm()
        self.assertIn("Profile: default", result.stdout)
        self.assertIn("creating a default one", result.stderr)

    def test_info_logging_on_by_default(self):
        result = self.run_xtm("-l")
        self.assertTrue(result.stderr.strip())

    def test_quiet_silences_informational_logging(self):
        self.run_xtm("-l")  # create the config first
        result = self.run_xtm("-q", "-l")
        self.assertEqual(result.stderr.strip(), "")
        self.assertIn("Profile: default", result.stdout)

    def test_debug_logs_subprocess_calls(self):
        result = self.run_xtm("-d", "-l")
        self.assertIn("run: tmux", result.stderr)

    def test_log_file_off_by_default(self):
        self.run_xtm("-l")
        self.assertEqual(list((self.tmp / "cfg").glob("*.log")), [])

    def test_log_file_captures_debug_even_when_console_is_quiet(self):
        log_path = self.tmp / "run.log"
        result = self.run_xtm("-q", "--log-file", str(log_path), "-l")
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stderr.strip(), "")
        self.assertIn("run: tmux", log_path.read_text())

    def test_invalid_arguments_exit_two(self):
        self.assertEqual(self.run_xtm("--reset", "--reset-all").returncode, 2)

    def test_unknown_profile_exits_one(self):
        result = self.run_xtm("-p", "nosuch", "-l")
        self.assertEqual(result.returncode, 1)
        self.assertIn("does not exist", result.stderr)


class TestProfileManagement(IntegrationBase):

    def test_set_add_and_update(self):
        self.assertEqual(self.run_xtm("-s", "work1", "10,20,900,700").returncode, 0)
        self.assertIn("work1", self.config_text())
        result = self.run_xtm("-s", "work1", "30,40,800,600")
        self.assertEqual(result.returncode, 0)
        self.assertIn("Updated slot work1", result.stderr)
        self.assertIn("800", self.config_text())

    def test_set_preserves_launch_settings(self):
        self.run_xtm("-s", "logs", "0,0,900,700")
        text = self.config_text().replace(
            "      height: 700", "      height: 700\n      command: htop")
        (self.tmp / "cfg" / "config.yaml").write_text(text)
        self.run_xtm("-s", "logs", "5,5,100,100")
        self.assertIn("command: htop", self.config_text())

    def test_set_rejects_bad_geometry(self):
        result = self.run_xtm("-s", "work1", "10,20,900")
        self.assertEqual(result.returncode, 1)
        self.assertIn("Geometry must be", result.stderr)

    def test_delete_session(self):
        self.run_xtm("-s", "work1", "0,0,900,700")
        result = self.run_xtm("-D", "work1")
        self.assertEqual(result.returncode, 0)
        self.assertNotIn("work1", self.config_text())

    def test_delete_missing_session_exits_one(self):
        self.run_xtm("-l")
        self.assertEqual(self.run_xtm("-D", "ghost").returncode, 1)

    def test_copy_rename_and_delete_profile(self):
        self.run_xtm("-s", "work1", "0,0,900,700")
        self.assertEqual(self.run_xtm("--copy-profile", "default", "two").returncode, 0)
        self.assertIn("two", self.config_text())
        self.assertEqual(self.run_xtm("--rename-profile", "two", "three").returncode, 0)
        self.assertIn("three", self.config_text())
        self.assertNotIn("two:", self.config_text())
        self.assertEqual(self.run_xtm("-y", "--delete-profile", "three").returncode, 0)
        self.assertNotIn("three", self.config_text())

    def test_copy_to_existing_name_exits_one(self):
        self.run_xtm("-l")
        self.assertEqual(
            self.run_xtm("--copy-profile", "default", "default").returncode, 1)

    def test_rename_missing_profile_exits_one(self):
        self.run_xtm("-l")
        self.assertEqual(
            self.run_xtm("--rename-profile", "ghost", "x").returncode, 1)

    def test_copy_drops_match_block(self):
        self.write_config("""\
            profiles:
              a:
                match:
                  hostname: "*"
                stack: {x: 1, y: 1, width: 10, height: 10, offset_x: 1, offset_y: 1}
                sessions: {}
            """)
        self.run_xtm("-p", "a", "--copy-profile", "a", "b")
        config = self.config_text()
        self.assertEqual(config.count("hostname"), 1)

    def test_rename_updates_current_profile_state(self):
        self.run_xtm("-l")
        self.run_xtm("--copy-profile", "default", "spare")
        self.run_xtm("-p", "spare", "-C")
        self.run_xtm("--rename-profile", "spare", "renamed")
        result = self.run_xtm("-C")
        self.assertEqual(result.stdout.strip(), "renamed")

    def test_delete_current_profile_clears_state(self):
        self.run_xtm("-l")
        self.run_xtm("--copy-profile", "default", "spare")
        self.run_xtm("-p", "spare", "-C")
        self.run_xtm("-y", "--delete-profile", "spare")
        result = self.run_xtm("-C")
        self.assertEqual(result.returncode, 0)
        self.assertNotIn("spare", result.stdout)

    def test_list_profiles_marks_the_effective_profile(self):
        self.run_xtm("-l")
        self.run_xtm("--copy-profile", "default", "spare")
        result = self.run_xtm("-P")
        self.assertIn("default *", result.stdout)
        self.assertIn("spare", result.stdout)
        self.run_xtm("-p", "spare", "-C")
        self.assertIn("spare *", self.run_xtm("-P").stdout)

    def test_list_profiles_json_distinguishes_current_from_effective(self):
        self.run_xtm("-l")
        payload = json.loads(self.run_xtm("-P", "-j").stdout)
        self.assertIsNone(payload["current"])
        self.assertEqual(payload["effective"], "default")

    def test_list_profiles_json(self):
        self.run_xtm("-l")
        result = self.run_xtm("-P", "-j")
        payload = json.loads(result.stdout)
        self.assertEqual([p["name"] for p in payload["profiles"]], ["default"])

    def test_commands_that_do_not_need_a_profile_work_without_one(self):
        self.write_config("""\
            profiles:
              onlyone:
                sessions: {}
            """)
        result = self.run_xtm("-P")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("onlyone", result.stdout)

    def test_missing_reserved_profile_is_recreated(self):
        # A config without "default" (written by 0.3, or edited by hand)
        # gains it on load, so resolution always has a last resort.
        self.write_config("""\
            profiles:
              onlyone:
                sessions: {}
            """)
        result = self.run_xtm("-l")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("default", self.config_text())
        self.assertIn("Profile: default", result.stdout)

    def test_auto_profile_selection_by_hostname(self):
        self.write_config("""\
            profiles:
              default:
                sessions: {}
              matched:
                match:
                  hostname: "*"
                sessions: {}
            """)
        result = self.run_xtm("-a", "-l")
        self.assertIn("Profile: matched", result.stdout)


class TestValidation(IntegrationBase):

    def test_valid_config_passes(self):
        self.run_xtm("-l")
        result = self.run_xtm("-V")
        self.assertEqual(result.returncode, 0)
        self.assertIn("OK", result.stdout)

    def test_invalid_config_fails_with_detail(self):
        self.write_config("""\
            profiles:
              broken:
                sessions:
                  a:
                    x: 0
                    y: 0
                    width: 0
                    height: 700
            """)
        result = self.run_xtm("-V")
        self.assertEqual(result.returncode, 1)
        self.assertIn("INVALID", result.stdout)

    def test_validate_json(self):
        self.run_xtm("-l")
        payload = json.loads(self.run_xtm("-V", "-j").stdout)
        self.assertTrue(payload["valid"])

    def test_tab_indented_config_reports_clearly(self):
        self.write_config("profiles:\n\tbroken:\n\t\tsessions: {}\n")
        result = self.run_xtm("-l")
        self.assertEqual(result.returncode, 1)
        if not xtm.HAVE_PYYAML:
            self.assertIn("tabs", result.stderr)

    def test_documented_flow_style_config_loads(self):
        self.write_config("""\
            profiles:
              twomon:
                stack: {x: 20, y: 20, width: 700, height: 900, offset_x: 30, offset_y: 30}
                sessions:
                  work1: {x: 0, y: 0, width: 1000, height: 1400}
            """)
        result = self.run_xtm("-p", "twomon", "-l")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("1000x1400", result.stdout)

    def test_documented_inline_comment_config_loads(self):
        self.write_config("""\
            profiles:
              onemon:
                stack:                 # default slot for unconfigured sessions
                  x: 40                # left edge
                  y: 40
                  width: 900
                  height: 650
                  offset_x: 40         # diagonal offset per stacked window
                  offset_y: 40
                sessions: {}
            """)
        result = self.run_xtm("-p", "onemon", "-l")
        self.assertEqual(result.returncode, 0, result.stderr)


class TestPositionalProfile(IntegrationBase):
    """`xtm desk` as the primary way to switch profiles."""

    def two_profiles(self):
        self.write_config("""\
            profiles:
              default:
                stack: {x: 40, y: 40, width: 900, height: 650, offset_x: 40, offset_y: 40}
                sessions: {}
              desk:
                stack: {x: 10, y: 10, width: 800, height: 600, offset_x: 20, offset_y: 20}
                sessions:
                  work1: {x: 0, y: 0, width: 900, height: 700}
            """)

    def test_positional_switches_and_persists(self):
        self.two_profiles()
        result = self.run_xtm("desk")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Profile: desk", result.stdout)
        self.assertEqual(self.run_xtm("--current-profile").stdout.strip(), "desk")

    def test_positional_matches_the_profile_flag_exactly(self):
        self.two_profiles()
        positional = self.run_xtm("desk", "--list")
        flag = self.run_xtm("--profile", "desk", "--list")
        self.assertEqual(positional.stdout, flag.stdout)

    def test_positional_combines_with_an_action(self):
        self.two_profiles()
        result = self.run_xtm("desk", "--set", "logs", "1,2,300,400")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("logs", self.config_text())
        self.assertEqual(self.run_xtm("--current-profile").stdout.strip(), "desk")

    def test_positional_before_or_after_a_value_taking_action(self):
        # argparse intermixing is the risk here: the positional must not
        # be swallowed as one of the action's own arguments.
        self.two_profiles()
        first = self.run_xtm("desk", "--set", "a", "1,2,300,400")
        self.assertEqual(first.returncode, 0, first.stderr)
        second = self.run_xtm("--set", "b", "1,2,300,400", "desk")
        self.assertEqual(second.returncode, 0, second.stderr)
        text = self.config_text()
        self.assertIn("a:", text)
        self.assertIn("b:", text)

    def test_positional_with_two_argument_actions(self):
        self.two_profiles()
        result = self.run_xtm("desk", "--copy-profile", "desk", "clone")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("clone", self.config_text())

    def test_both_spellings_together_is_a_usage_error(self):
        self.two_profiles()
        result = self.run_xtm("desk", "--profile", "default")
        self.assertEqual(result.returncode, 2)
        self.assertIn("not both", result.stderr)

    def test_both_spellings_rejected_even_when_they_agree(self):
        self.two_profiles()
        result = self.run_xtm("desk", "--profile", "desk")
        self.assertEqual(result.returncode, 2)

    def test_unknown_name_suggests_a_session_when_one_matches(self):
        self.two_profiles()
        result = self.run_xtm("work1")
        self.assertEqual(result.returncode, 1)
        self.assertIn("--open work1", result.stderr)
        self.assertIn("--focus work1", result.stderr)

    def test_unknown_name_lists_the_known_profiles(self):
        self.two_profiles()
        result = self.run_xtm("nosuch")
        self.assertEqual(result.returncode, 1)
        self.assertIn("desk", result.stderr)
        self.assertIn("default", result.stderr)


class TestReservedDefaultProfile(IntegrationBase):
    """The "default" profile is created, kept, and always global."""

    def test_created_on_first_run(self):
        result = self.run_xtm("--list-profiles")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("default", result.stdout)

    def test_recreated_when_missing(self):
        self.write_config("profiles:\n  desk:\n    sessions: {}\n")
        result = self.run_xtm("--list-profiles")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("default", self.config_text())

    def test_cannot_be_deleted(self):
        self.run_xtm("--list")
        result = self.run_xtm("--yes", "--delete-profile", "default")
        self.assertEqual(result.returncode, 1)
        self.assertIn("reserved", result.stderr)
        self.assertIn("default", self.config_text())

    def test_cannot_be_renamed(self):
        self.run_xtm("--list")
        result = self.run_xtm("--rename-profile", "default", "other")
        self.assertEqual(result.returncode, 1)
        self.assertIn("reserved", result.stderr)

    def test_cannot_be_renamed_onto(self):
        self.run_xtm("--list")
        self.run_xtm("--copy-profile", "default", "spare")
        result = self.run_xtm("--rename-profile", "spare", "default")
        self.assertEqual(result.returncode, 1)
        self.assertIn("reserved", result.stderr)

    def test_cannot_be_copied_onto(self):
        self.run_xtm("--list")
        self.run_xtm("--copy-profile", "default", "spare")
        result = self.run_xtm("--copy-profile", "spare", "default")
        self.assertEqual(result.returncode, 1)
        self.assertIn("reserved", result.stderr)

    def test_cannot_be_machine_specific(self):
        self.write_config("""\
            profiles:
              default:
                match: {hostname: "*"}
                sessions: {}
            """)
        result = self.run_xtm("--validate")
        self.assertEqual(result.returncode, 1)
        self.assertIn("reserved", result.stdout + result.stderr)

    def test_contents_remain_editable(self):
        self.run_xtm("--list")
        result = self.run_xtm("default", "--set", "work1", "1,2,300,400")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("work1", self.config_text())

    def test_is_the_last_resort_of_resolution(self):
        self.write_config("""\
            profiles:
              default:
                sessions: {}
              elsewhere:
                match: {hostname: nosuchhost-xyz}
                sessions: {}
            """)
        result = self.run_xtm("--list")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Profile: default", result.stdout)
