# Developing xtm

Architecture, design decisions, how to extend the tool, and how the test suite is put together. For everyday use see the [README](../README.md); for behavioural detail see [reference.md](reference.md).

## Architecture

A single file, `xtm.py`, roughly 2600 lines including documentation. The
layout is bottom-up: primitives first, then the layers that use them, then
the commands, then the CLI. There are no classes beyond a log formatter, a
lock context manager and the error type; everything else is a function
operating on plain dictionaries loaded from YAML.

```
argument parsing  ─┐
                   ├─→ command function ─→ action layer ─→ primitives
config + profile  ─┘
```

| Layer | Responsibility | Representative functions |
|---|---|---|
| Logging | Console and file handlers, level resolution | `setup_logging`, `console_level_from_args`, `_ConsoleFormatter` |
| Errors | Single user-facing exception, raised by `die()` | `XtmError`, `die` |
| Validation | Name charset, geometry shape and numeric sanity | `validate_session_name`, `validate_geometry`, `validate_profile` |
| Subprocess | One wrapper for every external call | `run`, `_subprocess_env`, `which_or_none` |
| File I/O | Atomic, durable writes and clean read errors | `_write_text_safe`, `_read_text_safe`, `_fsync_directory` |
| Locking | Advisory lock around read-modify-write | `config_lock` |
| YAML | PyYAML with a complete dependency-free fallback | `dump_yaml`, `load_yaml`, `_simple_yaml_load`, `_simple_yaml_dump` |
| Config | Load, normalise, validate, save | `resolve_paths`, `load_config`, `save_config`, `validate_profile` |
| State | Per-machine current profile and selection rules | `read_current_profile_state`, `match_profile`, `resolve_profile_name` |
| Naming | Slot-to-session and title resolution | `resolve_session_name`, `resolve_window_title`, `profile_slots`, `slot_for_session` |
| tmux | Session and client queries | `tmux_list_sessions`, `tmux_client_tty_for_pid`, `tmux_kill_session`, `tmux_detach_session` |
| Switching | Disposing of the previous profile's windows | `apply_switch_action`, `switch_mode` |
| X read-back | Window discovery, geometry, decorations, focus | `find_all_xtm_sessions`, `get_window_geometry`, `get_frame_extents`, `focus_window` |
| Placement | Escape sequences, verification, stacking | `place_window`, `verify_placement`, `stack_slot` |
| Actions | Spawning and opening | `spawn_xterm`, `open_session`, `capture_layout` |
| Commands | One function per CLI action | `cmd_open`, `cmd_reset`, `cmd_update_profile`, … |
| CLI | Parser, dispatch table, exit codes | `build_parser`, `main` |

## Key design decisions

**One `RunContext`, not seven globals, and not a threaded parameter.** The
run modes (`dry_run`, `verify`, `frame_compensation`) and resolved paths
(`config_file`, `state_dir`, and the `config_dir` / `state_file` derived from
them) live on a single module-level `CONTEXT` object.

They were seven separate globals. Collecting them does not make them less
global -- helpers still reach for `CONTEXT` rather than being handed it --
but it removes the failure mode that actually bit: there is one thing to save
and restore, so a partial restore is not possible, and derived paths are
computed rather than assigned, so they cannot fall out of step with their
sources. `scoped_context()` is the supported way to override settings for a
block.

Threading a context parameter through all 126 functions was considered and
rejected. It would add an argument that no call site reads to signatures like
`_write_text_safe(path, content, description)`, making every one of them
worse in exchange for a purity that a single-invocation CLI gets nothing
from. If xtm ever needed two configurations live in one process, that
calculation would change.


- **Single file.** The tool has to be copied to servers where nothing can be
  installed; one file with a shebang is the simplest thing that can be
  deployed by `scp`.
- **Python 3.6 floor.** Enterprise distributions ship 3.6, so the code avoids
  `capture_output=`/`text=` (3.7), PEP 585 generics (3.9), PEP 604 unions
  (3.10) and `typing.NoReturn` (3.6.2).
- **Runtime validation over static typing.** Config data is hand-editable
  YAML, so its shape is enforced by explicit checks that produce messages
  naming the profile, session and field, rather than by type declarations
  that cannot see the file.
