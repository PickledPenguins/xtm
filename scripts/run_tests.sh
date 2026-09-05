#!/bin/sh
# The one way to run xtm's tests.
#
#   scripts/run_tests.sh                 every module, each in its own interpreter
#   scripts/run_tests.sh --fast          the commit gate: in-process work plus smoke
#   scripts/run_tests.sh --long          the release gate: integration, races, examples
#   scripts/run_tests.sh test_yaml       only the modules whose path matches
#   scripts/run_tests.sh --shared        one interpreter for the whole suite (quick)
#   scripts/run_tests.sh --coverage      the suite under coverage, gated by the floor
#   scripts/run_tests.sh --perf          timing and memory gates, own baseline
#   scripts/run_tests.sh --property      generated inputs against stated invariants
#   scripts/run_tests.sh --mutation      the mutation catalogue
#   scripts/run_tests.sh --lint          pyflakes and a compile check
#   scripts/run_tests.sh --examples      every example, the failures, and a profile
#   scripts/run_tests.sh --profile       where the time goes in real usage
#   scripts/run_tests.sh --full          all of the above
#   scripts/run_tests.sh --list          print the selected modules, run nothing
#   scripts/run_tests.sh --install-hooks install the pre-commit hook
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

# The suite splits on one question: is this cheap enough to run before every
# commit. The fast half is in-process work plus four smoke checks that really
# do start the program, because a smoke test that does not start it tests
# nothing; the slow half runs xtm as a child process hundreds of times, or
# races several of them, and belongs before a release. The split is by
# directory because unittest has no markers and the directories already say
# what kind of test they hold. Every module is in exactly one half -- a
# module in neither is a test nobody runs, which
# tests/integration/test_entry_point.py asserts.
FAST_DIRS="tests/smoke tests/unit tests/differential tests/property tests/safety"
# tests/load holds both cost budgets (cheap) and the timing gates (their own
# run, against a per-machine baseline), so its modules are named rather than
# taken wholesale.
FAST_MODULES="tests.test_documentation tests.load.test_enumeration_cost tests.load.test_scaling"
LONG_DIRS="tests/integration tests/concurrency tests/fault_injection tests/examples"

want_shared=0
want_coverage=0
want_property=0
want_mutation=0
want_lint=0
want_examples=0
want_perf=0
want_list=0
want_profile=0
want_fast=0
want_long=0
want_unit=1
pattern=""

install_hooks() {
    hook="$ROOT/.git/hooks/pre-commit"
    cat > "$hook" <<'HOOK'
#!/bin/sh
# Installed by scripts/run_tests.sh --install-hooks.
# The fast half of the suite plus the linter, before every commit.
exec "$(git rev-parse --show-toplevel)/scripts/run_tests.sh" --lint --fast
HOOK
    chmod +x "$hook"
    echo "installed $hook (runs --lint --fast; bypass with git commit --no-verify)"
}

for arg in "$@"; do
    case "$arg" in
        --shared)    want_shared=1 ;;
        --fast)      want_fast=1 ;;
        --long)      want_long=1 ;;
        --coverage)  want_coverage=1; want_unit=0 ;;
        --perf)      want_perf=1; want_unit=0 ;;
        --property)  want_property=1; want_unit=0 ;;
        --mutation)  want_mutation=1; want_unit=0 ;;
        --lint)      want_lint=1; want_unit=0 ;;
        --examples)  want_examples=1; want_unit=0 ;;
        --profile)   want_profile=1; want_unit=0 ;;
        --list)      want_list=1; want_unit=0 ;;
        --full)      want_lint=1; want_examples=1; want_coverage=1
                     want_property=1; want_mutation=1; want_perf=1 ;;
        --install-hooks) install_hooks; exit 0 ;;
        -h|--help)   sed -n '2,20p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
        -*)          echo "unknown option: $arg" >&2; exit 2 ;;
        *)           pattern="$arg" ;;
    esac
done

