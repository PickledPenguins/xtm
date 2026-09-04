"""Real processes contending for the same files.

Everything here starts several xtm processes at once and asserts what
survives. Threads would not do: the lock is an `flock` held by a process, and
the failure being tested for -- a lost update, where two runs load the same
config and the second write discards the first one's changes -- is a
property of separate processes with separate memory.

Each test runs the race repeatedly. A race that passes once has not been
tested.
"""

import json
import subprocess
import sys
import unittest

from tests.support import IntegrationBase, REPO_ROOT

XTM_SCRIPT = REPO_ROOT / "xtm.py"

# Enough repeats that an interleaving has to be genuinely rare to escape,
# few enough that the suite stays quick.
ROUNDS = 8


class TestConcurrentWrites(IntegrationBase):
    """Simultaneous writers must not lose each other's work."""

    def start(self, *argv):
        env = dict(self.env)
        return subprocess.Popen(
            [sys.executable, str(XTM_SCRIPT)] + list(argv),
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            universal_newlines=True, env=env)

    def run_together(self, argv_list):
        """Start every command at once and wait for all of them."""
        processes = [self.start(*argv) for argv in argv_list]
        results = []
        for process in processes:
            stdout, stderr = process.communicate(timeout=60)
            results.append((process.returncode, stdout, stderr))
        return results

    def slots(self, profile=None):
        """The slots of one profile, named rather than inferred.

        Reading "the current profile" would follow whatever the last racing
        process happened to persist, which is the thing under test.
        """
        argv = (("--profile", profile) if profile else ()) + ("-l", "-j")
        payload = json.loads(self.run_xtm(*argv).stdout)
        return dict((s["name"], s["configured"]) for s in payload["sessions"])

    def test_simultaneous_set_commands_all_survive(self):
        """Eight writers, eight slots, no lost update.

        This is the failure the config lock exists for: without it each run
        loads the same starting config and the last write wins, so seven of
        the eight slots vanish.
        """
        for round_number in range(ROUNDS):
            # A fresh profile per round, so what is measured is this round's
            # eight writes rather than whatever earlier rounds left behind.
            # Deleting a profile no round ever created -- which is what this
            # line used to do -- let the rounds accumulate, and the weakest
            # form of the failure, a race dropping a slot an earlier round
            # wrote, went unmeasured.
            profile = "race%d" % (round_number,)
            self.run_xtm("--copy-profile", "default", profile)
            self.run_xtm("--profile", profile, "-s", "seed", "0,0,100,100")
            names = ["slot%d" % i for i in range(8)]
            results = self.run_together(
                [("--profile", profile, "-s", name, "%d,0,100,100" % (index * 10,))
                 for index, name in enumerate(names)])
            for code, _out, err in results:
                self.assertEqual(0, code, "a concurrent --set failed: %s" % (err,))
            written = self.slots(profile)
            missing = [n for n in names if n not in written]
            self.assertEqual(
                [], missing,
                "round %d lost %d of 8 concurrent writes: %s"
                % (round_number, len(missing), missing))
            self.assertIn("seed", written,
                          "round %d lost the slot written before the race"
                          % (round_number,))

    def test_a_reader_never_sees_a_half_written_config(self):
        """Readers racing a writer see the old config or the new one.

        Writes are atomic -- a temporary file renamed into place -- so a
        reader can never observe a truncated file. What it must never do is
        fail.
        """
        for _ in range(ROUNDS):
            results = self.run_together(
                [("-s", "written", "0,0,300,300")]
                + [("-l", "-j")] * 4)
            for code, out, err in results[1:]:
                self.assertEqual(0, code,
                                 "a concurrent --list failed: %s" % (err,))
                json.loads(out)   # raises if the read was partial

    def test_concurrent_profile_switches_leave_one_current_profile(self):
        """The state file holds one name, whoever wrote it last."""
        self.run_xtm("--copy-profile", "default", "alpha")
        self.run_xtm("--copy-profile", "default", "beta")
        for _ in range(ROUNDS):
            self.run_together([("alpha", "-l"), ("beta", "-l")])
            current = self.run_xtm("-C").stdout.strip()
            self.assertIn(current, ("alpha", "beta"),
                          "the current profile became %r after a race"
                          % (current,))

    def test_the_config_is_always_loadable_after_a_race(self):
        """Whatever interleaving happens, the file still parses.

        The validation that runs before a save is per process; this asserts
        the property that matters across processes, which is that no
        combination of them leaves a file the next run cannot read.
        """
        for _ in range(ROUNDS):
            self.run_together([
                ("-s", "a", "0,0,100,100"),
                ("-s", "b", "10,0,100,100"),
                ("--copy-profile", "default", "copy"),
                ("--delete-session", "a"),
            ])
            result = self.run_xtm("-V")
            self.assertEqual(0, result.returncode,
                             "the config was left invalid by a race: %s"
                             % (result.stderr,))


class TestLockDegradation(IntegrationBase):
    """An unusable lock must not stop the tool working."""

    def test_a_read_only_config_directory_still_reports(self):
        """Reporting commands take no lock and must survive one being
        impossible."""
        self.run_xtm("-s", "work1", "0,0,100,100")
        config_dir = self.tmp / "cfg"
        mode = config_dir.stat().st_mode
        config_dir.chmod(0o500)
        self.addCleanup(config_dir.chmod, mode)
        result = self.run_xtm("-l")
        self.assertEqual(0, result.returncode,
                         "--list failed when the config directory was "
                         "read-only: %s" % (result.stderr,))


if __name__ == "__main__":
    unittest.main()