- **stdout for data, stderr for logs.** Reporting commands stay pipeable
  under any log level.
- **Three module-level run modes.** `DRY_RUN`, `VERIFY` and
  `FRAME_COMPENSATION` are read-only, cross-cutting, and set once at startup;
  threading them through a dozen signatures purely to forward them would add
  noise without adding clarity.
- **Commands return exit codes** rather than calling `sys.exit`, so they are
  directly testable.

## Extending the tool

### Adding a command

`COMMANDS` near the bottom of the file is the single source of truth for
dispatch. Each entry is `(argparse dest, handler, mutates_config,
needs_profile)`:

```python
COMMANDS = (
    ("open", cmd_open, False, True),
    ...
)
```

- **dest** must match the argparse destination of the option that triggers
  it, so `--make-global` pairs with `make_global`.
- **mutates_config** decides whether the run holds the config lock across
  load, modify and save. Set it for anything that calls `save_config`.
- **needs_profile** decides whether an unresolvable profile is fatal.
  Commands that operate on the config as a whole (listing, validating,
  renaming) set it `False` so they still work when no profile can be
  resolved.

A handler takes `(args, config, profile_name)` and returns an exit code; it
must not call `sys.exit`, which is what makes it directly testable. Raise
`XtmError` via `die()` for user-facing failures — `main` turns that into a
single-line message with the traceback available under `--debug`.

`DEFAULT_ACTION` names the entry used when no action flag is given.

### Adding a config key

1. Accept and validate it in `validate_session_entry` or `validate_profile`,
   with a message naming the profile, session and field.
2. Consume it where it applies — `build_xterm_command` for launch settings,
   `place_window` for placement.
3. Make sure it survives a round trip through **both** YAML backends; the
   built-in writer must be able to emit it and the built-in reader to read it
   back.
4. Document it in the session or profile key table above, and add it to
   `config.yaml.example`.

### House rules

- **Single file, standard library only.** The tool is deployed by copying it
  to machines where nothing can be installed.
- **Python 3.6 syntax and API only.** See the module docstring for the
  specific constructs this rules out.
- **stdout is data, stderr is logs.** Anything a script might parse goes
  through `print`; everything else through `logger`.
- **Every external command goes through `run()`**, which supplies the
  timeout, the C locale, debug logging and clean errors for a missing
  program.
- **Comment intent, not mechanism.** Explain why a non-obvious choice was
  made; the code already says what it does.
- **Version bump per release**, incrementing the last component only
  (`0.9` → `0.10`), with a matching `CHANGELOG.md` entry.

### Before releasing

```bash
python3 -m py_compile xtm.py test_xtm.py
python3 -m pyflakes xtm.py test_xtm.py
python3 -m unittest test_xtm
xtm --help                      # confirm the README's option tables still match
```

Check that every option in the parser appears in the README and that no
README option is absent from the parser; a mismatch there is the failure mode
this document is most prone to.

## Data structures

The config is a plain nested dictionary throughout:

```python
{
  "profiles": {
    "<profile>": {
      "match":     {"hostname": str or [str]},            # optional
      "prefix":    str,                                   # optional
      "on_switch": "leave" | "detach" | "kill",           # optional
      "title":     str,                                   # optional template
      "stack":     {"x": int, "y": int, "width": int, "height": int,
                    "offset_x": int, "offset_y": int},
      "sessions": {                                       # slots, keyed by name
        "<slot>": {"x": int, "y": int, "width": int, "height": int,
                   "session": str, "title": str,          # optional
                   "command": str, "cwd": str,            # optional
                   "xterm_args": [str]},                  # optional
      },
    },
  },
}
```

A *geometry* is any mapping with `x`, `y`, `width` and `height`.
`session_geometry()` extracts one from a slot entry, dropping the launch
settings, so placement code never has to know about `command` or `cwd`.

Slot-to-session resolution is centralised in four functions, and no other
code should construct a session name by hand:

