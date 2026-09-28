#!/usr/bin/env python3
"""Run the examples, comparing each one against its recorded expectation.

Each example directory holds a config.yaml and an expected.json. This runs
xtm against the config -- reporting only, so no window is opened and no X
server is needed -- and compares the result with what was recorded.

The comparison deliberately drops the fields that cannot be the same on two
machines (absolute paths, the detected window-query tool, live geometry),
because an example is a statement about the CONFIG, not about the machine
reading it.

    python3 examples/run.py                       # all examples
    python3 examples/run.py simple/01-one-screen  # just one
    python3 examples/run.py --show simple/01-one-screen   # print, do not compare
    python3 examples/run.py --record              # rewrite every expected.json
    python3 examples/run.py --record complex/     # rewrite the matching ones
    python3 examples/run.py --negative            # the configs that must fail

An example directory may also carry:

    profile      the profile name to report on, when it is not the default
    flags        extra arguments the example needs, one per line or space
                 separated -- `--all` for an example whose subject is a
                 profile bound to another machine
"""

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE.parent
XTM = REPO_ROOT / "src" / "xtm.py"

# Fields whose value depends on the machine rather than on the config.
VOLATILE_KEYS = ("config", "state_file", "geometry_tool", "live",
                 "frame_compensation", "status")


def strip_volatile(value):
    """Recursively drop machine-dependent fields from a report."""
    if isinstance(value, dict):
        return dict((k, strip_volatile(v)) for k, v in value.items()
                    if k not in VOLATILE_KEYS)
    if isinstance(value, list):
        return [strip_volatile(v) for v in value]
    return value


def run_example(directory, env_extra=None):
    """Return the stripped --list report xtm produces for one example."""
    config = directory / "config.yaml"
    if not config.exists():
        raise SystemExit("%s has no config.yaml" % (directory.name,))

    profile = None
    profile_file = directory / "profile"
    if profile_file.exists():
        profile = profile_file.read_text().strip()

    # An example whose subject is a machine-bound profile cannot be reported
    # on any other machine without --all, since a bound profile is hidden
    # elsewhere by design. A `flags` file carries what such an example needs.
    extra_flags = []
    flags_file = directory / "flags"
    if flags_file.exists():
        extra_flags = flags_file.read_text().split()

    with tempfile.TemporaryDirectory(prefix="xtm-example-") as tmp:
        tmp = Path(tmp)
        (tmp / "cfg").mkdir()
        (tmp / "cfg" / "config.yaml").write_text(config.read_text())

        env = dict(os.environ)
        env.update({
            "XTM_CONFIG_DIR": str(tmp / "cfg"),
            "XTM_STATE_DIR": str(tmp / "state"),
            # Keep the report about the config: with no PATH, xtm finds no
            # tmux and no window-query tool, so nothing on this machine can
            # color the result.
            "PATH": "",
        })
        env.update(env_extra or {})

        argv = [sys.executable, str(XTM)] + extra_flags
        if profile:
            argv.append(profile)
        argv += ["--list", "--json"]

        result = subprocess.run(argv, stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE,
                                universal_newlines=True, env=env, timeout=60)
        if result.returncode != 0:
            raise SystemExit("%s: xtm exited %d\n%s"
                             % (directory.name, result.returncode, result.stderr))
        return strip_volatile(json.loads(result.stdout))


NEGATIVE = HERE / "negative"

# Examples live one level down, in the tier that says how much they combine:
# simple/ shows one idea, moderate/ a few interacting, complex/ every
# mechanism at once. A caller names an example by its path from examples/,
# so `simple/01-one-screen`.
TIERS = ("simple", "moderate", "complex")


def example_names():
    """Every example, in the order they are meant to be read."""
    found = []
    for tier in TIERS:
        directory = HERE / tier
        if not directory.is_dir():
            continue
        found.extend("%s/%s" % (tier, d.name)
                     for d in sorted(directory.iterdir())
                     if d.is_dir() and (d / "config.yaml").exists())
    return found


