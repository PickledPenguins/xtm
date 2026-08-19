"""Config and state files: paths, resolution, durable writes, locking."""

import fcntl
import os
import shutil
import tempfile
import unittest
from pathlib import Path

from tests.support import (
    PathIsolationMixin,
    xtm,
)


class TestPathResolution(PathIsolationMixin):

    def test_explicit_config_path_wins(self):
        self.assertEqual(xtm.CONFIG_FILE, self.tmp / "config.yaml")

    def test_state_file_is_per_host(self):
        self.assertTrue(xtm.STATE_FILE.name.startswith("current_profile."))
        self.assertTrue(xtm.STATE_FILE.name.endswith(xtm.short_hostname()))

    def test_env_vars_used_when_no_flags(self):
        os.environ["XTM_CONFIG_DIR"] = str(self.tmp / "envcfg")
        os.environ["XTM_STATE_DIR"] = str(self.tmp / "envstate")
        try:
            xtm.resolve_paths(None, None)
            self.assertEqual(xtm.CONFIG_FILE, self.tmp / "envcfg" / "config.yaml")
            self.assertEqual(xtm.STATE_FILE.parent, self.tmp / "envstate")
        finally:
            del os.environ["XTM_CONFIG_DIR"]
            del os.environ["XTM_STATE_DIR"]

    def test_short_hostname_is_filename_safe(self):
        self.assertRegex(xtm.short_hostname(), r"^[A-Za-z0-9_.\-]+$")


class TestStateFile(PathIsolationMixin):

    def test_round_trip(self):
        xtm.write_current_profile_state("work")
        self.assertEqual(xtm.read_current_profile_state(), "work")

    def test_absent_state_returns_none(self):
        self.assertIsNone(xtm.read_current_profile_state())

    def test_only_first_line_is_used(self):
        xtm.STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        xtm.STATE_FILE.write_text("work\ngarbage\n")
        self.assertEqual(xtm.read_current_profile_state(), "work")

    def test_blank_state_returns_none(self):
        xtm.STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        xtm.STATE_FILE.write_text("\n\n")
        self.assertIsNone(xtm.read_current_profile_state())

    def test_legacy_machine_independent_file_is_honoured(self):
        xtm.STATE_DIR.mkdir(parents=True, exist_ok=True)
        (xtm.STATE_DIR / "current_profile").write_text("legacy\n")
        self.assertEqual(xtm.read_current_profile_state(), "legacy")

    def test_clear_state(self):
        xtm.write_current_profile_state("work")
        xtm.clear_current_profile_state()
        self.assertIsNone(xtm.read_current_profile_state())


