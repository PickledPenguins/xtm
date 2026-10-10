# Testing

How to run the suite, what each part covers, and the standard tests are held to.

## Running it

```sh
tests/run-tests.sh --fast          # the commit gate: ~25s, and what the hook runs
tests/run-tests.sh --long          # the release gate: integration, races, examples
tests/run-tests.sh                 # both halves, each module in its own interpreter
tests/run-tests.sh test_yaml       # only the modules whose path matches
tests/run-tests.sh --perf          # timing and memory gates, against this machine
tests/run-tests.sh --coverage      # the suite under coverage, gated by the floor
tests/run-tests.sh --lint          # pyflakes and a compile check
tests/run-tests.sh --examples      # every example, the ones meant to fail, the workflows
tests/run-tests.sh --list          # which modules a selection covers, run nothing
tests/run-tests.sh --install-hooks # install the pre-push hook, switched off
tests/run-tests.sh --full          # all of the above
make check                           # --full, plus the clean-tree assertion
```

## The two halves

The suite splits on one question: is this cheap enough to run before every push.

**Fast** — about 25 seconds — is in-process work plus four smoke checks that really do
start the program, because a smoke test that does not start it tests nothing. It is what
`.git/hooks/pre-push` (`scripts/pre-push`) runs, alongside the linter, on each pushed commit
in a clean checkout of it; install it with `tests/run-tests.sh --install-hooks`, which links
it switched off as `.git/hooks/pre-push.off`, switch it on by renaming the link to
`pre-push`, and bypass it with `git push --no-verify`.  Commits are not gated.

**Long** — about two and a half minutes — runs xtm as a child process hundreds of times,
races several of them, and executes every example. It gates a release.

The split is by directory, because unittest has no markers and the directories already say
what kind of test they hold. **Every module is in exactly one half**, which
`tests/integration/test_entry_point.py` asserts by asking `--list` rather than by running
anything: a new test directory that joins neither half would otherwise be a test nobody
runs. The timing gates are the one deliberate exception, and are named as such.

`tests/run-tests.sh` is the only thing that knows how to run anything in this repository;
the Makefile delegates to it. Every artifact lands under `.build/`.

The suite needs no display, no window manager, no tmux and no X server. It takes about 160
seconds, or about 25 minutes under coverage, which traces every child process.

**Each module runs in a fresh interpreter by default.** xtm keeps state at module level —
the run context, the logger's level, the resolved paths — so a suite that passes only when
modules share an interpreter is hiding a leak between them. One such dependency was found
exactly this way. `--shared` exists for a quick answer while working, not as a gate.

## How the tests work

Two strategies, combined:

- **Unit tests** import `xtm.py` and exercise pure logic directly: YAML round trips, comment
  stripping, flow mappings, geometry validation, name validation, stack arithmetic,
  frame-extent maths, profile resolution precedence and argument parsing.
- **Integration tests** run `xtm.py` as a subprocess against fake `tmux`, `xterm`,
  `xdotool`, `wmctrl`, `xwininfo` and `xprop` programs generated into a temporary directory
  and placed at the front of `PATH`. The fakes read and write plain text state files, so a
  test can declare "these sessions exist, these windows are at these positions" and then
  assert on the resulting config, exit code and output.

Placement is verified for real rather than by mocking: a test allocates a pty pair with
`os.openpty()`, tells the fake tmux to report the slave as the session's client tty, runs a
reset, and reads the exact bytes that arrive on the master, asserting on the literal escape
sequences and their order.

The fakes are required to fail the way the real tools fail. The fake tmux renders the
`-F` format xtm sends and, under the C locale xtm runs it in, rewrites control characters
to `_` exactly as tmux 3.6 does — the tab-separated parsing that broke every real machine
passed the whole suite until the fake learned this. A missing PyYAML is tested end to end
by hiding it from the child process behind a module that raises `ImportError`, with a
guard test that fails if the hiding ever stops working.

## What each directory covers

| Directory | Covers |
|---|---|
| `tests/unit/` | Pure logic: YAML, validation, geometry, config, commands, CLI parsing |
| `tests/integration/` | xtm as a subprocess against the fakes: basics, environments, malformed input, reporting, packaging, windows, command failures |
| `tests/property/` | Stated invariants over generated configs, names and geometries |
| `tests/mutation/` | Decisions deliberately broken, which the suite must notice |
| `tests/concurrency/` | Real processes contending for the config, the lock and the state file |
| `tests/fault_injection/` | Failure paths a healthy filesystem never reaches, and the world outside the process |
| `tests/load/` | Cost budgets: enumeration calls per tool, scaling |
| `tests/safety/` | `--dry-run` changes nothing; `--list`, `--list-profiles`, `--validate` and `--check` with `-p` neither switch nor save the profile |
| `tests/examples/` | Every shipped example still describes the tool, at all three levels |
| `tests/test_documentation.py` | The documentation describes the tool that ships with it |

