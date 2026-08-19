"""Argument parsing, logging setup and subprocess helpers."""

import logging
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

from tests.support import (
    xtm,
)


class TestParser(unittest.TestCase):

    def parse(self, *argv):
        return xtm.build_parser().parse_args(list(argv))

    def test_short_and_long_forms_agree(self):
        for short, long_form, dest in (("-r", "--reset", "reset"),
                                       ("-R", "--reset-all", "reset_all"),
                                       ("-l", "--list", "list"),
                                       ("-K", "--close-all", "close_all"),
                                       ("-u", "--update-profile", "update_profile"),
                                       ("-V", "--validate", "validate"),
                                       ("-e", "--edit", "edit"),
                                       ("-P", "--list-profiles", "list_profiles"),
                                       ("-C", "--current-profile",
                                        "current_profile")):
            self.assertTrue(getattr(self.parse(short), dest), short)
            self.assertTrue(getattr(self.parse(long_form), dest), long_form)

    def test_value_options(self):
        self.assertEqual(self.parse("-o", "work1").open, "work1")
        self.assertEqual(self.parse("--open", "work1").open, "work1")
        self.assertEqual(self.parse("-k", "logs").close, "logs")
        self.assertEqual(self.parse("-f", "logs").focus, "logs")
        self.assertEqual(self.parse("-n", "new").new_profile, "new")
        self.assertEqual(self.parse("-D", "old").delete_session, "old")
        self.assertEqual(self.parse("-s", "a", "0,0,1,1").set, ["a", "0,0,1,1"])
        self.assertEqual(self.parse("--copy-profile", "a", "b").copy_profile,
                         ["a", "b"])
        self.assertEqual(self.parse("--rename-profile", "a", "b").rename_profile,
                         ["a", "b"])

    def test_legacy_single_dash_aliases(self):
        self.assertTrue(self.parse("-reset").reset)
        self.assertTrue(self.parse("-resetall").reset_all)
        self.assertTrue(self.parse("-listprofiles").list_profiles)
        self.assertTrue(self.parse("-currentprofile").current_profile)
        self.assertTrue(self.parse("-updateprofile").update_profile)
        self.assertEqual(self.parse("-open", "w").open, "w")
        self.assertEqual(self.parse("-newprofile", "n").new_profile, "n")
        self.assertEqual(self.parse("-profile", "p").profile, "p")
        self.assertTrue(self.parse("-debug").debug)

    def test_general_options(self):
        args = self.parse("-p", "work", "-j", "-N", "-y", "--verify",
                          "--no-frame-compensation", "-a")
        self.assertEqual(args.profile, "work")
        self.assertTrue(args.json and args.dry_run and args.yes)
        self.assertTrue(args.verify and args.no_frame_compensation and args.auto)

    def test_actions_are_mutually_exclusive(self):
        with self.assertRaises(SystemExit):
            self.parse("-r", "-R")

    def test_bad_log_level_rejected(self):
        with self.assertRaises(SystemExit):
            self.parse("--log-level", "chatty")

    def test_no_action_defaults_to_list(self):
        args = self.parse()
        dest, _handler, mutates, needs_profile = xtm.selected_action(args)
        self.assertEqual(dest, "list")
        self.assertFalse(mutates)
        self.assertTrue(needs_profile)
        self.assertTrue(args.list)

    def test_selected_action_reports_mutation(self):
        args = self.parse("-s", "a", "0,0,1,1")
        dest, _handler, mutates, _needs = xtm.selected_action(args)
        self.assertEqual(dest, "set")
        self.assertTrue(mutates)

    def test_every_command_dest_exists_on_namespace(self):
        args = self.parse()
        for dest, _handler, _mutates, _needs in xtm.COMMANDS:
            self.assertTrue(hasattr(args, dest), dest)


