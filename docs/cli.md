# CLI reference

Every action, every option, every field of every view, and the exit status.

Every action has a short and a long form. At most one action may be given; with none, xtm
prints the status of the effective profile.

## Actions

| Short | Long | Argument | Description |
|---|---|---|---|
| `-l` | `--list` | — | Show session and window status for the profile. **Default action.** |
| `-o` | `--open` | `SESSION` | Open one tmux session in a new xterm and place it. |
| `-r` | `--reset` | — | Reposition, or open if missing, every named session in the profile. |
| `-R` | `--reset-all` | — | As `--reset`, and also stack any stray sessions. |
| `-k` | `--close` | `SLOT` or `SESSION` | Kill one session, closing its window and destroying its tabs and panes. |
| `-t` | `--detach` | `SLOT` or `SESSION` | Close a session's window but leave the session, its tabs and its panes running. |
| `-K` | `--close-all` | — | Kill every running session named in the profile. Asks for confirmation. |
| `-f` | `--focus` | `SLOT` or `SESSION` | Raise and focus one session's window. |
| `-u` | `--update-profile` | — | Save current window positions into the profile. |
| `-n` | `--new-profile` | `NAME` | Create a new profile from the current layout. |
| `-s` | `--set` | `SESSION GEOMETRY` | Set a session's position. `GEOMETRY` is `x,y,width,height`. |
| `-D` | `--delete-session` | `SESSION` | Remove a session's entry from the profile. Does not kill it. |
| `-P` | `--list-profiles` | — | List every profile in the config. |
| `-C` | `--current-profile` | — | Print this machine's current profile. |
| `-V` | `--validate` | — | Check every profile in the config file. |
| `-e` | `--edit` | — | Open the config in `$VISUAL`/`$EDITOR`, then validate it. |
| — | `--delete-profile` | `NAME` | Delete a profile from the config. |
| — | `--copy-profile` | `SOURCE TARGET` | Copy an existing profile to a new name. |
| — | `--rename-profile` | `OLD NEW` | Rename an existing profile. |
| — | `--make-global` | `NAME` | Remove a profile's machine binding so it is usable everywhere. One-way. |

## General options

| Short | Long | Argument | Description |
|---|---|---|---|
| — | *(positional)* | `PROFILE` | Switch to a profile: `xtm desk`. Equivalent to `--profile`. |
| `-p` | `--profile` | `NAME` | Use profile `NAME` and persist it as this machine's current profile. |
| `-a` | `--auto` | — | Ignore the saved current profile; select one by matching the hostname. |
| `-A` | `--all` | — | Widen the scope: include profiles belonging to other machines when selecting or listing them, and with `--close-all` cover every profile visible on this machine rather than only the current one. Never reaches a profile bound to another host. |
| `-v` | `--verbose` | — | With `--list-profiles`, also show each profile's slots, session names and running state. |
| — | `--on-switch` | `MODE` | Override the previous profile's `on_switch` for this run: `leave`, `detach` or `kill`. |
| — | `--capture-new` | — | With `--update-profile`, also add open windows that are not part of the current profile. |
| — | `--detach-mode` | — | With `--close-all`, detach the windows instead of killing the sessions. |
| `-c` | `--config` | `PATH` | Path to the config file. |
| — | `--state-dir` | `PATH` | Directory holding the current-profile state file. |
| `-j` | `--json` | — | Emit JSON from `--list`, `--list-profiles`, `--current-profile` and `--validate`. |
| `-N` | `--dry-run` | — | Show what would happen without launching, moving, closing or saving anything. |
| `-y` | `--yes` | — | Answer yes to confirmation prompts. |
| — | `--verify` | — | After placing a window, read its position back and report any difference. |
| — | `--no-frame-compensation` | — | Do not correct captured positions for window decorations. |
| `-h` | `--help` | — | Show usage and exit. |
| — | `--version` | — | Show the version and exit. |

## Logging options