class TestProfileResolution(PathIsolationMixin):

    CONFIG = {"profiles": {"default": {"sessions": {}},
                           "desk": {"sessions": {},
                                    "match": {"hostname": "*"}},
                           "other": {"sessions": {}}}}

    def test_explicit_profile_is_persisted(self):
        name = xtm.resolve_profile_name(dict(self.CONFIG), "other")
        self.assertEqual(name, "other")
        self.assertEqual(xtm.read_current_profile_state(), "other")

    def test_unknown_explicit_profile_raises(self):
        with self.assertRaises(xtm.XtmError):
            xtm.resolve_profile_name(dict(self.CONFIG), "nope")

    def test_state_file_wins_over_match(self):
        xtm.write_current_profile_state("other")
        self.assertEqual(xtm.resolve_profile_name(dict(self.CONFIG), None), "other")

    def test_stale_state_file_raises(self):
        xtm.write_current_profile_state("deleted")
        with self.assertRaises(xtm.XtmError):
            xtm.resolve_profile_name(dict(self.CONFIG), None)

    def test_auto_ignores_state_and_matches(self):
        xtm.write_current_profile_state("other")
        self.assertEqual(
            xtm.resolve_profile_name(dict(self.CONFIG), None, auto=True), "desk")

    def test_falls_back_to_default(self):
        config = {"profiles": {"default": {"sessions": {}}}}
        self.assertEqual(xtm.resolve_profile_name(config, None), "default")

    def test_no_default_and_no_match_raises(self):
        config = {"profiles": {"only": {"sessions": {}}}}
        with self.assertRaises(xtm.XtmError):
            xtm.resolve_profile_name(config, None)

    def test_match_on_a_hostname_list(self):
        # One profile can serve several machines, so any pattern in the
        # list matching is enough.
        config = {"profiles": {"shared": {
            "sessions": {},
            "match": {"hostname": ["nowhere", xtm.short_hostname()]}}}}
        self.assertEqual(xtm.match_profile(config), "shared")

    def test_hostname_list_that_matches_nothing(self):
        config = {"profiles": {"shared": {
            "sessions": {},
            "match": {"hostname": ["nowhere", "elsewhere"]}}}}
        self.assertIsNone(xtm.match_profile(config))

    def test_hostname_that_does_not_fit_never_matches(self):
        config = {"profiles": {"p": {"match": {"hostname": "nosuchhost-xyz"}}}}
        self.assertIsNone(xtm.match_profile(config))

    def test_a_malformed_match_block_leaves_the_profile_visible(self):
        # Otherwise a typo would hide the profile behind "belongs to
        # another machine" instead of surfacing the real validation error.
        for criteria in ({"platform": "linux"},
                         {"hostname": ""},
                         {"hostname": []},
                         {"hostname": ["ok", 5]}):
            profile = {"match": criteria, "sessions": {}}
            self.assertTrue(xtm.profile_is_visible(profile, "anyhost"),
                            "%r should stay visible" % (criteria,))

    def test_a_well_formed_non_matching_block_hides_the_profile(self):
        profile = {"match": {"hostname": ["a*", "b*"]}, "sessions": {}}
        self.assertFalse(xtm.profile_is_visible(profile, "zebra"))
        self.assertTrue(xtm.profile_is_visible(profile, "apple"))

    def test_a_global_profile_is_visible_everywhere(self):
        self.assertTrue(xtm.profile_is_visible({"sessions": {}}, "anyhost"))

    def test_profile_without_match_never_matches(self):
        self.assertIsNone(xtm.match_profile({"profiles": {"p": {"sessions": {}}}}))


class TestConfigIO(PathIsolationMixin):

    def test_creates_default_config(self):
        config = xtm.load_config()
        self.assertIn("default", config["profiles"])
        self.assertTrue(xtm.CONFIG_FILE.exists())

    def test_round_trip_through_disk(self):
        config = xtm.load_config()
        config["profiles"]["default"]["sessions"]["a"] = {
            "x": 1, "y": 2, "width": 3, "height": 4, "command": "htop"}
        xtm.save_config(config)
        reloaded = xtm.load_config()
        self.assertEqual(reloaded["profiles"]["default"]["sessions"]["a"]["command"],
                         "htop")

    def test_missing_profiles_key_raises(self):
        xtm.CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
        xtm.CONFIG_FILE.write_text("something: else\n")
        with self.assertRaises(xtm.XtmError):
            xtm.load_config()

    def test_non_mapping_profiles_raises(self):
        xtm.CONFIG_FILE.write_text("profiles: 5\n")
        with self.assertRaises(xtm.XtmError):
            xtm.load_config()

    def test_invalid_profile_name_on_load_raises(self):
        xtm.CONFIG_FILE.write_text('profiles:\n  "bad name":\n    sessions: {}\n')
        with self.assertRaises(xtm.XtmError):
            xtm.load_config()

    def test_numeric_names_normalised_to_strings(self):
        xtm.CONFIG_FILE.write_text(
            "profiles:\n  123:\n    sessions:\n      456:\n"
            "        x: 0\n        y: 0\n        width: 8\n        height: 6\n")
        config = xtm.load_config()
        self.assertIn("123", config["profiles"])
        self.assertIn("456", config["profiles"]["123"]["sessions"])

    def test_create_if_missing_false_raises(self):
        with self.assertRaises(xtm.XtmError):
            xtm.load_config(create_if_missing=False)