class TestLoggingSetup(unittest.TestCase):

    def tearDown(self):
        xtm.setup_logging(logging.WARNING, None)

    def test_console_defaults_to_info(self):
        args = xtm.build_parser().parse_args([])
        self.assertEqual(xtm.console_level_from_args(args), logging.INFO)

    def test_quiet_and_debug(self):
        parser = xtm.build_parser()
        self.assertEqual(
            xtm.console_level_from_args(parser.parse_args(["-q"])), logging.WARNING)
        self.assertEqual(
            xtm.console_level_from_args(parser.parse_args(["-d"])), logging.DEBUG)

    def test_log_level_overrides_debug(self):
        args = xtm.build_parser().parse_args(["-d", "--log-level", "error"])
        self.assertEqual(xtm.console_level_from_args(args), logging.ERROR)

    def test_file_handler_added_and_written(self):
        tmp = Path(tempfile.mkdtemp(prefix="xtm-log-"))
        self.addCleanup(shutil.rmtree, str(tmp), True)
        log_path = tmp / "deep" / "xtm.log"
        xtm.setup_logging(logging.WARNING, str(log_path))
        xtm.logger.debug("a debug line that only the file should capture")
        for handler in xtm.logger.handlers:
            handler.flush()
        self.assertIn("only the file should capture", log_path.read_text())

    def test_handlers_are_not_duplicated(self):
        xtm.setup_logging(logging.INFO, None)
        first = len(xtm.logger.handlers)
        xtm.setup_logging(logging.INFO, None)
        self.assertEqual(len(xtm.logger.handlers), first)

    def test_unwritable_log_file_warns_but_continues(self):
        xtm.setup_logging(logging.INFO, "/proc/xtm-nope/x.log")
        self.assertTrue(xtm.logger.handlers)

    def test_console_formatter_labels_only_problems(self):
        formatter = xtm._ConsoleFormatter()
        info = logging.LogRecord("xtm", logging.INFO, "f", 1, "hello", None, None)
        warn = logging.LogRecord("xtm", logging.WARNING, "f", 1, "uh oh", None, None)
        self.assertEqual(formatter.format(info), "hello")
        self.assertEqual(formatter.format(warn), "xtm: warning: uh oh")


class TestSubprocessHelpers(unittest.TestCase):

    def test_missing_required_program_raises(self):
        with self.assertRaises(xtm.XtmError):
            xtm.run(["definitely-not-a-real-program-xyz"])

    def test_missing_optional_program_returns_none(self):
        self.assertIsNone(
            xtm.run(["definitely-not-a-real-program-xyz"], required=False))

    def test_timeout_raises(self):
        with self.assertRaises(xtm.XtmError):
            xtm.run([sys.executable, "-c", "import time; time.sleep(5)"],
                    timeout=0.2)

    def test_output_is_text(self):
        result = xtm.run([sys.executable, "-c", "print('hi')"])
        self.assertEqual(result.stdout.strip(), "hi")

    def test_forced_c_locale(self):
        result = xtm.run([sys.executable, "-c",
                          "import os; print(os.environ['LC_ALL'])"])
        self.assertEqual(result.stdout.strip(), "C")

    def test_truncate_helper(self):
        self.assertEqual(xtm._truncate(None), "")
        self.assertIn("more chars", xtm._truncate("x" * 400))
        self.assertEqual(xtm._truncate("  hi  "), "hi")

    def test_deep_copy_is_independent(self):
        original = {"a": {"b": [1, 2]}}
        copy = xtm._deep_copy(original)
        copy["a"]["b"].append(3)
        self.assertEqual(original["a"]["b"], [1, 2])


class TestBuildXtermCommand(unittest.TestCase):

    def test_minimal_command(self):
        argv = xtm.build_xterm_command("work1", None)
        self.assertEqual(argv[0], "xterm")
        self.assertIn("-xrm", argv)
        self.assertIn("XTerm*allowWindowOps: true", argv)
        self.assertEqual(argv[argv.index("-name") + 1], "xtm-work1")
        self.assertEqual(argv[argv.index("-T") + 1], "xtm:work1")
        self.assertEqual(argv[argv.index("-e") + 1:],
                         ["tmux", "new-session", "-A", "-s", "work1"])

    def test_command_and_cwd_are_appended(self):
        argv = xtm.build_xterm_command(
            "logs", {"command": "tail -f /var/log/messages", "cwd": "/var/log"})
        tail = argv[argv.index("-e") + 1:]
        self.assertEqual(tail[:5], ["tmux", "new-session", "-A", "-s", "logs"])
        self.assertEqual(tail[5:7], ["-c", "/var/log"])
        self.assertEqual(tail[7], "tail -f /var/log/messages")

    def test_cwd_is_expanded(self):
        argv = xtm.build_xterm_command("s", {"cwd": "~/src"})
        self.assertEqual(argv[argv.index("-c") + 1],
                         os.path.expanduser("~/src"))

    def test_xterm_args_precede_dash_e(self):
        argv = xtm.build_xterm_command("s", {"xterm_args": ["-bg", "#300"]})
        self.assertLess(argv.index("-bg"), argv.index("-e"))
        self.assertEqual(argv[argv.index("-bg") + 1], "#300")


class TestNonExecutableProgram(unittest.TestCase):

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="xtm-noexec-"))
        self.addCleanup(shutil.rmtree, str(self.tmp), True)
        self.script = self.tmp / "brokenprog"
        self.script.write_text("#!/bin/sh\ntrue\n")
        self.script.chmod(0o644)  # present but not executable

    def test_required_raises(self):
        with self.assertRaises(xtm.XtmError):
            xtm.run([str(self.script)])

    def test_optional_returns_none(self):
        self.assertIsNone(xtm.run([str(self.script)], required=False))