| Short | Long | Argument | Description |
|---|---|---|---|
| `-d` | `--debug` | — | Verbose diagnostics on stderr: every subprocess call, parsing decision and file operation. |
| `-q` | `--quiet` | — | Log only warnings and errors. |
| — | `--log-level` | `LEVEL` | Set the stderr level explicitly. Overrides `--debug` and `--quiet`. |
| `-L` | `--log-file` | `PATH` | Also write full DEBUG-level logs to `PATH`. Off by default. |

Console logging is **on by default at INFO**, written to stderr. Data output (`--list`,
`--json`, profile names) goes to stdout, so it stays pipeable no matter how verbose the log
stream is:

```bash
xtm --list --json --quiet | jq -r '.sessions[].name'
```

The long forms used before version 0.3 — `-open`, `-reset`, `-resetall`, `-updateprofile`,
`-newprofile`, `-list`, `-listprofiles`, `-currentprofile`, `-profile`, `-debug`,
`-log-file`, `-version` — are still accepted, so existing scripts and shell aliases keep
working.

## What each option changes

**What is read.** `--config` and `--state-dir` change which files are consulted, before
anything else happens. `--profile` selects a profile *and writes* it to the state file as a
side effect; `--auto` suppresses the state file for this run so the `match` blocks decide
instead. `--all` widens every command to include profiles bound to other machines.

**What is written.** `--dry-run` intercepts every write: config saves, state-file writes,
window placement, xterm launches and session kills all become log lines beginning
`[dry-run]`, and nothing is modified. `--yes` bypasses the confirmation prompt for
`--close-all`. `--no-frame-compensation` changes the numbers stored by `--update-profile`
and `--new-profile`.

**What is reported.** `--json` changes the format of the four reporting commands and nothing
else. `--verify` adds a read-back-and-compare step after each placement. `--debug`,
`--quiet`, `--log-level` and `--log-file` affect only the log stream, never stdout.

## Fixed execution order

Every invocation runs the same pipeline:

1. **Parse arguments.** A usage error exits 2 before anything is read. A positional profile
   name is folded into `--profile` here, and supplying both spellings is rejected.
2. **Configure logging.** `--log-level`, then `--debug`, then `--quiet`, otherwise INFO. A
   log file is always opened at DEBUG.
3. **Resolve paths.** `--config` beats `XTM_CONFIG_DIR` beats the default; `--state-dir`
   beats `XTM_STATE_DIR` beats the default. The state file name gains this machine's short
   hostname.
4. **Set run modes.** `--dry-run`, `--verify` and `--no-frame-compensation` take effect.
5. **Take the config lock**, for actions that write to the config file.
6. **Load and sanity-check the config**, creating one if it does not exist and adding the
   reserved `default` profile if the file lacks it. That addition is saved immediately.
7. **Resolve the profile**, in the order given in [concepts.md](concepts.md).
8. **Validate that profile** in full, injecting default `stack` and `sessions` if absent.
9. **Run the action.**
10. **Save the config**, for actions that changed it.

Within `--reset` and `--reset-all`, named sessions are processed in config file order, then
strays are stacked alphabetically.

## Per-option edge cases

