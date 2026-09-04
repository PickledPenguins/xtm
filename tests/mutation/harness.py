"""Apply one mutant to a copy of the tree and see whether the suite kills it.

A mutant is a single semantic decision, deliberately broken. If the suite
still passes, no test was pinning that decision -- which is a missing test,
not a curiosity about the mutant.

The work happens in a copy under .build/ rather than in place: a mutation run
that dies partway through must not leave a mutated xtm.py behind, and copying
a four-thousand-line file plus the tests costs a fraction of what running the
tests costs.
"""

import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
BUILD = REPO_ROOT / ".build"

# Copied into each mutant's tree. Everything the tests import or read.
COPIED = ("xtm.py", "tests", "examples", "config.yaml.example", ".coveragerc")

IGNORED = shutil.ignore_patterns("__pycache__", "*.pyc", ".build")


class Mutant(object):
    """One broken decision, and the tests that ought to notice."""

    def __init__(self, name, find, replace, killed_by, why):
        self.name = name
        self.find = find
        self.replace = replace
        # Naming the modules keeps a run to seconds instead of minutes. A
        # mutant whose listed modules pass is still reported as surviving,
        # so a wrong guess here reads as a missing test rather than hiding
        # one.
        self.killed_by = killed_by
        self.why = why

    def apply_to(self, source):
        """The mutated source, or None when the pattern no longer matches."""
        if source.count(self.find) != 1:
            return None
        return source.replace(self.find, self.replace, 1)


def build_tree(mutant, source):
    """A temporary copy of the tree with one mutant applied."""
    BUILD.mkdir(exist_ok=True)
    tree = Path(tempfile.mkdtemp(prefix="mutant-", dir=str(BUILD)))
    for name in COPIED:
        origin = REPO_ROOT / name
        if not origin.exists():
            continue
        target = tree / name
        if origin.is_dir():
            shutil.copytree(str(origin), str(target), ignore=IGNORED)
        else:
            shutil.copy2(str(origin), str(target))
    (tree / "xtm.py").write_text(source)
    return tree


def run_mutant(mutant, timeout=600):
    """Run one mutant's tests. Returns (outcome, detail).

    outcome is "killed", "survived", or "stale" when the code the mutant
    describes no longer exists in the form it names.
    """
    source = (REPO_ROOT / "xtm.py").read_text()
    mutated = mutant.apply_to(source)
    if mutated is None:
        return "stale", ("the pattern %r matches %d times, not once"
                         % (mutant.find, source.count(mutant.find)))
    tree = build_tree(mutant, mutated)
    try:
        result = subprocess.run(
            [sys.executable, "-m", "unittest"] + list(mutant.killed_by),
            cwd=str(tree), stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            universal_newlines=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        # A mutation that hangs is a mutation the suite noticed, loudly.
        return "killed", "timed out"
    finally:
        shutil.rmtree(str(tree), ignore_errors=True)
    if result.returncode != 0:
        first = next((line for line in result.stdout.splitlines()
                      if line.startswith(("FAIL:", "ERROR:"))), "")
        return "killed", first
    return "survived", "every listed test still passed"
