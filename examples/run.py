#!/usr/bin/env python3
"""Run the examples, comparing each one against its recorded expectation.

Each example directory holds a config.yaml and an expected.json. This runs
xtm against the config -- reporting only, so no window is opened and no X
server is needed -- and compares the result with what was recorded.

The comparison deliberately drops the fields that cannot be the same on two
machines (absolute paths, the detected window-query tool, live geometry),
because an example is a statement about the CONFIG, not about the machine
reading it.

    python3 examples/run.py                  # all examples
    python3 examples/run.py 01-single-desktop
    python3 examples/run.py --show 01-single-desktop   # print, do not compare
"""

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE.parent
XTM = REPO_ROOT / "xtm.py"

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
            # colour the result.
            "PATH": "",
        })
        env.update(env_extra or {})

        argv = [sys.executable, str(XTM)]
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


def main(argv):
    show = "--show" in argv
    argv = [a for a in argv if a != "--show"]

    names = argv or sorted(d.name for d in HERE.iterdir()
                           if d.is_dir() and (d / "config.yaml").exists())
    failures = []
    for name in names:
        directory = HERE / name
        if not directory.is_dir():
            raise SystemExit("no such example: %s" % (name,))

        env_extra = {}
        # 06 proves the dependency-free path, so it runs with PyYAML hidden.
        if (directory / "block-pyyaml").exists():
            blocker = directory / ".pyyaml-blocker"
            blocker.mkdir(exist_ok=True)
            (blocker / "yaml.py").write_text(
                "raise ImportError('PyYAML hidden by the example runner')\n")
            env_extra["PYTHONPATH"] = str(blocker)
            # Guard the guard. If the blocker ever stopped working, this
            # example would silently test PyYAML twice and still pass, while
            # claiming to prove something about the built-in parser.
            probe = subprocess.run(
                [sys.executable, "-c",
                 "import sys; sys.path.insert(0, %r); import xtm;"
                 " sys.exit(0 if not xtm.HAVE_PYYAML else 1)" % (str(REPO_ROOT),)],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                universal_newlines=True,
                env=dict(os.environ, PYTHONPATH=str(blocker)), timeout=60)
            if probe.returncode != 0:
                raise SystemExit(
                    "%s: PyYAML is still importable, so this example is not "
                    "exercising the dependency-free parser." % (name,))

        actual = run_example(directory, env_extra)
        if show:
            print(json.dumps(actual, indent=2, sort_keys=True))
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