Classes of risk that carry their own groups, and what each protects:

- **CLI combination and ordering** — option order never matters; short and long forms
  produce identical output; `--config` beats `XTM_CONFIG_DIR`;
  `--profile` beats `--auto` while `--auto` beats a saved profile; `--log-level` overrides
  `--debug` and `--quiet`; `--json` affects only the reporting commands.
- **Malformed input** — an empty file, binary junk, a top-level list, a missing or
  non-mapping `profiles` key, string and missing geometry fields, negative sizes, names
  containing a colon or a space, unknown `match` keys, a non-list `xterm_args`. Every case
  exits 1 with a message naming the problem and no traceback.
- **Documented ordering** — named sessions in config order, strays alphabetically, asserted
  from the recorded sequence of tmux calls.
- **Enumeration cost** — the exact number of subprocess calls each geometry tool needs, so a
  change that quietly makes discovery more expensive fails rather than just running slower.
- **Locking** — the lock is a sibling file rather than the config itself, is genuinely
  exclusive between processes, is released on both normal exit and an exception, and
  degrades to an unlocked run when the path is unusable.
- **The reserved profile** — creation, recreation when missing, refusal to delete, rename,
  rename onto or copy onto, refusal of a `match` block, and its role as the last resort of
  resolution.
- **Machine visibility and binding** — hidden profiles absent from listing and
  unselectable, `--all` revealing them, `--validate` ignoring visibility, `--make-global`
  and its one-way nature.
- **Slot naming** — prefix application, explicit `session:` never prefixed, two profiles
  sharing one session deliberately, two profiles not colliding without an override, title
  resolution, and the rejection of two slots resolving to one session.
- **Detaching and switching** — detach leaving the session alive, all three `on_switch`
  modes, `--on-switch` overriding the profile setting, a shared session surviving a switch.
- **Capture scope** — foreign windows skipped by default and added with `--capture-new`,
  known slots still updated, stray attribution.

## Properties

`tests/property/` states what must be true of *every* input rather than of a chosen one:
what xtm writes it reads back unchanged and in order, text YAML would type is quoted on
the way out, parsing inverts the geometry format, stack offsets are monotonic in the index,
a valid name survives a round trip as a key, and an explicit `session:` is never
prefixed.

The engine is `tests/property/engine.py` — under sixty lines of generate, check, shrink.
Hypothesis is deliberately not used: xtm's premise is running where nothing can be
installed, and a property test that skips because a dependency is missing tests nothing.
What that gives up is real shrinking strategy and a corpus; what it keeps is many inputs
per property, fixed seeds so a failure reproduces, and a counterexample small enough to
read.

## Mutation

`tests/mutation/run.py` is a catalog of decisions deliberately broken — YAML keys read as
written, duplicate keys refused, key order kept on writing; the tmux field separator,
which clients count as xtm's windows, waiting for the xterm just spawned; bringing a
profile up, off-screen reports, held failing commands, `--check` on a missing `cwd`;
resize before move, the sign of frame compensation, the stack offset, an explicit session
never prefixed; the switch keep set; edit-time validation and restore, reading never
rewriting the config, the hostname override reaching the state file, a rename following
every machine, and the config lock —
each naming the test modules that ought to fail because of it.

**A surviving mutant is a missing test.** Write the test; never soften the mutant. A stale
mutant means the code moved: re-point it after checking the decision is still being made
somewhere.

A checked-in catalog rather than a mutation tool, for the same reason as the property
engine: it adds no dependency, it is reviewable, and naming the modules per mutant keeps a
full run to a couple of minutes.

## Concurrency

`tests/concurrency/` starts real processes, because the lock is an `flock` held by a
process and a lost update is a property of separate memory. Each race repeats eight times —
a race that passes once has not been tested.

## Profiling

Any run that executes the examples prints a profile with them:
`tests/run-tests.sh --examples`, `--full`, and `make check`. `--profile` runs it alone.

The examples are the closest thing here to real use — a whole config in, a full report out
— so they are the right place to ask where the time goes. The table names xtm's own
functions by cumulative time, and the full profile is saved to
`.build/profile/examples.pstats` for `python3 -m pstats` to explore.

What it measures, and what it cannot: the examples are re-run **in-process** for this,
because a profiler cannot see into a subprocess. That covers the config-reading and
reporting path — reading, validation, resolution, the report — which is where
a config-driven tool spends its time and where every performance finding so far has been.
Placement and the tmux calls are subprocess work this cannot reach; the timing gates below
cover the parts of that which matter.

## Performance

`tests/run-tests.sh --perf` times reading and writing a thousand-slot config, and asserts a memory ceiling for reading one. Two
things make the numbers mean something:

- **The baseline is per machine**, recorded under `.build/perf-baseline.json` and
  gitignored. A threshold calibrated on another machine is a coin toss. A new case records
  and skips on its first run, then gates from the second.
