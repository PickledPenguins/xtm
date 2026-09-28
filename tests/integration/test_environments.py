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

    def test_an_editor_with_arguments_is_honored(self):
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
            universal_newlines=True, env=env,
            # A suite started in the background inherits SIGINT as ignored,
            # and Python then installs no KeyboardInterrupt handler at all:
            # the signal below would be dropped and the test would time out
            # for a reason that has nothing to do with xtm.
            preexec_fn=lambda: signal.signal(signal.SIGINT, signal.SIG_DFL))
        # Signal once xtm is really waiting for the attach: the fake xterm
        # logs its launch just before that wait begins. A fixed one-second
        # sleep raced the interpreter's own start-up on a loaded machine and
        # signalled a process that had not installed its handlers yet.
        launched = self.state / "launched.log"
        deadline = time.time() + 20
        while not launched.exists() and time.time() < deadline:
            time.sleep(0.05)
        time.sleep(0.2)
        proc.send_signal(signal.SIGINT)
        _out, err = proc.communicate(timeout=30)
        self.assertEqual(proc.returncode, xtm.EXIT_INTERRUPTED, err)
        self.assertIn("Interrupted", err)


class TestWithoutPyYaml(IntegrationBase):
    """xtm needs PyYAML to read its config, and says so when it is missing.

    PyYAML is hidden from the child process by a module ahead of it on the
    import path that raises ImportError.
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
        # Guards the test itself: a broken blocker would make every other
        # test here pass for the wrong reason.
        result = subprocess.run(
            [sys.executable, "-c", "import yaml"],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            universal_newlines=True, env=self.env, timeout=40)
        self.assertNotEqual(result.returncode, 0)

    def test_reading_the_config_says_how_to_install_pyyaml(self):
        result = self.run_xtm("--list")
        self.assertEqual(result.returncode, 1)
        self.assertIn("xtm reads its config with PyYAML", result.stderr)
        self.assertIn("pip install --user PyYAML", result.stderr)
        self.assertNotIn("Traceback", result.stderr)

    def test_help_and_version_need_no_pyyaml(self):
        for flag in ("--help", "--version"):
            result = self.run_xtm(flag)
            self.assertEqual(result.returncode, 0, result.stderr)


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