class TestAtomicWrite(PathIsolationMixin):

    def test_writes_content(self):
        target = self.tmp / "sub" / "file.txt"
        xtm._write_text_safe(target, "hello\n", "test file")
        self.assertEqual(target.read_text(), "hello\n")

    def test_leaves_no_temp_files(self):
        target = self.tmp / "file.txt"
        xtm._write_text_safe(target, "hello\n", "test file")
        leftovers = [p.name for p in self.tmp.iterdir() if p.name.endswith(".tmp")]
        self.assertEqual(leftovers, [])

    def test_replaces_existing_content_entirely(self):
        target = self.tmp / "file.txt"
        xtm._write_text_safe(target, "a much longer original\n", "test file")
        xtm._write_text_safe(target, "short\n", "test file")
        self.assertEqual(target.read_text(), "short\n")

    def test_dry_run_does_not_write(self):
        target = self.tmp / "file.txt"
        xtm.DRY_RUN = True
        try:
            xtm._write_text_safe(target, "hello\n", "test file")
        finally:
            xtm.DRY_RUN = False
        self.assertFalse(target.exists())

    def test_unwritable_location_raises_cleanly(self):
        with self.assertRaises(xtm.XtmError):
            xtm._write_text_safe(Path("/proc/xtm-nope/file"), "x", "test file")

    def test_read_missing_file_raises_cleanly(self):
        with self.assertRaises(xtm.XtmError):
            xtm._read_text_safe(self.tmp / "absent", "test file")

    def test_config_lock_is_reentrant_across_uses(self):
        with xtm.config_lock():
            pass
        with xtm.config_lock():
            pass


class TestConfigLocking(unittest.TestCase):
    """The advisory lock that prevents concurrent writes losing updates."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="xtm-lock-"))
        self.addCleanup(shutil.rmtree, str(self.tmp), True)
        self.config = self.tmp / "config.yaml"
        self.config.write_text("profiles: {}\n")

    def test_lock_file_is_a_sibling_not_the_config_itself(self):
        # Locking the config directly would not survive the atomic
        # rename that replaces its inode on every save.
        with xtm.config_lock(self.config) as lock:
            self.assertNotEqual(lock.path, self.config)
            self.assertTrue(lock.path.exists())
            self.assertEqual(lock.path.parent, self.config.parent)

    def test_lock_is_exclusive_between_processes(self):
        lock_path = self.config.with_suffix(self.config.suffix + ".lock")
        holder = open(str(lock_path), "w")
        self.addCleanup(holder.close)
        fcntl.flock(holder.fileno(), fcntl.LOCK_EX)
        # A second exclusive, non-blocking attempt must be refused while
        # the first is held; that refusal is what serialises writers.
        contender = open(str(lock_path), "w")
        self.addCleanup(contender.close)
        with self.assertRaises(IOError):
            fcntl.flock(contender.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        fcntl.flock(holder.fileno(), fcntl.LOCK_UN)
        fcntl.flock(contender.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)

    def test_lock_is_released_on_exit(self):
        with xtm.config_lock(self.config) as lock:
            path = lock.path
        after = open(str(path), "w")
        self.addCleanup(after.close)
        fcntl.flock(after.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)

    def test_lock_is_released_when_the_body_raises(self):
        path = None
        with self.assertRaises(ValueError):
            with xtm.config_lock(self.config) as lock:
                path = lock.path
                raise ValueError("boom")
        after = open(str(path), "w")
        self.addCleanup(after.close)
        fcntl.flock(after.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)

    def test_unwritable_lock_directory_does_not_stop_the_run(self):
        # Losing the lock is better than losing the command: an unlocked
        # write still succeeds, it is only unprotected against a
        # simultaneous second writer.
        blocker = self.tmp / "blocker"
        blocker.write_text("not a directory\n")
        unusable = blocker / "config.yaml"   # parent is a file, so mkdir fails
        with xtm.config_lock(unusable) as lock:
            self.assertIsNone(lock._handle)
