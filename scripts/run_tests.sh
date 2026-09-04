#!/bin/sh
# The one way to run xtm's tests.
#
#   scripts/run_tests.sh                 every module, each in its own interpreter
#   scripts/run_tests.sh test_yaml       only the modules whose path matches
#   scripts/run_tests.sh --shared        one interpreter for the whole suite (fast)
#   scripts/run_tests.sh --coverage      the suite under coverage, gated by the floor
#   scripts/run_tests.sh --property      the property tests, if Hypothesis is present
#   scripts/run_tests.sh --mutation      the mutation catalogue
#   scripts/run_tests.sh --lint          pyflakes and a compile check
#   scripts/run_tests.sh --examples      every example against its recorded output
#   scripts/run_tests.sh --full          all of the above, in that order
#
# Each module gets a fresh interpreter by default because xtm keeps state at
# module level -- the run context, the logger's level, the resolved config
# paths -- and a suite that only passes when modules share an interpreter is
# hiding a leak between them. --shared is there for a quick answer while
# working, not for a gate.
#
# Every artefact this script produces lands under .build/. Nothing it runs
# writes to the project root.

set -eu

PYTHON=${PYTHON:-python3}
ROOT=$(cd "$(dirname "$0")/.." && pwd)
BUILD="$ROOT/.build"
COVERAGE_BOOTSTRAP="$BUILD/coverage-bootstrap"

cd "$ROOT"
mkdir -p "$BUILD"

# Keeps .pyc files out of the source tree; harmless on Python 3.8+, ignored
# by anything older, which is why it is set rather than tested for.
PYTHONPYCACHEPREFIX="$BUILD/pycache"
export PYTHONPYCACHEPREFIX

want_shared=0
want_coverage=0
want_property=0
want_mutation=0
want_lint=0
want_examples=0
want_unit=1
pattern=""

for arg in "$@"; do
    case "$arg" in
        --shared)    want_shared=1 ;;
        --coverage)  want_coverage=1; want_unit=0 ;;
        --property)  want_property=1; want_unit=0 ;;
        --mutation)  want_mutation=1; want_unit=0 ;;
        --lint)      want_lint=1; want_unit=0 ;;
        --examples)  want_examples=1; want_unit=0 ;;
        --full)      want_lint=1; want_examples=1; want_coverage=1
                     want_property=1; want_mutation=1 ;;
        -h|--help)   sed -n '2,20p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
        -*)          echo "unknown option: $arg" >&2; exit 2 ;;
        *)           pattern="$arg" ;;
    esac
done

modules() {
    find tests -name 'test_*.py' | sed 's|/|.|g; s|\.py$||' | sort | {
        if [ -n "$pattern" ]; then grep -- "$pattern" || true; else cat; fi
    }
}

run_lint() {
    echo "--- lint"
    files="xtm.py $(find tests scripts -name '*.py' 2>/dev/null || true)"
    # shellcheck disable=SC2086
    $PYTHON -m pyflakes $files
    # shellcheck disable=SC2086
    $PYTHON -m py_compile $files
    echo "pyflakes and compile clean."
}

run_unit() {
    if [ "$want_shared" = 1 ]; then
        echo "--- suite, one interpreter"
        if [ -n "$pattern" ]; then
            $PYTHON -m unittest $(modules) -v
        else
            $PYTHON -m unittest discover -t . -s tests -v
        fi
        return
    fi
    found=0
    for module in $(modules); do
        found=1
        echo "--- $module"
        $PYTHON -m unittest "$module"
    done
    if [ "$found" = 0 ]; then
        echo "no test module matches ${pattern:-*}" >&2
        exit 2
    fi
    echo "Every test module passes on its own."
}

run_coverage() {
    echo "--- coverage"
    # The .pth file runs at interpreter startup in EVERY child process, which
    # is the only hook early enough to measure a subprocess from its first
    # line. Without it the reported figure collapses to about 63%, which is a
    # measurement artefact rather than a regression.
    mkdir -p "$COVERAGE_BOOTSTRAP"
    printf 'import coverage; coverage.process_startup()\n' \
        > "$COVERAGE_BOOTSTRAP/coverage-subprocess.pth"
    rm -rf "$BUILD/coverage"
    mkdir -p "$BUILD/coverage"
    COVERAGE_PROCESS_START="$ROOT/.coveragerc" \
    PYTHONPATH="$COVERAGE_BOOTSTRAP" \
        $PYTHON -m coverage run -m unittest discover -t . -s tests
    $PYTHON -m coverage combine
    $PYTHON -m coverage report
}

run_property() {
    echo "--- property"
    if [ ! -d "$ROOT/tests/property" ]; then
        echo "no property tests in this tree; skipped."
        return
    fi
    $PYTHON tests/property/run.py
}

run_mutation() {
    echo "--- mutation"
    if [ ! -d "$ROOT/tests/mutation" ]; then
        echo "no mutation catalogue in this tree; skipped."
        return
    fi
    $PYTHON tests/mutation/run.py
}

run_examples() {
    echo "--- examples"
    $PYTHON examples/run.py
}

[ "$want_lint" = 1 ] && run_lint
[ "$want_unit" = 1 ] && run_unit
[ "$want_examples" = 1 ] && run_examples
[ "$want_coverage" = 1 ] && run_coverage
[ "$want_property" = 1 ] && run_property
[ "$want_mutation" = 1 ] && run_mutation
exit 0