| Option | Edge-case behaviour |
|---|---|
| `--profile` | A name that is not in the config is an error, and the state file is left unchanged. |
| `--auto` | With no matching `match` block, falls through to the reserved `default` profile, which always exists. |
| `--all` | Affects visibility only. It never changes what a command does to a profile it can already see. |
| `--detach` | Accepts a slot name or a session name. A session with no window attached reports so and exits 0; a session that is not running exits 1. |
| `--capture-new` | Only meaningful with `--update-profile`. Captured windows are recorded with an explicit `session:`, because those sessions already exist under the names they were captured with and must not be renamed by the profile's prefix. |
| `--on-switch` | Applies to the profile being left, not the one being entered. Has no effect when the profile is not actually changing. |
| `--detach-mode` | Only meaningful with `--close-all`. |
| `--verbose` | Only meaningful with `--list-profiles`. |
| `--make-global` | A profile that is already global reports so and exits 0. One-way: there is no command to re-bind a global profile to a machine. |
| *(positional)* | Giving both a positional profile and `--profile` is a usage error even when they name the same profile. An unknown name that matches a session suggests `--open`/`--focus` instead. |
| `--open` | An already-attached session is an error, not a silent no-op. An unconfigured session is placed in the next stack slot, counted from the number of currently attached strays. |
| `--reset` | A profile with no named sessions logs a notice and exits 0. Individual failures are collected, reported per session, and the command exits 1 at the end; other sessions are still processed. |
| `--reset-all` | Runs `--reset` first, then stacks strays. A failure in either phase exits 1, but neither phase aborts the other. |
| `--close` | A session that is not running is an error, since silently succeeding would hide a typo. |
| `--close-all` | Scoped to the current profile's sessions unless `--all` widens it; sessions belonging to no profile are never touched. With nothing running, logs a notice and exits 0. When stdin is not a terminal, the confirmation prompt is skipped, because a scripted caller has already opted in and cannot answer. |
| `--focus` | Accepts a slot name or a session name. Requires `xdotool` or `wmctrl`; with only `xwininfo` present, fails with an explanation before looking for the window. A session with no window exits 1. |
| `--update-profile` | Captures only windows whose session belongs to the current profile; others are counted and reported, and added only with `--capture-new`. With no xtm windows open, logs a notice and changes nothing. Windows whose geometry cannot be read are skipped with a warning. Sessions with no window on screen keep their recorded position. |
| `--new-profile` | An existing name is an error. The new profile gets `prefix: <name>-` written explicitly, and every captured window is recorded with an explicit `session:`. Stack settings are inherited from the current profile. Capturing an empty screen creates an empty profile and says so. |
| `--set` | Creates the session entry if it does not exist, and preserves `command`, `cwd` and `xterm_args` if it does. Rejects non-integer, non-finite and non-positive sizes. |
| `--delete-session` | A name that is not in the profile is an error. A running session is not killed; only its recorded position is removed. |
| `--delete-profile` | Deleting the current profile also clears the state file, so the next run falls back to matching or `default`. Deleting the last remaining profile is allowed. |
| `--copy-profile` | The target must not already exist. The copy is deep, so later edits to one do not affect the other. |
| `--rename-profile` | Updates the state file if the renamed profile was current. |
| `--edit` | `$VISUAL` is checked before `$EDITOR`, and either may carry arguments (`vim -p`); the config path is appended last. A config left invalid after editing is reported and exits 1; the file is not reverted, so the edit is never lost. |
| `--dry-run` | Confirmation prompts are auto-accepted, since nothing will actually happen. |
| `--verify` | Without a geometry tool, warns once and skips verification rather than failing the placement. |
| `--json` | Applies only to `--list`, `--list-profiles`, `--current-profile` and `--validate`. With no profile saved, `--current-profile --json` reports `current_profile: null` alongside the `resolved_profile` that would be used. |

Any command that edits a profile re-runs validation before saving and leaves the profile
untouched if the edit would not survive it, so no command writes a config a later command
cannot read.

## Choosing between similar options

| Compare | Difference |
|---|---|
| `--reset` vs `--reset-all` | `--reset` touches only sessions named in the profile. `--reset-all` also gathers every attached session that is *not* named, and stacks them diagonally. |
| `--open` vs `--reset` | `--open` handles exactly one session and refuses if it is already attached. `--reset` is idempotent. |
| `--update-profile` vs `--new-profile` | Both capture the screen. The first overwrites the current profile in place; the second creates a new one and fails if the name exists. |
| `--set` vs `--update-profile` | `--set` writes one exact position from the command line and needs no geometry tool. `--update-profile` captures every window at once and does. |
| `--close` vs `--detach` | `--close` kills the session, destroying its tabs, panes and running jobs. `--detach` closes only the window and leaves everything running. |
| `prefix:` vs `session:` | `prefix:` makes a slot's session belong to the profile. An explicit `session:` opts one slot out of that, which is how two profiles share one. |
| `--close` vs `--delete-session` | `--close` kills a running session and its window, leaving the profile untouched. `--delete-session` removes the profile entry and leaves the session alone. |
| `--dry-run` vs `--verify` | `--dry-run` runs before the fact and changes nothing. `--verify` runs after the fact and checks that what was requested happened. |
| `--profile` vs `--auto` | `--profile` names a profile and remembers it. `--auto` forgets the remembered one for this run and re-derives the choice from the hostname. |
| `--quiet` vs `--json` | `--quiet` silences the log stream on stderr. `--json` changes the data format on stdout. |

