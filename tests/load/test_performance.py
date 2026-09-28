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

`tests/run-tests.sh --perf` runs these. They are deliberately outside both
the fast and the long halves: a timing test inside a pass/fail suite that
mixes it with unit tests is a flaky test waiting to happen.
"""

import json
import os
import sys
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


def under_instrumentation():
    """Whether something is measuring this process line by line.

    A timing gate is meaningless under coverage: tracing every line makes
    everything several times slower, so the gate fails on the measurement
    rather than on the code. The suite runs under coverage as a whole, so
    these tests have to notice and stand down.
    """
    return bool(os.environ.get("COVERAGE_PROCESS_START")
                or "coverage" in sys.modules)


class PerformanceCase(unittest.TestCase):
    """Times an operation and compares it with this machine's baseline."""

    def setUp(self):
        if under_instrumentation():
            self.skipTest("running under coverage; a timing gate measures the "
                          "tracer, not the code. Run tests/run-tests.sh "
                          "--perf on its own.")
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
    """Reading the config runs on every single invocation."""

    def setUp(self):
        PerformanceCase.setUp(self)
        self.text = big_config()

    def test_reading_a_thousand_slots(self):
        self.time("read_1000_slots", lambda: xtm.load_yaml(self.text))

    def test_writing_a_thousand_slots(self):
        data = xtm.load_yaml(self.text)
        self.time("write_1000_slots", lambda: xtm.dump_yaml(data))


class TestMemoryCeiling(unittest.TestCase):
    """Reading a large config must not cost memory out of proportion to it.

    A ceiling rather than a baseline: this one is about shape, not speed, and
    an absolute number is easier to reason about than a ratio.
    """

    def test_reading_a_large_config_stays_proportionate(self):
        if under_instrumentation():
            self.skipTest("running under coverage; the tracer's own "
                          "allocations swamp the measurement")
        def peak_for(text):
            tracemalloc.start()
            try:
                xtm.load_yaml(text)
                return tracemalloc.get_traced_memory()[1]
            finally:
                tracemalloc.stop()

        small, large = big_config(profiles=5), big_config(profiles=20)
        small_peak, large_peak = peak_for(small), peak_for(large)
        # PyYAML composes the whole node graph before building the data, so
        # the peak is some tens of times the text -- about 75 here, 4 MB for
        # a thousand slots. The ceiling is on that ratio with room to spare,
        # and the growth check is what catches a quadratic mistake: four
        # times the config must cost about four times the memory, not
        # sixteen.
        self.assertLess(large_peak, len(large) * 150,
                        "reading a %d-byte config peaked at %d bytes"
                        % (len(large), large_peak))
        growth = large_peak / float(small_peak)
        size_growth = len(large) / float(len(small))
        self.assertLess(growth, size_growth * 1.5,
                        "memory grew %.1fx for %.1fx the config"
                        % (growth, size_growth))


if __name__ == "__main__":
    unittest.main()
