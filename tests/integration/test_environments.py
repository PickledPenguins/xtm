"""Degraded environments: missing tools, missing tmux, no PyYAML."""

import json
import signal
import subprocess
import sys
import time

from tests.support import (
    IntegrationBase,
    XTM_SCRIPT,
    xtm,
)


class TestDegradedEnvironments(IntegrationBase):
    TOOLS = ["tmux", "xterm"]  # no xdotool / wmctrl / xwininfo / xprop

    def test_list_works_without_a_window_query_tool(self):
        self.run_xtm("-s", "work1", "0,0,900,700")
        result = self.run_xtm("-l")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Install or locate", result.stdout)

    def test_update_profile_explains_the_missing_tool(self):
        self.run_xtm("-l")
        result = self.run_xtm("-u", "--capture-new")
        self.assertEqual(result.returncode, 1)
        self.assertIn("xdotool", result.stderr)

    def test_focus_explains_the_missing_tool(self):
        result = self.run_xtm("-f", "work1")
        self.assertEqual(result.returncode, 1)

    def test_open_and_reset_still_work(self):
        self.run_xtm("-s", "work1", "0,0,900,700")
        self.assertEqual(self.run_xtm("-o", "work1").returncode, 0)


class TestNoTmux(IntegrationBase):
    TOOLS = ["xdotool", "wmctrl", "xwininfo", "xprop"]  # no tmux, no xterm

    def test_list_degrades_with_a_warning(self):
        result = self.run_xtm("-s", "work1", "0,0,900,700")
        self.assertEqual(result.returncode, 0)
        result = self.run_xtm("-l")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("tmux was not found", result.stderr)
        self.assertIn("not running", result.stdout)


class TestAlternateGeometryTools(IntegrationBase):
    """The three window-query backends must agree with one another."""

    def _capture_with(self, tool):
        for name in ("xdotool", "wmctrl", "xwininfo"):
            if name != tool:
                (self.bin / name).unlink()
        self.run_xtm("-l")
        self.add_window("work1", 321, 654, 800, 600)
        result = self.run_xtm("-u", "--capture-new")
        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(self.run_xtm("-l", "-j").stdout)
        return [s for s in payload["sessions"] if s["name"] == "work1"][0]

    def test_xdotool_backend(self):
        entry = self._capture_with("xdotool")
        self.assertEqual(entry["configured"],
                         {"x": 321, "y": 654, "width": 800, "height": 600})

    def test_wmctrl_backend(self):
        entry = self._capture_with("wmctrl")
        self.assertEqual(entry["configured"],
                         {"x": 321, "y": 654, "width": 800, "height": 600})

    def test_xwininfo_backend(self):
        entry = self._capture_with("xwininfo")
        self.assertEqual(entry["configured"],
                         {"x": 321, "y": 654, "width": 800, "height": 600})


