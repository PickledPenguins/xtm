#!/usr/bin/env python3
"""Write what the last coverage run measured, for the docs to be checked against.

docs/testing.md quotes statement, branch and test counts. Those are the one
part of the documentation whose entire value is being current, and they had
drifted three times before this existed -- each time silently, because no
test reads prose.

This records the real numbers under .build/, and
tests/test_documentation.py compares the table against them. The file is
gitignored: it describes this machine's last run, and a committed copy would
be one more thing to go stale.
"""

import json
import re
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
METRICS = REPO_ROOT / ".build" / "metrics.json"


def coverage_numbers():
    """Statements, misses, branches, partials and the combined figure."""
    result = subprocess.run([sys.executable, "-m", "coverage", "report"],
                            cwd=str(REPO_ROOT), stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE, universal_newlines=True,
                            timeout=120)
    for line in result.stdout.split("\n"):
        if line.startswith("TOTAL"):
            parts = line.split()
            return {"statements": int(parts[1]), "missing": int(parts[2]),
                    "branches": int(parts[3]), "partial": int(parts[4]),
                    "combined": parts[5]}
    raise SystemExit("no TOTAL line in the coverage report:\n%s" % (result.stdout,))


def test_count():
    """How many tests the run that just finished held.

    Read from the coverage run's own output rather than by running the
    suite again: counting is not worth eight minutes.
    """
    log = REPO_ROOT / ".build" / "coverage-run.log"
    if not log.exists():
        raise SystemExit("no .build/coverage-run.log; run the coverage step first")
    found = re.findall(r"^Ran (\d+) tests", log.read_text(), re.M)
    if not found:
        raise SystemExit("no test count in the coverage run's output")
    return int(found[-1])


def main():
    METRICS.parent.mkdir(parents=True, exist_ok=True)
    numbers = coverage_numbers()
    numbers["tests"] = test_count()
    METRICS.write_text(json.dumps(numbers, indent=2, sort_keys=True) + "\n")
    print("recorded %s" % (METRICS.relative_to(REPO_ROOT),))
    return 0


if __name__ == "__main__":
    sys.exit(main())