| Function | Returns |
|---|---|
| `resolve_session_name(profile, slot)` | The tmux session name for one slot. |
| `resolve_window_title(profile, profile_name, slot, session)` | The window title, slot override before profile template before default. |
| `profile_slots(profile)` | `(slot, session)` pairs in config order. |
| `slot_for_session(profile, session)` | The slot a session occupies, or `None`. |

## Testing

`test_xtm.py` contains 420 tests and requires no display, no window manager,
no tmux and no X server.

```bash
python3 -m unittest test_xtm            # run everything
python3 -m unittest test_xtm -v         # per-test output
python3 -m unittest test_xtm.TestPlacementSequences
```

Two strategies are combined:

- **Unit tests** import `xtm.py` and exercise pure logic directly: YAML round
  trips, comment stripping, flow mappings, geometry validation, name
  validation, stack arithmetic, frame-extent maths, profile resolution
  precedence and argument parsing.
- **Integration tests** run `xtm.py` as a subprocess against fake `tmux`,
  `xterm`, `xdotool`, `wmctrl`, `xwininfo` and `xprop` programs generated
  into a temporary directory and placed at the front of `PATH`. The fakes
  read and write plain text state files, so a test can declare "these
  sessions exist, these windows are at these positions" and then assert on
  the resulting config, exit code and output.

Beyond those two strategies, specific classes of risk get their own groups:

- **CLI combination and ordering** (`TestCliCombinations`): that option order
  never matters, that short, long and pre-0.3 single-dash forms produce
  identical output, that `--config` beats `XTM_CONFIG_DIR` and `--state-dir`
  beats `XTM_STATE_DIR`, that `--profile` beats `--auto` while `--auto` beats
  a saved profile, that `--log-level` overrides both `--debug` and `--quiet`,
  that `--json` affects only the reporting commands, and that `--json
  --quiet` leaves stdout purely machine-readable.
- **Malformed input** (`TestMalformedConfig`): an empty file, binary junk, a
  top-level list, a missing or non-mapping `profiles` key, non-mapping
  profiles and sessions, string and missing geometry fields, negative sizes,
  names containing a colon or a space, unknown and mistyped `match` keys, a
  non-list `xterm_args`, a non-string `command`, and a `stack` missing its
  offsets. Every case must exit 1 with a message naming the problem and
  without a traceback.
- **Documented ordering** (`TestOrderingGuarantees`): that named sessions are
  processed in config order and strays alphabetically, asserted from the
  recorded sequence of tmux calls.
- **Enumeration cost** (`TestEnumerationCost`): the exact number of
  subprocess calls each geometry tool needs, so a change that quietly makes
  discovery more expensive fails a test instead of just running slower.
- **Locking** (`TestConfigLocking`): that the lock is a sibling file rather
  than the config itself, that it is genuinely exclusive between processes,
  that it is released on both normal exit and an exception, and that an
  unusable lock path degrades to an unlocked run instead of an error.
- **Positional profile** (`TestPositionalProfile`): that `xtm desk` matches
  `--profile desk` exactly, that it intermixes with value-taking actions in
  either order without swallowing their arguments, that giving both spellings
  is a usage error even when they agree, and that an unknown name matching a
  session suggests `--open`/`--focus`.
- **The reserved profile** (`TestReservedDefaultProfile`): creation,
  recreation when missing, refusal to delete, rename, rename onto or copy
  onto, refusal of a `match` block, editability of its contents, and its role
  as the last resort of resolution.
- **Machine visibility** (`TestMachineVisibility`): hidden profiles absent
  from listing and unselectable, `--all` revealing them, the hidden count in
  both output formats, `--validate` ignoring visibility, and the explanation
  given when a saved profile becomes hidden.
- **Machine binding** (`TestMakeGlobal`): removing a binding, the resulting
  visibility change, already-global being a no-op rather than an error,
  dry-run safety, and global profiles never being auto-selected.
- **Slot naming** (`TestSessionNaming`, `TestSlotIntegration`): prefix
  application, explicit `session:` never being prefixed, two profiles sharing
  one session by naming it, two profiles not colliding without an override,
  title resolution and template validation, and the rejection of two slots
  resolving to one session.
- **Detaching and switching** (`TestDetachAndSwitch`): detach leaving the
  session alive, all three `on_switch` modes, `--on-switch` overriding the
  profile setting, a shared session surviving a switch, reselecting the same
  profile being a no-op, and `--close-all` under `--detach-mode` and `--all`.
