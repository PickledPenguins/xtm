"""--dry-run must have no effect on anything."""

import copy
import os
import pty
import shutil
import tempfile
import time
import unittest
from pathlib import Path

from tests.support import (
    IntegrationBase,
    xtm,
)


class TestDryRun(IntegrationBase):

    # The config and state trees, which are everything --dry-run promises not
    # to touch. Both are per-test temporary directories.
    def _managed_roots(self):
        return (self.tmp / "cfg", self.tmp / "state")

    def test_dry_run_leaves_the_filesystem_completely_untouched(self):
        """The invariant --dry-run actually sells, asserted as an invariant.

        Checking individual effects one at a time is how a leak survives:
        every specific assertion passed while `xtm -N` was still creating a
        config lock file, because no test asked the general question. This
        one sweeps the whole config and state tree instead, so any new write
        path that forgets its DRY_RUN guard fails here regardless of what it
        writes.
        """
        self.run_xtm("-l")                       # create a real config first
        self.add_session("work1")
        before = self.fs_snapshot(*self._managed_roots())
        self.assertTrue(before, "expected a config to exist before the dry run")

        dry_commands = [
            ("-N", "-s", "work1", "0,0,900,700"),
            ("-N", "-o", "work1"),
            ("-N", "-k", "work1"),
            ("-N", "-r"),
            ("-N", "-R"),
            ("-N", "-K", "-y"),
            ("-N", "-t", "work1"),
            ("-N", "-n", "captured"),
            ("-N", "-D", "work1"),
            ("-N", "--copy-profile", "default", "clone"),
            ("-N", "--rename-profile", "default", "renamed"),
            ("-N", "--delete-profile", "default", "-y"),
            ("-N", "--make-global", "default"),
            ("-N", "-u"),
            ("-N", "otherprofile"),
        ]
        for argv in dry_commands:
            self.run_xtm(*argv)
            after = self.fs_snapshot(*self._managed_roots())
            self.assertEqual(
                after, before,
                "xtm %s changed the filesystem during a dry run" % (" ".join(argv),))

    def test_dry_run_creates_nothing_from_a_clean_slate(self):
        """A dry run against a machine with no config must not create one."""
        roots = self._managed_roots()
        for root in roots:
            self.assertFalse(root.exists(), "test precondition: %s" % (root,))
        self.run_xtm("-N", "-s", "work1", "0,0,900,700")
        for root in roots:
            self.assertFalse(
                root.exists(),
                "dry run created %s; it should have written nothing" % (root,))

    def test_dry_run_set_does_not_change_config(self):
        self.run_xtm("-l")
        before = self.config_text()
        result = self.run_xtm("-N", "-s", "work1", "0,0,900,700")
        self.assertEqual(result.returncode, 0)
        self.assertEqual(self.config_text(), before)
        self.assertIn("[dry-run]", result.stderr)

    def test_dry_run_open_launches_nothing(self):
        self.run_xtm("-l")
        result = self.run_xtm("-N", "-o", "work1")
        self.assertEqual(result.returncode, 0)
        self.assertFalse((self.state / "launched.log").exists())

    def test_dry_run_close_kills_nothing(self):
        self.add_session("work1")
        self.run_xtm("-N", "-k", "work1")
        self.assertEqual([r[0] for r in self.rows("sessions.txt")], ["work1"])

    def test_dry_run_reset_moves_nothing(self):
        master_fd, slave_fd = pty.openpty()
        self.addCleanup(os.close, master_fd)
        self.addCleanup(os.close, slave_fd)
        self.run_xtm("-s", "work1", "100,200,900,700")
        self.add_session("work1", tty=os.ttyname(slave_fd))
        self.run_xtm("-N", "-r")
        os.set_blocking(master_fd, False)
        time.sleep(0.1)
        with self.assertRaises(BlockingIOError):
            os.read(master_fd, 4096)


class TestDryRunExtras(IntegrationBase):

    def test_dry_run_edit_does_not_start_an_editor(self):
        editor = self.tmp / "must-not-run"
        editor.write_text("#!/bin/sh\nexit 9\n")
        editor.chmod(0o755)
        result = self.run_xtm("-N", "-e", env={"EDITOR": str(editor)})
        self.assertEqual(result.returncode, 0)
        self.assertIn("would open", result.stderr)

    def test_dry_run_delete_profile_keeps_it(self):
        self.run_xtm("-l")
        self.run_xtm("--copy-profile", "default", "spare")
        self.run_xtm("-N", "-y", "--delete-profile", "spare")
        self.assertIn("spare", self.config_text())

    def test_verify_reports_the_delta(self):
        self.run_xtm("-s", "work1", "100,200,900,700")
        self.add_session("work1")
        self.add_window("work1", 105, 233, 900, 700)
        result = self.run_xtm("--verify", "-r")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("delta", result.stderr)


class TestDryRunDoesNotAlterTheDefaults(unittest.TestCase):
    """A dry run must not change the module's own idea of a default config.

    load_config() hands the caller a stand-in config when --dry-run means
    no file was created, and the caller goes on to inject defaults and run
    a command against it. A shallow copy shares the nested 'profiles'
    mapping with the DEFAULT_CONFIG constant, so one in-process dry run
    would permanently alter the default every later call starts from --
    which matters because xtm.py is imported as well as executed.
    """

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="xtm-defaults-"))
        self.addCleanup(shutil.rmtree, str(self.tmp), True)
        context = xtm.scoped_context()
        context.__enter__()
        self.addCleanup(context.__exit__, None, None, None)

    def test_a_dry_run_leaves_default_config_untouched(self):
        before = copy.deepcopy(xtm.DEFAULT_CONFIG)
        xtm.main(["--config", str(self.tmp / "absent.yaml"),
                  "--state-dir", str(self.tmp / "state"),
                  "--dry-run", "--quiet", "--set", "newslot", "1,2,3,4"])
        self.assertEqual(xtm.DEFAULT_CONFIG, before,
                         "a dry run mutated the DEFAULT_CONFIG constant")

    def test_the_returned_config_is_independent_of_the_constant(self):
        """Asserted on the seam itself, not only through one command."""
        with xtm.scoped_context(dry_run=True,
                                config_file=self.tmp / "absent.yaml"):
            config = xtm.load_config()
        config["profiles"]["default"]["sessions"]["x"] = {"x": 0}
        self.assertEqual(
            xtm.DEFAULT_CONFIG["profiles"]["default"]["sessions"], {},
            "load_config returned a view onto DEFAULT_CONFIG, not a copy")