## The status view (`--list`)

What xtm prints with no arguments.

```
Profile: desk
Config:  /home/user/.config/xtm/config.yaml
Prefix:  desk-

Slots:
  work1                attached     configured: x=0 y=0 960x1180  [live: x=0 y=0 960x1180]
                                    session:    desk-work1
  logs                 attached     configured: x=960 y=0 960x580  [live: x=964 y=2 960x580]
                                    session:    desk-logs
                                    command:    journalctl -f
  notes                attached     configured: x=0 y=1180 960x400
                                    session:    notes  (shared, kept on profile switch)
  cluster              not running  configured: x=960 y=600 960x580
                                    session:    desk-cluster
                                    command:    ssh headnode
                                    cwd:        ~/projects

Stray attached sessions (no configured position):
  lab-build  (belongs to profile lab)
  scratch
```

| Field | Meaning |
|---|---|
| `Profile:` | The profile in effect for this invocation. |
| `Config:` | Absolute path of the config file actually being used. |
| `Prefix:` | The profile's session-name prefix. Omitted when the profile has none. |
| Slot name | The key from the profile, left-aligned in a 20-column field. |
| Status | `attached` — running with a window; `detached` — the tmux session exists but nothing is attached; `not running` — no such tmux session. |
| `configured:` | The position recorded in the profile, as `x=<x> y=<y> <width>x<height>` in pixels. |
| `[live: …]` | The position read off the screen right now, shown only when the session is attached and a geometry tool is available. A difference from `configured` means the window has been moved or resized since the profile was saved. |
| `session:` | The tmux session name this slot resolves to. Shown when it differs from the slot name, or when it is shared. `(shared, kept on profile switch)` marks a slot with an explicit `session:`. |
| `command:` | The slot's configured launch command, shown only when one is set. |
| `cwd:` | The slot's configured working directory, shown only when one is set. |
| Stray sessions | Attached sessions matching no slot in this profile. Those belonging to another profile are labelled with its name. |

A trailing note appears when no geometry tool is installed, explaining that live positions
cannot be shown.

## The status view as JSON (`--list --json`)

```json
{
  "config": "/home/user/.config/xtm/config.yaml",
  "frame_compensation": true,
  "geometry_tool": "xdotool",
  "on_switch": "detach",
  "prefix": "desk-",
  "profile": "desk",
  "sessions": [
    {
      "command": "journalctl -f",
      "configured": {"height": 580, "width": 960, "x": 960, "y": 0},
      "cwd": null,
      "live": {"height": 580, "width": 960, "x": 964, "y": 2},
      "name": "logs",
      "session": "desk-logs",
      "shared": false,
      "status": "attached",
      "title": "xtm:desk-logs"
    }
  ],
  "state_file": "/home/user/.local/state/xtm/current_profile.workstation",
  "stray_owners": {"lab-build": "lab"},
  "strays": ["lab-build", "scratch"]
}
```

| Key | Type | Meaning |
|---|---|---|
| `config` | string | Path of the config file in use. |
| `frame_compensation` | boolean | Whether decoration compensation is enabled for this run. |
| `geometry_tool` | string or null | The geometry tool selected, or `null` if none was found. |
| `on_switch` | string | The profile's disposal mode: `leave`, `detach` or `kill`. |
| `prefix` | string | The profile's session-name prefix, or `""` when it has none. |
| `profile` | string | Profile in effect. |
| `sessions` | array | One object per slot in the profile, in config order. |
| `sessions[].name` | string | Slot name — the key from the profile. |
| `sessions[].session` | string | The tmux session name the slot resolves to. |
| `sessions[].shared` | boolean | Whether the slot names its session explicitly, and so is kept across a profile switch. |
| `sessions[].title` | string | The window title the slot resolves to. |
| `sessions[].status` | string | `attached`, `detached` or `not running`. |
| `sessions[].configured` | object | Recorded `x`, `y`, `width`, `height`. |
| `sessions[].live` | object or null | Current on-screen geometry, or `null` if unavailable. |
| `sessions[].command` | string or null | Configured launch command. |
| `sessions[].cwd` | string or null | Configured working directory. |
| `state_file` | string | Path of this machine's current-profile state file. |
| `strays` | array | Names of attached sessions with no configured position. |
| `stray_owners` | object | Maps a stray session to the profile that claims it. Empty when none do. |

