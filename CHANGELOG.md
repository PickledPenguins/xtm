# Changelog

Versioning scheme: `0.<N>`, with `N` incrementing by one per release
(`0.9` is followed by `0.10`, never by `1.0`).

## 0.5

Slots separated from sessions, and profile switching given a defined
lifecycle.

### Added

- **A profile now owns its sessions by default.** A `prefix` on the profile is
  prepended to a slot name to form the tmux session name, so slot `work1` in a
  profile prefixed `desk-` is the session `desk-work1`, and the same slot name
  in another profile is a different session. Two profiles can no longer
  collide by accident. A profile with no `prefix` behaves exactly as before.
- **An explicit `session:` on a slot is used verbatim and never prefixed.**
  This is the supported way to share one session between profiles: both write
  the same name and land on the same session. The rule is that profile-owned
  sessions come and go with the profile, while explicitly named sessions
  persist across switches.
- **Configurable window titles.** `title:` on a slot, or a profile-level
  template using `{slot}`, `{session}` and `{profile}`, defaulting to
  `xtm:{session}`. Invalid templates are caught by `--validate` rather than at
  window-open time. The instance name (`WM_CLASS`) is unchanged and not
  configurable, so a custom title cannot break window discovery.
- **`on_switch: leave | detach | kill`**, defaulting to `detach`: switching
  away from a profile now disposes of its windows instead of leaving them
  mixed in with the new profile's. Sessions the new profile also claims are
  left attached and repositioned rather than closed and reopened.
  `--on-switch MODE` overrides it for one run.
- **`-t/--detach`**, closing a session's window while leaving the session, its
  tabs, its panes and its running jobs alive; and `--detach-mode`, which makes
  `--close-all` tidy rather than destroy.
- **`--close-all --all`**, covering every visible profile's sessions rather
  than only the current profile's, so sessions left behind by an earlier
  profile can be cleaned up without switching back to it.
- **`--list-profiles --verbose`**, reporting every profile with its slots,
  resolved session names and running state, plus the sessions claimed by no
  profile. Answers "what is running and who owns it" in one command.
- Strays in `--list` are now labelled with the profile they belong to.

### Changed

- **`--update-profile` no longer captures windows that are not part of the
  current profile.** Previously, switching profiles and then updating would
  silently graft the previous profile's sessions onto the new one. Foreign
  windows are now counted and reported, and added only with `--capture-new`.
- **`--new-profile` writes an explicit `prefix` and an explicit `session:` for
  every captured window**, because those sessions already exist under the
  names they were captured with and must not be renamed by the new prefix.
- `--close`, `--detach` and `--delete-session` accept either a slot name or
  the session name it resolves to.
- Two slots in one profile resolving to the same session is now a validation
  error; they would otherwise fight over the same window on every reset.
- `--list` reports the resolved session name whenever it differs from the slot
  name or is shared, and shows the profile's prefix.
- Messages and documentation now distinguish a **slot** (a position on screen)
  from a **session** (what is attached to it) throughout.

### Fixed

- The README claimed `xwininfo` could only find windows by title, and that
  `set-titles on` in `.tmux.conf` would therefore break `--update-profile` on
  a system where it is the only geometry tool. Neither was true: `xwininfo
  -root -tree` reports the instance name alongside the title, the code has
  always preferred it, and geometry is read with `-id` rather than by name.
- Documented that writing the config rewrites flow-style mappings as block
  style and drops comments. Both were already true and neither was mentioned.
- The test suite ran roughly four times slower than it needed to. The fake
  terminal inherited the harness's stdout and stderr pipes, so every test that
  opened a window waited for the fake terminal's whole lifetime instead of for
  xtm to exit. Runtime dropped from 210 seconds to about 50.

### Testing

- 420 tests at 95.0% line and 92.1% branch coverage, with new groups for slot
  naming, detaching and switching, and capture scope.

## 0.4

Profile selection made direct, and machine binding made explicit.

### Fixed

- The status view did not show a session's configured `cwd`, although the
  JSON view did and the documentation described it. The two views now agree.

### Added

- **The profile name is now a positional argument**: `xtm desk` is equivalent
  to `xtm --profile desk`. Switching profiles is the tool's most common
  operation, so it no longer needs a flag. `--profile` remains as an alias so
  existing scripts and shell functions keep working. Supplying both spellings
  in one command is a usage error, even when they name the same profile,
  rather than one silently winning.
- **A reserved `default` profile.** It is created with the config, recreated
  if it goes missing, and cannot be deleted, renamed, renamed onto or copied
  onto. It is always global and may not carry a `match` block. Its contents
  remain fully editable. Because resolution now ends at a profile guaranteed
  to exist under a fixed name, the fallback can never silently change when the
  config is edited or reordered.
- **`match.hostname` accepts a list of globs**, so one profile can serve
  several machines with the same layout instead of being duplicated.
- **Machine-specific profiles are hidden on other machines.** A profile
  carrying a `match` block no longer appears in `--list-profiles` and cannot
  be selected by name on a machine it is not bound to. `-A/--all` reveals them
  for listing and management, so a profile is never unreachable. `--validate`
  ignores visibility and always checks every profile.
- **`--make-global NAME`** removes a profile's machine binding. One-way by
  design: re-binding would hide a profile other machines may rely on, so it is
  left as a deliberate edit of the config file.
- A failed profile lookup now explains itself: naming a session by mistake
  suggests `--open`/`--focus`, and naming a profile bound to another machine
  says so and points at `--all`.

### Changed

- **Matching on `$DISPLAY` was removed.** A config still using `display:`
  fails validation with a message naming the removal and the version, rather
  than silently never matching. Machine binding covers what `match` is
  actually used for, and hostname is the only criterion it needs.
- An empty `match: {}` block is now a validation error, since it expressed
  neither "bound" nor "global" clearly. Remove the block to make a profile
  global.
- A **malformed** `match` block — unknown key, empty or non-string hostname —
  leaves the profile visible rather than hidden, so the validation error is
  what the user sees. Previously a typo such as `platform:` instead of
  `hostname:` would have made the profile silently disappear and produced a
  misleading "belongs to another machine" message.
- A saved current profile that has become hidden is now reported with an
  explanation instead of being used or silently skipped.
- Test suite expanded to 380 tests at 95.4% line and 93.0% branch coverage,
  with new groups for the positional profile, the reserved profile, machine
  visibility and machine binding.

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