- **Capture scope** (`TestCaptureScope`): foreign windows skipped by default
  and added with `--capture-new`, known slots still updated, stray
  attribution, and the verbose profile listing in both output formats.
- **Documented behaviours** (`TestDocumentedBehaviours`): claims made in this
  README that no other test covered, including all three session statuses,
  that `--delete-session` leaves a running session alone while `--close`
  leaves the profile entry alone, that `--copy-profile` is a deep copy, that
  `--edit` does not revert an invalid result, and that data goes to stdout
  while logs go to stderr.

Placement is verified for real rather than by mocking: a test allocates a pty
pair with `os.openpty()`, tells the fake tmux to report the slave as the
session's client tty, runs a reset, and reads the exact bytes that arrive on
the master, asserting on the literal escape sequences and their order.

The dependency-free YAML path gets its own end-to-end class, which hides
PyYAML from the child process by putting a module that raises `ImportError`
ahead of it on the import path — with a guard test that fails if the hiding
ever stops working. This matters because the built-in parser is the code path
that runs on hosts where nothing can be installed, and it would otherwise be
tested only in isolation.

### Coverage

Measured with `coverage.py`, including subprocess coverage of the integration
tests:

| Metric | Result |
|---|---|
| Statements | 1786 |
| Line coverage | **96.2%** (1718 / 1786) |
| Branches | 814 |
| Branch coverage | **93.2%** (759 / 814) |
| Combined | **95.3%** — the figure `fail_under` enforces |
| Tests | 488, all passing |

The floor is enforced, not just reported: `.coveragerc` sets `fail_under`, so
a drop fails the build rather than being noticed later.

```bash
pip install coverage
make test            # the suite
make test-isolated   # each module in a fresh interpreter
make examples        # every example against its recorded output
make coverage        # coverage, with subprocess tracking
make check           # all of the above, plus lint and a clean-tree assertion
```

The target sets up the subprocess tracking itself; `.coveragerc` in the
repository root carries the `branch` and `parallel` settings it needs.

That setup is what makes the subprocess integration tests count toward
coverage. Without it the measured figure drops to roughly 63%, because most
command code runs in a child process rather than in the test process — a
plain `coverage run -m unittest test_xtm` therefore understates coverage
badly. If you see 63%, the recipe was skipped; it is not a regression.

`make check` runs the linter, the coverage run and an assertion that a full
run leaves no new files behind, which is what keeps `.gitignore` honest.

The suite takes roughly 50 seconds, or about 2.5 minutes under coverage. The
fake terminal deliberately outlives the xtm process that spawned it, as a real
xterm does, but it redirects its inherited stdout and stderr to `/dev/null`
before doing so: otherwise the harness would wait on those pipes rather than
on xtm, and every test that opens a window would block for the fake
terminal's full lifetime.

The uncovered remainder is almost entirely defensive error handling that
cannot be triggered without breaking the filesystem underneath a running
process: `OSError` branches around `fsync`, lock acquisition and temp-file
cleanup, and the `ImportError` fallbacks for `fcntl` and PyYAML.

### What cannot be tested here

Three behaviours are code-reviewed but not executed by the suite, because
they need a real X server and window manager:

1. **Frame compensation against a real window manager.** The arithmetic and
   the `xprop` parsing are unit-tested, and the end-to-end flow is tested
   against a fake `xprop`, but whether a given window manager applies
   requested coordinates to the frame or to the client window is a property
   of that window manager. Confirm on a new setup with
   `xtm --update-profile && xtm --reset --verify`: a stable round trip
   reports no delta.
2. **Whether a given window manager honours the escape sequences at all.**
   The bytes written are asserted exactly; what a window manager does with
   them is not.
3. **Real xterm startup**, including `xterm_args` that a real xterm would
   reject. The fake xterm accepts any arguments; a genuinely invalid one
   surfaces at runtime through the immediate-exit detection in
   `wait_for_client_tty`.

### Static analysis