`sessions[].name` is the **slot** and `sessions[].session` is what tmux calls it; they
differ whenever the profile sets a `prefix` or the slot sets a `session`.

## The profile list (`--list-profiles`)

```
desk *
laptop
```

The `*` marks the **effective** profile: the one this invocation would act on.

With `--json`, the top-level object carries `current` (the saved profile, or `null`),
`effective` (the profile in force), `hidden` (how many profiles belonging to other machines
were left out; `--all` brings it to zero) and `profiles`, an array in which each entry has:

| Key | Type | Meaning |
|---|---|---|
| `name` | string | Profile name. |
| `current` | boolean | Whether this is the saved current profile. |
| `effective` | boolean | Whether this is the profile in force for this run. |
| `sessions` | number | How many sessions the profile defines. |
| `match` | object or null | The machine-binding block, if the profile has one. |
| `global` | boolean | Whether the profile is available on every machine. |

## The cross-profile view (`--list-profiles --verbose`)

Answers "what is running, and which profile owns it" in one command:

```
Config: /home/user/.config/xtm/config.yaml

default  (on_switch detach)
  (no slots configured)

desk *  (prefix desk-, on_switch detach)
  work1              desk-work1     attached
  notes              notes          attached      shared

lab  (prefix lab-, on_switch leave)
  work1              lab-work1      detached
  notes              notes          attached      shared

Sessions claimed by no profile:
  scratch
```

| Field | Meaning |
|---|---|
| Profile line | The profile name, `*` if it is the one in effect, then its `prefix` (omitted when it has none) and its `on_switch` mode. |
| Slot name | The key from the profile. |
| Session name | What the slot resolves to. |
| Status | `attached`, `detached` or `not running`. |
| `shared` | The slot has an explicit `session:`, so it is not owned by the profile. |
| Sessions claimed by no profile | Running sessions that match no slot in any visible profile. |

`--all` extends this to profiles bound to other machines. With `--json`, the same data is
emitted as `{"profiles": [...], "unclaimed": [...], "config": ...}`, each profile carrying
`name`, `current`, `prefix`, `on_switch` and `slots`.

## The validation report (`--validate`)

```
Config: /home/user/.config/xtm/config.yaml
  desk                 OK
  laptop               OK
```

Every profile is checked, not just the active one, and visibility is ignored. A profile
that fails is reported with the reason on the same line, and the command exits 1.

## Exit status

| Code | Meaning |
|---|---|
| `0` | Success. |
| `1` | A runtime error: bad config, missing tool, a window that could not be placed, a session that could not be closed. |
| `2` | Usage error from argument parsing, such as two actions at once. |
| `130` | Interrupted with Ctrl-C. |

## Environment variables

| Variable | Effect |
|---|---|
| `XTM_CONFIG_DIR` | Directory holding `config.yaml`. Overridden by `--config`. |
| `XTM_STATE_DIR` | Directory holding the current-profile state file. Overridden by `--state-dir`. |
| `DISPLAY` | Required to open windows. Not used for profile auto-selection, which matches on the hostname alone. |
| `VISUAL` | Editor used by `--edit`, checked before `EDITOR`. May carry arguments (`vim -p`); the config path is appended as the last one. |
| `EDITOR` | Editor used by `--edit` when `VISUAL` is unset. With neither set, `--edit` falls back to `vi`. |
