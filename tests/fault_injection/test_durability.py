"""Failure paths a healthy filesystem never reaches."""

import builtins
import os
import tempfile
from pathlib import Path

from tests.support import (
    PathIsolationMixin,
    xtm,
)


class TestFaultInjection(PathIsolationMixin):
    """Force the failure paths that a healthy filesystem never reaches.

    Durability code is mostly error handling, and error handling that has
    never executed is a guess. These tests break os.replace, os.fsync,
    tempfile.mkstemp, flock and the optional imports on purpose, so the
    recovery behavior is observed rather than assumed.
    """

    def _patch(self, module, name, replacement):
        original = getattr(module, name)
        setattr(module, name, replacement)
        self.addCleanup(setattr, module, name, original)

    @staticmethod
    def _raiser(exc):
        def fail(*_args, **_kwargs):
            raise exc
        return fail

    # -- atomic write -----------------------------------------------------

    def test_rename_failure_reports_cleanly_and_removes_the_temp_file(self):
        target = self.tmp / "file.txt"
        xtm._write_text_safe(target, "original\n", "test file")
        self._patch(os, "replace", self._raiser(OSError("disk went away")))
        with self.assertRaises(xtm.XtmError) as caught:
            xtm._write_text_safe(target, "replacement\n", "test file")
        self.assertIn("disk went away", str(caught.exception))
        # The original must survive: a failed write is not a lost file.
        self.assertEqual(target.read_text(), "original\n")
        self.assertEqual([p.name for p in self.tmp.iterdir()
                          if p.name.endswith(".tmp")], [],
                         "a failed write left its temp file behind")

    def test_temp_file_cleanup_failure_does_not_mask_the_real_error(self):
        # Both the write and its cleanup fail. The error the user sees must
        # be the one that actually matters, not the tidy-up failure.
        target = self.tmp / "file.txt"
        self._patch(os, "replace", self._raiser(OSError("the real problem")))
        self._patch(Path, "unlink", self._raiser(OSError("cleanup also failed")))
        with self.assertRaises(xtm.XtmError) as caught:
            xtm._write_text_safe(target, "content\n", "test file")
        self.assertIn("the real problem", str(caught.exception))
        self.assertNotIn("cleanup also failed", str(caught.exception))

    def test_mkstemp_failure_is_reported_as_a_user_error(self):
        self._patch(tempfile, "mkstemp", self._raiser(OSError("no space left")))
        with self.assertRaises(xtm.XtmError) as caught:
            xtm._write_text_safe(self.tmp / "f.txt", "x", "test file")
        self.assertIn("no space left", str(caught.exception))

    def test_file_fsync_failure_aborts_the_write_and_keeps_the_original(self):
        # Deliberately NOT best-effort. fsync on the data file is what makes
        # the write survive a crash, so if it cannot be done the swap must
        # not happen: a config that is present but empty is worse than one
        # that was never replaced. Contrast _fsync_directory below, where
        # failure IS swallowed because the data is already safe by then.
        target = self.tmp / "f.txt"
        xtm._write_text_safe(target, "original\n", "test file")
        self._patch(os, "fsync", self._raiser(OSError("fsync unsupported")))
        with self.assertRaises(xtm.XtmError) as caught:
            xtm._write_text_safe(target, "replacement\n", "test file")
        self.assertIn("fsync unsupported", str(caught.exception))
        self.assertEqual(target.read_text(), "original\n")
        self.assertEqual([p.name for p in self.tmp.iterdir()
                          if p.name.endswith(".tmp")], [])

    def test_directory_fsync_failure_is_swallowed(self):
        for broken in ("open", "fsync"):
            self._patch(os, broken, self._raiser(OSError("nope")))
        xtm._fsync_directory(self.tmp)   # must not raise

    # -- config lock ------------------------------------------------------

    def test_lock_acquisition_failure_degrades_to_no_locking(self):
        # An unlocked run is still far better than a failed one.
        self._patch(builtins, "open", self._raiser(OSError("permission denied")))
        with xtm.config_lock(self.tmp / "config.yaml") as lock:
            self.assertIsNone(lock._handle)

    def test_lock_release_survives_a_failing_close(self):
        lock = xtm.config_lock(self.tmp / "config.yaml")

        class Handle(object):
            @staticmethod
            def close():
                raise OSError("close failed")

        lock._handle = Handle()
        lock._close()          # must not raise
        self.assertIsNone(lock._handle)

    def test_missing_fcntl_proceeds_without_a_lock(self):
        # Non-POSIX platforms have no fcntl; xtm must still run.
        real_import = builtins.__import__

        def no_fcntl(name, *args, **kwargs):
            if name == "fcntl":
                raise ImportError("no fcntl on this platform")
            return real_import(name, *args, **kwargs)

        self._patch(builtins, "__import__", no_fcntl)
        with xtm.config_lock(self.tmp / "config.yaml") as lock:
            self.assertIsNone(lock._handle)

    # -- read path --------------------------------------------------------

    def test_unreadable_config_reports_cleanly(self):
        target = self.tmp / "config.yaml"
        target.write_text("profiles: {}\n")
        self._patch(Path, "read_text", self._raiser(OSError("permission denied")))
        with self.assertRaises(xtm.XtmError) as caught:
            xtm._read_text_safe(target, "config file")
        self.assertIn("permission denied", str(caught.exception))
