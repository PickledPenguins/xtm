#!/usr/bin/env python3
"""Profile xtm doing what the examples do, and report where the time goes.

The examples are the closest thing in this repository to real use: a whole
config in, a full report out. Timing them says whether it got slower; a
profile says *where*, which is the question you actually have when it did.

Every example is run in-process rather than as a subprocess, because a
profiler cannot see into a child. That is a real limitation and it is worth
stating: what is measured here is the config-reading and reporting path --
the guard, the reader, validation, resolution and the report -- which is
where a config-driven tool spends its time and where every performance
finding so far has been. Placement and the tmux calls are subprocess work
this cannot reach.

    python3 scripts/profile_examples.py            # every example
    python3 scripts/profile_examples.py --top 25   # a longer table
    python3 scripts/profile_examples.py 15-        # only matching examples
"""

import cProfile
import io
import os
import pstats
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "src"))

import xtm  # noqa: E402  (after the path insert, deliberately)

EXAMPLES = REPO_ROOT / "examples"
OUTPUT_DIR = REPO_ROOT / ".build" / "profile"

# Functions that are xtm's own work rather than the standard library's, so the
# table says something about this code rather than about `posixpath`.
OWN_CODE = "xtm.py"


TIERS = ("simple", "moderate", "complex")


def example_dirs(pattern=""):
    """Every example, in reading order: simple, then moderate, then complex."""
    found = []
    for tier in TIERS:
        directory = EXAMPLES / tier
        if not directory.is_dir():
            continue
        found.extend(d for d in sorted(directory.iterdir())
                     if d.is_dir() and (d / "config.yaml").exists())
    return [d for d in found
            if pattern in "%s/%s" % (d.parent.name, d.name)]


def run_one(directory):
    """Read one example's config and produce its report, in this process."""
    profile_file = directory / "profile"
    profile_name = (profile_file.read_text().strip()
                    if profile_file.exists() else None)
    with tempfile.TemporaryDirectory(prefix="xtm-profile-") as tmp:
        tmp = Path(tmp)
        (tmp / "cfg").mkdir()
        (tmp / "cfg" / "config.yaml").write_bytes(
            (directory / "config.yaml").read_bytes())
        flags_file = directory / "flags"
        extra = flags_file.read_text().split() if flags_file.exists() else []
        argv = ["--config", str(tmp / "cfg" / "config.yaml"),
                "--state-dir", str(tmp / "state"), "--quiet", "--json"] + extra
        if profile_name:
            argv += ["--profile", profile_name]
        argv.append("--list")
        # PATH is emptied for the same reason the example runner empties it:
        # no tmux and no geometry tool, so the measurement is about the
        # config path and cannot vary with what is installed.
        saved_path = os.environ.get("PATH", "")
        os.environ["PATH"] = ""
        stdout = sys.stdout
        sys.stdout = io.StringIO()
        # The report is the output; xtm's own warnings about the missing
        # tools are noise here, and every example would repeat them.
        # main() configures logging itself, so raising the level here is
        # undone before the run. Silence the handler instead, which survives
        # it -- the report is the output, and every example would otherwise
        # repeat the same warning about the missing tools.
        saved_filters = list(xtm.logger.filters)
        xtm.logger.addFilter(lambda record: False)
        try:
            xtm.main(argv)
        except SystemExit:
            pass
        finally:
            sys.stdout = stdout
            xtm.logger.filters = saved_filters
            os.environ["PATH"] = saved_path


def summarise(stats, top, examples, seconds):
    """The table that goes into the report, own code first."""
    lines = ["profile: %d example(s) in %.2fs, %d calls"
             % (examples, seconds, stats.total_calls)]
    buffer = io.StringIO()
    stats.stream = buffer
    stats.sort_stats("cumulative").print_stats(OWN_CODE, top)
    rows = [line.rstrip() for line in buffer.getvalue().split("\n")
            # pstats prints its own "reduced N due to restriction" banner,
            # which matches the filter and is not a row.
            if line.strip() and OWN_CODE in line and "restriction" not in line]
    lines.append("  %-9s %-9s %s" % ("cumtime", "tottime", "function"))
    for row in rows[:top]:
        parts = row.split(None, 5)
        if len(parts) < 6:
            continue
        _calls, tottime, _percall, cumtime, _percall2, where = parts
        # Absolute paths make every row the same width of noise.
        where = where.replace(str(REPO_ROOT) + "/", "")
        lines.append("  %-9s %-9s %s" % (cumtime, tottime, where))
    return "\n".join(lines)


def main(argv):
    top = 12
    if "--top" in argv:
        index = argv.index("--top")
        top = int(argv[index + 1])
        argv = argv[:index] + argv[index + 2:]
    pattern = argv[0] if argv else ""

    directories = example_dirs(pattern)
    if not directories:
        print("no example matches %r" % (pattern,), file=sys.stderr)
        return 2

    profiler = cProfile.Profile()
    import time
    start = time.perf_counter()
    for directory in directories:
        profiler.enable()
        run_one(directory)
        profiler.disable()
    elapsed = time.perf_counter() - start

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    stats = pstats.Stats(profiler)
    stats.dump_stats(str(OUTPUT_DIR / "examples.pstats"))

    report = summarise(stats, top, len(directories), elapsed)
    (OUTPUT_DIR / "examples.txt").write_text(report + "\n")
    print(report)
    print("  full profile: .build/profile/examples.pstats "
          "(python3 -m pstats .build/profile/examples.pstats)")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