search_roots() {
    if [ "$want_fast" = 1 ]; then
        echo "$FAST_DIRS"
    elif [ "$want_long" = 1 ]; then
        echo "$LONG_DIRS"
    else
        echo tests
    fi
}

modules() {
    # shellcheck disable=SC2046
    find $(search_roots) -name 'test_*.py' 2>/dev/null \
        | sed 's|/|.|g; s|\.py$||' | sort | {
        if [ "$want_fast" = 1 ]; then
            { cat; echo "$FAST_MODULES" | tr " " "\n"; } | grep -v '^$' | sort
        else
            cat
        fi
    } | {
        if [ -n "$pattern" ]; then grep -- "$pattern" || true; else cat; fi
    }
}

no_match() {
    echo "no test module matches ${pattern:-*}" >&2
    exit 2
}

run_lint() {
    echo "--- lint ($(date +%H:%M:%S))"
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
            # The guard matters more here than below: with no modules this
            # would be a bare `python -m unittest`, which is discovery from
            # the current directory -- so a typo ran the whole suite from
            # the repository root and said nothing about the pattern.
            [ -n "$(modules)" ] || no_match
            $PYTHON -m unittest $(modules) -v
        else
            $PYTHON -m unittest discover -t . -s tests -v
        fi
        return
    fi
    total=$(modules | wc -l | tr -d " ")
    [ "$total" = 0 ] && no_match
    index=0
    started=$(date +%s)
    for module in $(modules); do
        index=$((index + 1))
        module_started=$(date +%s)
        # The count and the elapsed time are the whole point: a run that
        # takes two and a half minutes and says nothing looks like a hang,
        # and the first question anyone has is which module is slow.
        echo "--- [$index/$total] $module"
        $PYTHON -m unittest "$module"
        echo "    done in $(($(date +%s) - module_started))s"
    done
    echo "Every test module passes on its own ($total modules, $(($(date +%s) - started))s)."
}

run_coverage() {
    echo "--- coverage ($(date +%H:%M:%S))"
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

run_perf() {
    echo "--- performance ($(date +%H:%M:%S))"
    # Outside both halves on purpose: a timing test inside a pass/fail suite
    # that mixes it with unit tests is a flaky test waiting to happen. The
    # baseline lives under .build/ and is per machine.
    $PYTHON -m unittest tests.load.test_performance
}

run_property() {
    echo "--- property ($(date +%H:%M:%S))"
    if [ ! -d "$ROOT/tests/property" ]; then
        echo "no property tests in this tree; skipped."
        return
    fi
    $PYTHON tests/property/run.py
}

run_mutation() {
    echo "--- mutation ($(date +%H:%M:%S))"
    if [ ! -d "$ROOT/tests/mutation" ]; then
        echo "no mutation catalogue in this tree; skipped."
        return
    fi
    $PYTHON tests/mutation/run.py
}

run_examples() {
    echo "--- examples ($(date +%H:%M:%S))"
    $PYTHON examples/run.py
    $PYTHON examples/run.py --negative
    # The examples are the closest thing here to real usage, so a run that
    # executes them says where the time went as well as whether they passed.
    run_profile
}

run_profile() {
    echo "--- profile ($(date +%H:%M:%S))"
    $PYTHON scripts/profile_examples.py
}

if [ "$want_list" = 1 ]; then
    modules
    exit 0
fi

[ "$want_fast" = 1 ] || [ "$want_long" = 1 ] && want_unit=1
[ "$want_lint" = 1 ] && run_lint
[ "$want_unit" = 1 ] && run_unit
[ "$want_examples" = 1 ] && run_examples
[ "$want_profile" = 1 ] && [ "$want_examples" = 0 ] && run_profile
[ "$want_coverage" = 1 ] && run_coverage
[ "$want_perf" = 1 ] && run_perf
[ "$want_property" = 1 ] && run_property
[ "$want_mutation" = 1 ] && run_mutation
exit 0
