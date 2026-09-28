# Architecture

How xtm is built, where each behavior lives, and what to know before changing it.

## Shape

One file, `src/xtm.py`, four and a half thousand lines including its documentation. The layout is bottom-up:
primitives first, then the layers that use them, then the commands, then the CLI. There are
four classes — a log formatter, the lock context manager, the error type and `RunContext`;
everything else is a function over plain dictionaries loaded from YAML.

```
argument parsing  ─┐
                   ├─→ command function ─→ action layer ─→ primitives
config + profile  ─┘
```

| Layer | Responsibility | Representative functions |
|---|---|---|
| Run context | Resolved paths and run modes for one invocation | `RunContext`, `CONTEXT`, `scoped_context`, `resolve_paths` |
| Logging | Console and file handlers, level resolution | `setup_logging`, `console_level_from_args`, `_ConsoleFormatter` |
| Errors | Single user-facing exception, raised by `die()` | `XtmError`, `die` |
| Validation | Name charset, geometry shape and numeric sanity | `validate_session_name`, `validate_geometry`, `validate_profile` |
| Subprocess | One wrapper for every external call | `run`, `_subprocess_env`, `which_or_none` |
| File I/O | Atomic, durable writes and clean read errors | `_write_text_safe`, `_read_text_safe`, `_fsync_directory` |
| Locking | Advisory lock around read-modify-write | `config_lock` |
| YAML | PyYAML, with keys read as names and key order kept | `load_yaml`, `dump_yaml`, `_ConfigLoader`, `_ConfigDumper` |
| Config | Load, validate, save, guarded edits | `load_config`, `save_config`, `validate_profile`, `edit_profile` |
| State | Per-machine current profile and selection rules | `read_current_profile_state`, `match_profile`, `resolve_profile_name` |
| Naming | Slot-to-session and title resolution | `resolve_session_name`, `resolve_window_title`, `profile_slots`, `slot_for_session` |
| tmux | Session and client queries | `tmux_list_sessions`, `tmux_client_tty_for_pid`, `tmux_kill_session`, `tmux_detach_session` |
| Switching | Disposing of the previous profile's windows | `apply_switch_action`, `switch_mode` |
| X read-back | Window discovery, geometry, decorations, focus | `find_all_xtm_sessions`, `get_window_geometry`, `get_frame_extents`, `focus_window` |
| Placement | Escape sequences, verification, stacking | `place_window`, `verify_placement`, `stack_slot` |
| Actions | Spawning and opening | `spawn_xterm`, `open_session`, `capture_layout` |
| Commands | One function per CLI action | `cmd_open`, `cmd_reset`, `cmd_update_profile`, … |
| CLI | Parser, dispatch table, exit codes | `build_parser`, `main`, `cli` |

## Key design decisions

**Single file.** The tool has to be copied to servers where nothing else can be installed
beyond PyYAML; one file with a shebang is the simplest thing that can be deployed by `scp`.
`install.sh` copies
exactly one file. Splitting it into a package would break that, so the repository grows
structure around it instead.

**Python 3.6 floor.** Enterprise distributions ship 3.6, so the code avoids
`capture_output=`/`text=` (3.7), PEP 585 generics (3.9), PEP 604 unions (3.10) and
`typing.NoReturn` (3.6.2).

**One `RunContext`, not seven globals, and not a threaded parameter.** The run modes
(`dry_run`, `verify`, `frame_compensation`) and resolved paths (`config_file`, `state_dir`,
and the `config_dir` / `state_file` derived from them) live on a single module-level
`CONTEXT` object.

They were seven separate globals. Collecting them does not make them less global — helpers
still reach for `CONTEXT` rather than being handed it — but it removes the failure mode that
actually bit: there is one thing to save and restore, so a partial restore is not possible,
and derived paths are computed rather than assigned, so they cannot fall out of step with
their sources. `scoped_context()` is the supported way to override settings for a block.

Threading a context parameter through all 126 functions was considered and rejected. It
would add an argument no call site reads to signatures like `_write_text_safe(path, content,
description)`, making every one of them worse in exchange for a purity a single-invocation
CLI gets nothing from. If xtm ever needed two configurations live in one process, that
calculation would change.

**Runtime validation over static typing.** Config data is hand-editable YAML, so its shape
is enforced by explicit checks producing messages that name the profile, session and field,
rather than by type declarations that cannot see the file.

**Commands return exit codes** rather than calling `sys.exit`, so they are directly
testable. `cli()` is the layer that turns `XtmError` into a one-line message and
`KeyboardInterrupt` into exit 130; both `python3 src/xtm.py` and the console script installed by
`pyproject.toml` call it, which is what keeps a pip-installed xtm behaving like the copied
script.

**Edits are validated before they are saved.** `edit_profile` wraps any command that changes
a profile, re-runs the validation the next load would perform, and restores the profile if
the edit does not survive it. Without it a command could write a config that every later
command refused to load, leaving hand-editing as the only way back.

**stdout for data, stderr for logs**, so reporting commands stay pipeable under any log
level.

## Reading the config

The config is read and written by PyYAML alone, through two small subclasses:

- `_ConfigLoader` builds every mapping key from the text written, so a key is always a
  name — `on:` stays `on` rather than becoming `True` — and refuses a key written twice
  in one mapping, which YAML would otherwise collapse to its last value. Duplicates are
  counted among the keys written, before a merge key is folded in, so a key beside
  `<<: *base` overrides rather than collides.