- **The threshold comes from measured noise.** Repeated runs of an unchanged tree here move
  a median by up to about 35%, so the gate sits at +100%: a real regression in this code is
  a factor, not a fraction, and a tighter gate would fail on nothing but weather.

The baseline keeps the smaller of the recorded and the new value, so a slow afternoon
cannot ratchet it upward until it stops gating. Verified by making the reader sleep and
watching the gate fire.

**They stand down under coverage.** Tracing every line makes everything several times
slower, so a timing gate run that way measures the tracer rather than the code; the cases
skip themselves when instrumentation is active and say so.

## Coverage

Measured on the combined figure — statements and branch exits over one denominator — which
is the strictest of the three numbers and therefore holds the other two above it.

| Metric | Result |
|---|---|
| Statements | 1851 |
| Line coverage | **98.8%** |
| Branch coverage | **96.5%** |
| Combined | **98.1%** — the figure `fail_under` enforces |
| Tests | 678 |

Measured at commit `a0f5e54` with the uncommitted fixes of 2026-10-09 applied. **These numbers are checked, not trusted:**
`tests/run-tests.sh --coverage` records what it measured under `.build/metrics.json`, and
`tests/test_documentation.py` fails when this table disagrees with it. They had gone stale
three times before that existed. The floor is enforced rather than reported:
`pyproject.toml` sets `fail_under` under `[tool.coverage.report]`, so a drop fails the run. It moves in steps, each earned by
the tests that raised the number — 95 to 96 to 97 — rather than to match a figure that
drifted upward on its own.

Coverage requires subprocess tracking, and this is not optional: most command code runs in a
child process, so a plain `coverage run -m unittest` reports about 63% — a measurement
artifact, not a regression. `tests/run-tests.sh --coverage` writes the `.pth` file and
sets `COVERAGE_PROCESS_START` itself. **If you see 63%, you bypassed the entry point.**

The uncovered remainder is 23 statements and 27 partial branches, all defensive: the
`OSError` from a directory `fsync`; type guards behind validation that has already
refused what they check for; parse fallbacks for lines no supported version of a
geometry tool prints; a geometry tool that answers one call and fails the next; a state
file deleted between the check and the unlink; and the `return` after a `die`. None can
be reached without breaking the filesystem or a tool underneath a running process, or
bypassing validation, so covering them would mean asserting on internals: a suite that
breaks when the code is refactored rather than when it is wrong.

## Standards

- **Every test asserts a value, a message, an exit code or a state change.** Never that
  something ran.
- **A regression test is named for the defect** and says in a docstring what went wrong.
- **A fixed count in a test is a claim**: `test_there_are_examples_to_run` guards the
  example sweep so a discovery bug cannot make every other example test vacuously pass.
- **An example directory may carry more than a config.** `profile` names the profile to
  report on, `flags` adds arguments the example needs — `--all` for one whose subject is a
  profile bound to another machine.
  `python3 examples/run.py --record [prefix]` rewrites the recorded output after a
  deliberate change; read the diff.
- **The examples are fixtures.** A new directory under `examples/` is under test as soon as
  it exists; there is no list to update.
- **The workflows run against fakes.** `examples/workflows/` runs command sequences
  (switch, capture, `--set`, copy and rename, `--check`) against the fake `tmux`, `xterm`
  and geometry tools of `tests/support.py`, not a real X server. A transcript shows what
  xtm decides and prints; where a real window manager puts the window is outside it (see
  below). `python3 examples/run.py --workflows --record [prefix]` re-records them.

## What cannot be tested here

Three behaviors are code-reviewed but not executed, because they need a real X server and
window manager:

1. **Frame compensation against a real window manager.** The arithmetic and the `xprop`
   parsing are unit-tested and the end-to-end flow runs against a fake `xprop`, but whether a
   given window manager applies requested coordinates to the frame or the client window is a
   property of that window manager. Confirm on a new setup with `xtm --update-profile && xtm
   --reset --verify`: a stable round trip reports no delta.
2. **Whether a given window manager honors the escape sequences at all.** The bytes written
   are asserted exactly; what a window manager does with them is not.
3. **Real xterm startup**, including `xterm_args` a real xterm would reject. The fake xterm
   accepts any arguments; a genuinely invalid one surfaces at runtime through the
   immediate-exit detection in `wait_for_client_tty`.

## Static analysis

`pyflakes` reports no findings on `src/xtm.py`, `tests/`, `scripts/` or `examples/run.py`, and all of it compiles
cleanly under `python3 -m py_compile`. `tests/run-tests.sh --lint` runs both, with the
interpreter `PYTHON` names (`python3` by default); when pyflakes is not importable there it
names that interpreter and stops, and `PYTHON=python3.12 tests/run-tests.sh --lint` points
it at one that has it. Every
function and class carries a docstring.