def run_negative(directory):
    """Run one config that is meant to fail; return its combined output.

    The config is copied into a temporary directory first. xtm adds the
    reserved `default` profile to a config that lacks it and saves that
    immediately, so pointing it at the example directory itself would
    rewrite the very file the example is about -- expanding its flow style,
    dropping its comments, and in one case turning the deliberately broken
    line into a valid one.
    """
    with tempfile.TemporaryDirectory(prefix="xtm-negative-") as tmp:
        tmp = Path(tmp)
        (tmp / "cfg").mkdir()
        (tmp / "cfg" / "config.yaml").write_bytes(
            (directory / "config.yaml").read_bytes())
        env = dict(os.environ)
        env.update({"XTM_CONFIG_DIR": str(tmp / "cfg"),
                    "XTM_STATE_DIR": str(tmp / "state"),
                    "PATH": ""})
        result = subprocess.run([sys.executable, str(XTM), "--validate"],
                                stdout=subprocess.PIPE,
                                stderr=subprocess.STDOUT,
                                universal_newlines=True, env=env, timeout=60)
        # The path the config really had, so the caller can strip exactly
        # that. Matching a literal /tmp instead made the whole catalog
        # depend on $TMPDIR: under TMPDIR=/var/tmp every case failed with a
        # mangled "/varCONFIG", which is the one property a test suite must
        # never have.
        return result.returncode, result.stdout, str(tmp / "cfg" / "config.yaml")


def check_negatives(names, show):
    """Every negative example must fail, with the message it recorded."""
    failures = []
    for name in names:
        directory = NEGATIVE / name
        code, output, config_path = run_negative(directory)
        decisive = ""
        for line in output.split("\n"):
            if "error:" in line or "INVALID:" in line:
                # The config lives in a temporary directory that changes
                # every run, so the recorded message names it as CONFIG.
                decisive = line.strip().replace(config_path, "CONFIG")
                break
        if show:
            print("%s\nrc=%d\n%s\n" % (name, code, decisive))
            continue
        expected_file = directory / "expected-error.txt"
        if not expected_file.exists():
            failures.append("%s has no expected-error.txt" % (name,))
            continue
        expected = expected_file.read_text().strip()
        if code == 0:
            failures.append("%s was accepted; it is meant to fail" % (name,))
        elif expected not in decisive:
            failures.append("%s said:\n     %s\n  expected:\n     %s"
                            % (name, decisive, expected))
        else:
            print("ok       negative/%s" % (name,))
    return failures


def main(argv):
    show = "--show" in argv
    record = "--record" in argv
    argv = [a for a in argv if a not in ("--show", "--record")]

    if "--negative" in argv:
        argv = [a for a in argv if a != "--negative"]
        names = argv or sorted(d.name for d in NEGATIVE.iterdir()
                               if d.is_dir())
        failures = check_negatives(names, show)
        if failures:
            print("\n%d negative example(s) did not fail as recorded:\n"
                  % (len(failures),))
            for failure in failures:
                print("  %s" % (failure,))
            return 1
        if not show:
            print("\n%d negative example(s) refused as recorded." % (len(names),))
        return 0

    # A bare tier name, or any prefix, selects the examples under it: the
    # alternative is typing forty paths to re-record a tier.
    names = example_names()
    if argv:
        selected = []
        for wanted in argv:
            matched = [n for n in names if n == wanted or n.startswith(wanted)]
            if not matched:
                raise SystemExit("no example matches %r" % (wanted,))
            selected.extend(matched)
        names = selected
    failures = []
    for name in names:
        directory = HERE / name
        if not directory.is_dir():
            raise SystemExit("no such example: %s" % (name,))

        env_extra = {}
        actual = run_example(directory, env_extra)
        if show:
            print(json.dumps(actual, indent=2, sort_keys=True))
            continue
        if record:
            # Deliberately a mode of this runner rather than a shell loop:
            # recording forty expectations by hand is how one of them ends
            # up recorded from a different command than the one that checks
            # it.
            (directory / "expected.json").write_text(
                json.dumps(actual, indent=2, sort_keys=True) + "\n")
            print("recorded %s" % (name,))
            continue

        expected_file = directory / "expected.json"
        if not expected_file.exists():
            raise SystemExit("%s has no expected.json; create it with --show"
                             % (name,))
        expected = json.loads(expected_file.read_text())
        if actual == expected:
            print("ok       %s" % (name,))
        else:
            print("FAILED   %s" % (name,))
            failures.append((name, expected, actual))

    for name, expected, actual in failures:
        print("\n--- %s ---" % (name,))
        print("expected:\n%s" % (json.dumps(expected, indent=2, sort_keys=True),))
        print("actual:\n%s" % (json.dumps(actual, indent=2, sort_keys=True),))

    if failures:
        return 1
    if not show:
        print("\n%d example(s) match their recorded output." % (len(names),))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