class TestEditor(IntegrationBase):

    def test_edit_runs_editor_and_validates(self):
        editor = self.tmp / "fake-editor"
        editor.write_text("#!/bin/sh\nprintf '' >> \"$1\"\n")
        editor.chmod(0o755)
        result = self.run_xtm("-e", env={"EDITOR": str(editor)})
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("OK", result.stdout)

    def test_edit_reports_an_invalid_result(self):
        editor = self.make_editor(
            "breaking-editor",
            "profiles:\n  p:\n    stack:\n      x: nope\n")
        result = self.run_xtm("-e", env={"EDITOR": str(editor)})
        self.assertEqual(result.returncode, 1)
        self.assertIn("INVALID", result.stdout)

    def test_missing_editor_exits_one(self):
        result = self.run_xtm("-e", env={"EDITOR": "/nonexistent/editor"})
        self.assertEqual(result.returncode, 1)

    def make_argument_editor(self, name, profile_name):
        """An editor that writes to its LAST argument, not its first.

        The config path arrives after whatever arguments the setting
        carried, so writing to "$1" would silently write to the flag
        instead and prove nothing about how the setting was split.
        """
        path = self.tmp / name
        path.write_text(
            "#!/bin/sh\n"
            "for f in \"$@\"; do :; done\n"
            "printf 'profiles:\\n  %s:\\n    sessions: {}\\n' > \"$f\"\n"
            % (profile_name,))
        path.chmod(0o755)
        return path

    def test_an_editor_with_arguments_is_honoured(self):
        """VISUAL and EDITOR routinely carry arguments.

        "vim -p" and "code --wait" are ordinary settings; treating the
        whole string as one program name made them fail with "Could not
        start editor" rather than opening the configured editor.
        """
        editor = self.make_argument_editor("arg-editor", "edited")
        result = self.run_xtm("-e", env={"EDITOR": "%s --wait" % (editor,)})
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("OK", result.stdout)
        # The edit landed, so the config path really was the last argument.
        self.assertIn("edited", self.config_text())

    def test_visual_beats_editor_and_keeps_its_arguments(self):
        chosen = self.make_argument_editor("visual-editor", "chosen")
        ignored = self.tmp / "must-not-run"
        ignored.write_text("#!/bin/sh\nexit 9\n")
        ignored.chmod(0o755)
        result = self.run_xtm("-e", env={"VISUAL": "%s --wait" % (chosen,),
                                         "EDITOR": str(ignored)})
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("chosen", self.config_text())

    def test_an_unparsable_editor_setting_is_reported(self):
        result = self.run_xtm("-e", env={"EDITOR": 'vim "unterminated'})
        self.assertEqual(result.returncode, 1)
        self.assertIn("editor setting", result.stderr)

    def test_an_empty_editor_setting_is_reported(self):
        result = self.run_xtm("-e", env={"EDITOR": "   "})
        self.assertEqual(result.returncode, 1)
        self.assertIn("editor setting", result.stderr)


class TestInterruptHandling(IntegrationBase):

    def test_sigint_exits_130(self):
        self.run_xtm("-s", "work1", "0,0,900,700")
        env = dict(self.env)
        env["XTM_FAKE_XTERM_NO_ATTACH"] = "1"
        proc = subprocess.Popen(
            [sys.executable, str(XTM_SCRIPT), "-o", "work1"],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            universal_newlines=True, env=env)
        time.sleep(1.0)
        proc.send_signal(signal.SIGINT)
        _out, err = proc.communicate(timeout=30)
        self.assertEqual(proc.returncode, xtm.EXIT_INTERRUPTED)
        self.assertIn("Interrupted", err)


class TestDependencyFreeYamlEndToEnd(IntegrationBase):
    """Exercise complete commands with PyYAML unavailable.

    The built-in YAML reader/writer is the code path that runs on hosts
    where nothing can be installed, so it is verified end to end and not
    only in isolation. PyYAML is hidden from the child process by putting
    a module ahead of it on the import path that raises ImportError.
    """

    def setUp(self):
        IntegrationBase.setUp(self)
        blocker = self.tmp / "noyaml"
        blocker.mkdir()
        (blocker / "yaml.py").write_text(
            "raise ImportError('PyYAML hidden for this test')\n")
        self.env["PYTHONPATH"] = "%s:%s" % (
            blocker, self.env.get("PYTHONPATH", ""))

    def test_pyyaml_really_is_hidden(self):
        # Guards the test itself: if the blocker ever stopped working,
        # every other test in this class would silently fall back to
        # testing PyYAML instead of the built-in parser.
        result = subprocess.run(
            [sys.executable, "-c", "import yaml"],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            universal_newlines=True, env=self.env, timeout=40)
        self.assertNotEqual(result.returncode, 0)

    def test_validates_an_annotated_config_with_flow_mappings(self):
        self.write_config("""\
            profiles:
              desk:            # the layout used at the main desk
                stack: {x: 20, y: 20, width: 700, height: 900, offset_x: 30, offset_y: 30}
                sessions:
                  work1: {x: 0, y: 0, width: 1000, height: 1400}
                  logs:
                    x: 1000
                    y: 0        # top edge
                    width: 900
                    height: 700
            """)
        result = self.run_xtm("--profile", "desk", "--validate")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("desk", result.stdout)

    def test_set_round_trips_through_the_builtin_writer(self):
        self.write_config("""\
            profiles:
              desk:
                stack: {x: 20, y: 20, width: 700, height: 900, offset_x: 30, offset_y: 30}
                sessions: {}
            """)
        first = self.run_xtm("--profile", "desk", "--set", "logs", "10,20,800,600")
        self.assertEqual(first.returncode, 0, first.stderr)
        # Reading the file back with the same parser must yield the values
        # that were written, which is what makes the config durable across
        # runs on a host without PyYAML.
        second = self.run_xtm("--list", "--json")
        self.assertEqual(second.returncode, 0, second.stderr)
        payload = json.loads(second.stdout)
        entry = next(e for e in payload["sessions"] if e["name"] == "logs")
        configured = entry["configured"]
        self.assertEqual(
            [configured["x"], configured["y"],
             configured["width"], configured["height"]],
            [10, 20, 800, 600])

    def test_a_command_containing_a_hash_survives_a_round_trip(self):
        # '#' only starts a comment when preceded by whitespace, and never
        # inside quotes; a launch command relying on either must not be
        # truncated when the config is rewritten.
        self.write_config("""\
            profiles:
              desk:
                stack: {x: 20, y: 20, width: 700, height: 900, offset_x: 30, offset_y: 30}
                sessions:
                  logs:
                    x: 0
                    y: 0
                    width: 900
                    height: 700
                    command: "printf '# not a comment' ; bash"
            """)
        rewrite = self.run_xtm("--profile", "desk", "--set", "other", "1,2,300,400")
        self.assertEqual(rewrite.returncode, 0, rewrite.stderr)
        self.assertIn("# not a comment", self.config_text())
        check = self.run_xtm("--validate")
        self.assertEqual(check.returncode, 0, check.stderr)