`pyflakes` reports no findings on `xtm.py` or `test_xtm.py`. Both compile
cleanly under `python3 -m py_compile`. Every function and class carries a
docstring.

## Project structure

```
xtm.py                 The tool. Single file, no imports outside the stdlib.
install.sh             Installer: copies to a PATH directory, optional completion.
xtm-completion.bash    Bash completion for actions, profiles and session names.
config.yaml.example    Annotated example configuration.
pyproject.toml         Packaging, for `pip install -e .`. install.sh stays the
                       primary path: xtm targets machines where pip is not an
                       option, and copying one file is what works there.
Makefile               make test / coverage / lint / examples / check / clean.
.coveragerc            Branch and parallel modes, and the enforced coverage floor.
.gitignore             Build and test artefacts.
.github/workflows/     CI: the suite across Python versions, each crossed with
                       PyYAML present and absent, plus lint and the examples.

README.md              User guide.
docs/reference.md      Behavioural detail: placement, identification, options.
docs/development.md    This document.
CHANGELOG.md           Release history.

tests/
  support.py           Fake external tools and the shared base classes.
  unit/                Pure logic: YAML, validation, geometry, config, CLI.
  integration/         xtm run as a subprocess against the fakes.
  differential/        The two YAML backends must agree.
  fault_injection/     Failure paths a healthy filesystem never reaches.
  load/                Cost budgets.
  safety/              --dry-run invariants.
  examples/            The shipped examples must still describe the tool.

examples/
  run.py               Runs each example and compares it with expected.json.
  README.md            Index of the examples.
  NN-name/             config.yaml, README.md, expected.json.
```

`xtm.py` stays one stdlib-only file on purpose. It has to be copyable to a
machine where nothing can be installed, and `install.sh` copies exactly one
file; splitting it into a package would break that. The repository grows
structure around it instead.

## Future work

- Fractional and monitor-relative geometry (`x: 50%`, `monitor: HDMI-1`)
  derived from `xrandr`, so a profile survives a resolution change and is
  portable between machines.
- Per-session tmux window and pane layouts, which would make `xtm` a
  full workspace manager rather than a placement tool.
- `--restore`, reattaching windows to profile sessions that already exist in
  tmux after an X restart.
- A placement backend using `xdotool`/`wmctrl` instead of escape sequences,
  which would remove the dependency on `allowWindowOps` and open the door to
  terminal emulators other than xterm.
- Z-order control, so overlapping windows come out in a defined stacking
  order.
- Renaming the `match:` block to `machine:`, now that it is hostname-only and
  its purpose is binding rather than general matching.
- A tmux server per profile (`tmux -L <name>`), for hard isolation between
  contexts rather than the namespacing a `prefix` provides.
- Per-slot tmux tab names (`new-session -n`), distinct from the window title.
- Further ways to bind a profile to a context beyond the hostname, once
  real-world use shows which are actually wanted.

## Version history

| Version | Summary |
|---|---|
| 0.5 | Slots separated from sessions: per-profile `prefix`, explicit `session:` for deliberate sharing, and configurable window `title:`. `on_switch` disposal on profile change with `--detach`, `--detach-mode` and `--on-switch`. `--update-profile` scoped to the profile with `--capture-new`. `--list-profiles --verbose` cross-profile view, and stray attribution. |
| 0.4 | Profile name as a positional argument (`xtm desk`); reserved global `default` profile; `match:` reduced to hostname only, accepting a list; machine-specific profiles hidden on other machines with `-A/--all` to reveal; `--make-global`. |
| 0.3 | Production release: Python 3.6 compatibility fixed, old-PyYAML fallback, inline comments and flow mappings in the built-in parser, frame compensation, `WM_CLASS` identification, per-machine state, short options throughout, INFO logging by default, session `command`/`cwd`/`xterm_args`, profile auto-selection, close/focus/set/delete/copy/rename/edit/validate, `--json`, `--dry-run`, `--verify`, config locking, and a 283-test suite. |
| 0.2 | `xwininfo` accepted for read-back; PID-aware client selection at open time; `--debug` and `--log-file`. |
| 0.1 | Initial version: open, reset, reset-all, update-profile, new-profile, list. |

See `CHANGELOG.md` for the full detail of each release.
