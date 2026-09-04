# Architecture

How xtm is built, where each behaviour lives, and what to know before changing it.

## Shape

One file, `xtm.py`, about 4,200 lines including its documentation. The layout is bottom-up:
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
| YAML | PyYAML with a complete dependency-free fallback, and the parity guard | `dump_yaml`, `load_yaml`, `_simple_yaml_load`, `_scan_for_ambiguous_scalars` |
| Config | Load, normalise, validate, save, guarded edits | `load_config`, `save_config`, `validate_profile`, `edit_profile` |
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

**Single file.** The tool has to be copied to servers where nothing can be installed; one
file with a shebang is the simplest thing that can be deployed by `scp`. `install.sh` copies
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
`KeyboardInterrupt` into exit 130; both `python3 xtm.py` and the console script installed by
`pyproject.toml` call it, which is what keeps a pip-installed xtm behaving like the copied
script.

**Edits are validated before they are saved.** `edit_profile` wraps any command that changes
a profile, re-runs the validation the next load would perform, and restores the profile if
the edit does not survive it. Without it a command could write a config that every later
command refused to load, leaving hand-editing as the only way back.

**stdout for data, stderr for logs**, so reporting commands stay pipeable under any log
level.

## The YAML parity guard

Two readers stand behind every config value, and the contract is that a config means the
same thing whichever one is present. The guard enforcing that runs **before** either backend
parses, in `_scan_for_ambiguous_scalars`, and refuses input the two would read differently.

Three things a change here has to respect:

- **Every rule exists because the two backends actually disagreed.** The list is in
  [configuration.md](configuration.md).
- **Several rules were written twice**, because the first attempt made the built-in parser
  *stricter* than PyYAML — which is the same defect pointing the other way. Check what
  PyYAML really does with an input before adding a rule for it.
- **A key is a name, not a value.** `_simple_yaml_key_load` interprets the quoting and
  nothing else; running the value rules over keys refused names PyYAML accepts.
  `_normalize_config_keys` then forces PyYAML's typed keys back to `str`, which is why a
  numeric key needs no rule and a boolean one does.

`_partition_key` splits on a colon only before a space or the end of a line, and honours a
quote only where a scalar may begin. Both were once "anywhere", and both produced silent
disagreements between the backends.

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

### Adding a command

`COMMANDS` near the bottom of the file is the single source of truth for dispatch. Each
entry is `(argparse dest, handler, mutates_config, needs_profile)`:

```python
COMMANDS = (
    ("open", cmd_open, False, True),
    ...
)
```

- **dest** must match the argparse destination of the option that triggers it, so
  `--make-global` pairs with `make_global`.
- **mutates_config** decides whether the run holds the config lock across load, modify and
  save. Set it for anything that calls `save_config`.
- **needs_profile** decides whether an unresolvable profile is fatal. Commands operating on
  the config as a whole (listing, validating, renaming) set it `False`.

A handler takes `(args, config, profile_name)` and returns an exit code; it must not call
`sys.exit`. Raise `XtmError` via `die()` for user-facing failures. `DEFAULT_ACTION` names the
entry used when no action flag is given.

Anything that edits a profile does it inside `edit_profile`.

### Adding a config key

1. Accept and validate it in `validate_session_entry` or `validate_profile`, with a message
   naming the profile, session and field.
2. Consume it where it applies — `build_xterm_command` for launch settings, `place_window`
   for placement.
3. Make sure it survives a round trip through **both** YAML backends.
4. Document it in [configuration.md](configuration.md) and add it to `config.yaml.example`.

### House rules

- **Single file, standard library only.**
- **Python 3.6 syntax and API only.** The module docstring lists what that rules out.
- **stdout is data, stderr is logs.** Anything a script might parse goes through `print`;
  everything else through `logger`.
- **Every external command goes through `run()`**, which supplies the timeout, the C locale,
  debug logging and clean errors for a missing program.
- **Comment intent, not mechanism.** Explain why a non-obvious choice was made; the code
  already says what it does.
- **Version bumps move the last component by one** (`0.9` → `0.10`, never `1.0`). The commit
  log is the release history; there is no changelog beside it to fall out of step with it.

## Project structure

```
xtm.py                 The tool. Single file, no imports outside the stdlib.
install.sh             Installer: copies to a PATH directory, optional completion.
xtm-completion.bash    Bash completion for actions, profiles and session names.
config.yaml.example    Annotated example configuration.
pyproject.toml         Packaging, for `pip install -e .`. install.sh stays the primary
                       path: xtm targets machines where pip is not an option.
Makefile               Delegates every target to scripts/run_tests.sh.
scripts/run_tests.sh   The one way to run anything here.
.coveragerc            Branch and parallel modes, artefact paths, the coverage floor.
.gitignore             .build/, plus the names the same tools use when run by hand.
.github/workflows/     CI: the suite across Python versions, crossed with PyYAML present
                       and absent, plus lint and the examples.

README.md              Index: what xtm is, how to install it, where everything is.
docs/                  This document set; see the README index.

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

## Assumptions and limitations

- **Linux.** Matching a tmux client to the xterm that spawned it reads `/proc/<pid>/stat`.
- **X11.** There is no Wayland support; the placement mechanism is an X window operation.
- **xterm specifically.** Placement relies on xterm's `allowWindowOps` escape sequences.
- **One client per session.** When several tmux clients are attached to one session, xtm can
  identify its own only for a window it opened in the same invocation. For `--reset` on a
  session opened by an earlier run, it acts on whichever client tmux lists first. A PID is
  deliberately not persisted across invocations, because a reused PID would be actively
  misleading.
- **No screen-size awareness.** Stack offsets can push a window off-screen; there is no
  wraparound.
- **Absolute pixels only.** Positions do not scale between resolutions, which is why
  profiles are per machine.
- **`command` runs only at creation**, as tmux itself does. It replaces the pane's shell, so
  the pane closes when the command exits.
- **`command` is executed by a shell**, so it supports pipes and `&&`, and should be treated
  with the same care as any line in a shell profile.
- **Windows opened by hand are invisible to xtm**, since they carry no `xtm-` instance name.
- **xtm manages sessions, not their contents.** Tabs, panes and layouts inside a session are
  tmux's business.