class TestHostnameOverride(IntegrationBase):
    """`--hostname` answers "what would this config do on that machine?".

    A profile bound with `match:` is hidden everywhere but the machine it
    names, so before this there was no way to check one from anywhere else:
    `--all` reveals it for listing, but auto-selection -- the thing the
    `match` block is *for* -- could only be exercised by being on that host.
    Writing the fleet example ran straight into it.
    """

    CONFIG = """\
        profiles:
          default:
            sessions: {}
          build:
            match:
              hostname: build-*
            sessions:
              compile: {x: 0, y: 0, width: 900, height: 700}
          desk:
            match:
              hostname: workstation-7
            sessions:
              editor: {x: 0, y: 0, width: 900, height: 700}
        """

    def setUp(self):
        IntegrationBase.setUp(self)
        self.write_config(self.CONFIG)

    def write_config(self, text):
        import textwrap
        path = self.tmp / "cfg" / "config.yaml"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(textwrap.dedent(text))
        return path

    def test_a_bound_profile_is_selectable_as_its_own_machine(self):
        result = self.run_xtm("--hostname", "build-3", "-l")
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertIn("Profile: build", result.stdout)

    def test_it_is_auto_selected_the_way_it_would_be_there(self):
        """The point: `match` is exercised, not bypassed."""
        result = self.run_xtm("--hostname", "workstation-7", "--auto", "-l")
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertIn("Profile: desk", result.stdout)

    def test_visibility_follows_the_named_machine(self):
        result = self.run_xtm("--hostname", "build-1", "-P")
        self.assertIn("build", result.stdout)
        self.assertNotIn("desk", result.stdout)

    def test_an_unmatched_name_falls_back_to_the_reserved_profile(self):
        result = self.run_xtm("--hostname", "somewhere-else", "--auto", "-l")
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertIn("Profile: default", result.stdout)

    def test_the_state_file_follows_it_too(self):
        """Acting as a machine means acting as that machine throughout, so
        the per-machine state file is that machine's as well."""
        self.run_xtm("--hostname", "build-9", "--profile", "build", "-l")
        names = [p.name for p in (self.tmp / "state").iterdir()]
        self.assertIn("current_profile.build-9", names,
                      "the state file did not follow --hostname: %s" % (names,))

    def test_an_invalid_hostname_is_refused(self):
        result = self.run_xtm("--hostname", "not a hostname", "-l")
        self.assertNotEqual(0, result.returncode)
        self.assertNotIn("Traceback", result.stderr)