- `_ConfigDumper` keeps key order (slots are placed in the order written, and every
  PyYAML release sorts a plain dict) and writes no anchors.

Both use the libyaml-backed classes when PyYAML was built with them, which read a
thousand-slot config in about 0.04s.

Values keep YAML's own typing. `require_text` turns "YAML read `yes` as a boolean" into a
message naming the field and saying to quote it. A second, built-in reader existed until
2026-09-27 so that xtm could run without PyYAML, with a guard refusing everything the two
readers disagreed about; it was the most defect-prone part of the program, and the owner
decided PyYAML is always available. The reasoning is in [future-work.md](future-work.md).

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

A *geometry* is any mapping with `x`, `y`, `width` and `height`. `session_geometry()`
extracts one from a slot entry, dropping the launch settings, so placement code never has to
know about `command` or `cwd`.

Slot-to-session resolution is centralised in four functions, and no other code constructs a
session name by hand:

| Function | Returns |
|---|---|
| `resolve_session_name(profile, slot)` | The tmux session name for one slot. |
| `resolve_window_title(profile, profile_name, slot, session)` | The window title: slot override, then profile template, then default. |
| `profile_slots(profile)` | `(slot, session)` pairs in config order. |
| `slot_for_session(profile, session)` | The slot a session occupies, or `None`. |

## Extending the tool

Where a change goes, the recipes for adding a command or a config key, and
what each gate enforces are in [contributing.md](contributing.md). The parts that are
design decisions rather than procedure stay here:

- **`COMMANDS` is the single source of truth for dispatch.** Each entry is
  `(argparse dest, handler, mutates_config, needs_profile)`. `mutates_config` decides
  whether the run holds the config lock across load, modify and save; `needs_profile`
  decides whether an unresolvable profile is fatal, which is why listing and validating
  work when no profile can be resolved.
- **A handler returns an exit code and never calls `sys.exit`**, which is what makes every
  command directly testable.
- **Anything that edits a profile does it inside `edit_profile`**, so a command cannot
  write a config that no later command can read.

## Project structure

```
src/xtm.py             The tool. Single file; PyYAML is its one import outside the stdlib.
completions/xtm.bash   Bash completion for actions, profiles and session names.
install.sh             Installer: copies src/xtm.py to a PATH directory, optional completion.
pyproject.toml         Packaging, for `pip install -e .`, and the coverage settings.
                       install.sh stays the primary path.
Makefile               Delegates every target to tests/run-tests.sh.
scripts/profile_examples.py  Profiles the examples; run with any that executes them.
scripts/record_metrics.py    Records what a coverage run measured, for the docs test.
.gitignore             .build/, plus the names the same tools use when run by hand.
.github/workflows/     CI: the suite across Python versions, plus lint and the examples.
.build/                Every generated file: coverage, bytecode, profiles, mutants.

README.md              Index: what xtm is, how to install it, where everything is.
docs/                  This document set; see the README index.

tests/
  run-tests.sh         The one way to run anything here.
  support.py           Fake external tools and the shared base classes.
  test_documentation.py  The docs must describe the tool that ships with them.
  unit/                Pure logic: YAML, validation, geometry, config, CLI.
  integration/         xtm run as a subprocess against the fakes.
  property/            Stated invariants over generated input, plus the engine.
  mutation/            Decisions broken on purpose; the suite must notice.
  concurrency/         Real processes contending for the config and the lock.
  fault_injection/     Failure paths a healthy filesystem never reaches.
  load/                Cost budgets.
  safety/              --dry-run invariants.
  examples/            The shipped examples must still describe the tool.

examples/
  run.py               Runs each example, and --negative the ones meant to fail.
  README.md            Index of the examples, by level.
  config.yaml.example  Annotated example configuration, every key in context.
  simple/NN-name/      one idea: config.yaml, README.md, expected.json.
  moderate/NN-name/    several features interacting.
  complex/NN-name/     every mechanism at once.
  negative/NN-name/    config.yaml, README.md, expected-error.txt.
```

## Assumptions and limitations

- **Linux.** Telling xtm's tmux clients from anyone else's reads `/proc/<pid>/cmdline` and
  `/proc/<pid>/stat`. Without `/proc`, every attached client is taken to be xtm's.
- **X11.** Placement is an X window operation. Under a Wayland desktop xterm runs through
  Xwayland, and placement, capture and `--verify` work there too (verified on KDE Plasma 6
  with Xwayland at scale 1); native Wayland terminals are not supported.
- **xterm specifically.** Placement relies on xterm's `allowWindowOps` escape sequences.
- **Screen checks, not layout.** Each window is checked against the monitors `xrandr`
  reports, and one that would land off screen is reported or, with `--clamp`, moved onto a
  monitor. Profiles are not chosen by the monitors connected: `xtm NAME` switches.
- **Absolute pixels only.** Positions do not scale between resolutions, which is why
  profiles are per machine.
- **`command` runs only at creation**, as tmux itself does. It replaces the pane's shell, so
  the pane closes when the command exits.
- **`command` is executed by a shell**, so it supports pipes and `&&`, and should be treated
  with the same care as any line in a shell profile.
- **Windows opened by hand are invisible to xtm**, since they carry no `xtm-` instance name.
- **xtm manages sessions, not their contents.** Tabs, panes and layouts inside a session are
  tmux's business.
