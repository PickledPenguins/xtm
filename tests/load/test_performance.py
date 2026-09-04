"""Timing and memory gates, against a baseline recorded on this machine.

A cost budget in subprocess calls -- which is what the rest of tests/load/
asserts -- does not notice a change that doubles the work inside one call.
These do.

Two rules make the numbers mean something. The baseline is per machine and
gitignored, because a threshold calibrated on someone else's laptop is a
coin toss; and the threshold comes from measured noise rather than from
taste. Repeated runs of an unchanged tree here move a median by up to about
35%, so the gate sits at +100%: a real regression in this code is a factor,
not a fraction, and a tighter gate would fail on nothing but weather.

`scripts/run_tests.sh --perf` runs these. They are deliberately outside both
the fast and the long halves: a timing test inside a pass/fail suite that
mixes it with unit tests is a flaky test waiting to happen.
"""

import json
import time
import tracemalloc
import unittest

from tests.support import REPO_ROOT, xtm

BASELINE = REPO_ROOT / ".build" / "perf-baseline.json"

# The multiple of the recorded median at which a case fails. Derived from
# measured run-to-run spread on this machine, not chosen for tidiness.
BUDGET = 2.0

# Repeats per case: enough that one scheduling hiccup does not set the
# median, few enough that the whole file stays under ten seconds.
REPEATS = 5


def median(values):
    ordered = sorted(values)
    return ordered[len(ordered) // 2]


class PerformanceCase(unittest.TestCase):
    """Times an operation and compares it with this machine's baseline."""

    def setUp(self):
        self.baseline = {}
        if BASELINE.exists():
            self.baseline = json.loads(BASELINE.read_text())

    def record(self, name, seconds):
        """Store a new median, and fail if it has grown past the budget."""
        previous = self.baseline.get(name)
        BASELINE.parent.mkdir(parents=True, exist_ok=True)
        current = dict(self.baseline)
        # Keep the smaller of the two: a baseline that drifts upward with
        # every slow run stops being a gate after a few bad afternoons.
        current[name] = min(seconds, previous) if previous else seconds
        BASELINE.write_text(json.dumps(current, indent=2, sort_keys=True))
        if previous is None:
            self.skipTest("no baseline for %r yet; recorded %.4fs. Run again "
                          "to gate against it." % (name, seconds))
        self.assertLess(
            seconds, previous * BUDGET,
            "%s took %.4fs against a baseline of %.4fs (budget %.0f%%). "
            "Either the change made it slower, or the baseline in %s is stale "
            "-- delete that entry to re-record."
            % (name, seconds, previous, BUDGET * 100, BASELINE))

    def time(self, name, work):
        samples = []
        for _ in range(REPEATS):
            start = time.perf_counter()
            work()
            samples.append(time.perf_counter() - start)
        self.record(name, median(samples))


def big_config(profiles=20, slots=50):
    """A config far larger than a person would write, in the shape they write."""
    lines = ["profiles:"]
    for p in range(profiles):
        lines.append("  profile%d:" % (p,))
        lines.append("    prefix: p%d-" % (p,))
        lines.append("    sessions:")
        for s in range(slots):
            lines.append("      slot%d: {x: %d, y: %d, width: 900, height: 700}"
                         % (s, s * 10, s * 5))
    return "\n".join(lines) + "\n"


class TestParsingCost(PerformanceCase):
    """The guard and the reader run on every single invocation."""

    def setUp(self):
        PerformanceCase.setUp(self)
        self.text = big_config()
        self.saved = xtm.HAVE_PYYAML
        self.addCleanup(setattr, xtm, "HAVE_PYYAML", self.saved)

    def test_the_builtin_reader_on_a_thousand_slots(self):
        xtm.HAVE_PYYAML = False
        self.time("builtin_read_1000_slots", lambda: xtm.load_yaml(self.text))

    def test_the_parity_guard_on_a_thousand_slots(self):
        # Separately from the reader: the guard runs for both backends, so a
        # regression here is paid by every user, not only the ones without
        # PyYAML.
        self.time("guard_scan_1000_slots",
                  lambda: xtm._scan_for_ambiguous_scalars(self.text))

    def test_writing_a_thousand_slots(self):
        xtm.HAVE_PYYAML = False
        data = xtm.load_yaml(self.text)
        self.time("builtin_write_1000_slots", lambda: xtm.dump_yaml(data))


class TestMemoryCeiling(unittest.TestCase):
    """Reading a large config must not cost memory out of proportion to it.

    A ceiling rather than a baseline: this one is about shape, not speed, and
    an absolute number is easier to reason about than a ratio.
    """

    def test_reading_a_large_config_stays_proportionate(self):
        text = big_config()
        saved = xtm.HAVE_PYYAML
        xtm.HAVE_PYYAML = False
        self.addCleanup(setattr, xtm, "HAVE_PYYAML", saved)
        tracemalloc.start()
        try:
            xtm.load_yaml(text)
            _current, peak = tracemalloc.get_traced_memory()
        finally:
            tracemalloc.stop()
        # The text is about 70 KiB; twenty times that is generous for a
        # dictionary of a thousand small mappings and still catches the kind
        # of mistake that builds a list per row.
        ceiling = len(text) * 20
        self.assertLess(
            peak, ceiling,
            "reading a %d-byte config peaked at %d bytes, over the %d-byte "
            "ceiling" % (len(text), peak, ceiling))


if __name__ == "__main__":
    unittest.main()
