# Changelog

Versioning scheme: `0.<N>`, with `N` incrementing by one per release
(`0.9` is followed by `0.10`, never by `1.0`).

## 0.3

Production release. Correctness fixes, new commands, and a full test suite.

### Fixed

- **Python 3.6 compatibility was broken.** Subprocess calls used
  `capture_output=` and `text=`, both introduced in Python 3.7, so the tool
  raised `TypeError` on the first external command under the Python 3.6
  interpreter shipped by long-lived enterprise distributions — despite
  documenting 3.6 as supported. Calls now use `stdout=PIPE, stderr=PIPE,
  universal_newlines=True`. `typing.NoReturn`, which requires 3.6.2, is no
  longer used either.
- **Saving the config failed with older PyYAML.** `safe_dump(sort_keys=...)`
  requires PyYAML 5.1; earlier releases raise `TypeError`. Every config write
  therefore failed on systems that had an older PyYAML installed. Dumping now
  falls back to the built-in writer in that case, preserving key order rather
  than rewriting the file alphabetically on every save.
- **The documented configuration format did not parse without PyYAML.** The
  built-in reader stripped only whole-line comments and understood only the
  empty flow mapping `{}`. An annotated line such as `offset_x: 40  # offset`
  loaded as a string, and the compact form `stack: {x: 20, y: 20}` shown in
  the documentation loaded as a string as well. Both now parse: comments are
  recognised anywhere outside quotes when preceded by whitespace, and
  flow-style mappings and lists are fully supported.
- **Captured window positions drifted on every save/restore cycle.** Window
  managers apply a requested position to the frame they draw, while the
  geometry tools report the position of the client window inside it, so each
  `--update-profile` followed by `--reset` shifted every window down and
  right by the thickness of its decorations. Captured positions are now
  corrected using `_NET_FRAME_EXTENTS` read with `xprop`, making the round
  trip stable. `--no-frame-compensation` disables the correction.
- **Window identification relied on the title alone**, which `set-titles on`
  in `.tmux.conf`, and many shell prompts, rewrite at runtime — silently
  breaking window discovery. Windows now also carry the instance name
  `xtm-<session>` in `WM_CLASS`, which a running program cannot change, and
  are matched on that first.
- **Non-finite geometry values escaped validation.** `inf` and `nan` parse as
  floats, and `x: nan` passed the numeric check before failing with an
  unhandled `ValueError` deep inside placement. Geometry fields are now
  required to be finite.
- **The current-profile state file was shared across machines.** With a
  home directory on NFS, two machines overwrote each other's current
  profile, defeating the purpose of per-machine profiles. The state file name
  now includes the short hostname, and the pre-0.3 file is still read as a
  fallback so existing setups keep working.
- **Concurrent config writes could lose an update.** Two simultaneous
  `--update-profile` runs each loaded the same starting config, and the
  second write discarded the first one's changes. Commands that modify the
  config now hold an exclusive `flock` on a sibling lock file for the whole
  read-modify-write cycle.
- **A crash during a config write could leave an empty file.** The atomic
  rename was not preceded by `fsync`. Writes are now flushed and synced
  before the rename, and the parent directory is synced after it.
- **`place_window()` resolved the client tty twice**, once for the resize and
  once for the move, meaning two `tmux list-clients` calls that could
  disagree if a client attached or detached between them. It is now resolved
  once and reused.
- **An xterm that died immediately produced a misleading message** after the
  full six-second attach timeout. The spawned process is now polled during
  the wait, and its actual exit status is reported at once.
- **Profile and session names were validated on input but not on load**, so a
  hand-edited name containing `:` round-tripped into a file that could no
  longer be parsed. Names are now validated when the config is read.

### Added

- Short options for every action and every commonly used flag; the
  single-dash long forms used before 0.3 remain accepted.
- Console logging is on by default at INFO on stderr, with `--quiet`,
  `--debug` and an explicit `--log-level`. Data output moved to stdout so it
  stays pipeable at any log level. File logging remains off by default and
  captures full DEBUG detail when enabled with `--log-file`.
- Running with no arguments prints the status of the current profile, falling
  back to the `default` profile on a fresh installation.
- Per-session `command`, `cwd` and `xterm_args` settings, so a session can
  open directly into a remote host, a log tail or a working directory, with
  its own font and colours.
- Profile auto-selection through an optional `match` block matching hostname
  and `$DISPLAY` globs, with `--auto` to ignore the saved profile and
  re-derive the choice.
- `--close` and `--close-all` to tear sessions down; `--close-all` is scoped
  to the profile and asks for confirmation.
- `--focus` to raise a session's window by name.
- `--set`, `--delete-session`, `--delete-profile`, `--copy-profile`,
  `--rename-profile`, `--edit` and `--validate`, so profiles can be managed
  without hand-editing YAML.
- `--json` output from `--list`, `--list-profiles`, `--current-profile` and
  `--validate`.
- `--dry-run`, which turns every write, launch, placement and kill into a
  logged intention.
- `--verify`, which re-reads a window's geometry after placement and reports
  the difference between what was requested and what the window manager did.
- `--config` and `--state-dir`, plus the `XTM_STATE_DIR` environment
  variable, complementing the existing `XTM_CONFIG_DIR`.
- Documented exit codes: 0 success, 1 runtime error, 2 usage error, 130
  interrupted.
- `test_xtm.py`: 341 tests covering 95.4% of statements and 92.8% of
  branches, using fake external tools and a real pty pair to assert the exact
  placement escape sequences, with dedicated groups for CLI option
  combinations and ordering, malformed configuration input, documented
  processing order, per-tool enumeration cost, config locking, and the
  dependency-free YAML path end to end.
- `install.sh`, `xtm-completion.bash` and `config.yaml.example`.

### Changed

- `--reset` and `--reset-all` collect per-session failures, report each one,
  and continue rather than stopping at the first problem.
- Window enumeration under `xdotool` now runs two searches, by instance name
  and by title, so that windows opened by earlier versions are still found.
  Identifying each window additionally costs a `getwindowname` call and an
  `xprop` call, because `xdotool` cannot print a window's `WM_CLASS` itself;
  where `xprop` is unavailable the title alone is used and the second call is
  skipped. `wmctrl` and `xwininfo` both enumerate in a single call.
- The `need_enumeration` parameter, which no longer excluded any tool, was
  removed from geometry-tool selection.
- README restructured into user, advanced and developer parts, with a
  field-by-field breakdown of every view, an options deep dive, a worked
  example and a task-to-options recipe table.

## 0.2

- `xwininfo` accepted for reading window positions back, alongside `xdotool`
  and `wmctrl`, using a `-root -tree` scan for titles followed by a per-window
  `-name` lookup for geometry. The tree dump's own coordinates are ambiguous
  under reparenting window managers and are deliberately not used.
- At open time, the tmux client running inside the xterm just spawned is
  identified by process ancestry, so placement acts on the right client when
  more than one is attached.
- `-debug` for verbose stderr diagnostics and `-log-file` for persistent
  DEBUG-level logs.
- Atomic config writes.
- `-profile` is honoured alongside `-currentprofile` and `-listprofiles`
  instead of being silently ignored.

## 0.1

Initial version: `-open`, `-reset`, `-resetall`, `-updateprofile`,
`-newprofile`, `-list`, `-listprofiles`, `-currentprofile`, `-profile`, with
window placement by xterm escape sequences and a YAML profile file.
