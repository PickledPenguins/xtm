# xtm — xterm + tmux workspace manager

**Version 0.5**

`xtm` manages a set of `xterm` windows, each attached to a named `tmux`
session, and positions them on screen according to a named *profile* stored
in a small YAML file. One command restores an entire desktop layout; another
saves the current layout back into the profile.

Profiles are per machine and per monitor arrangement, so the same
configuration file can describe a laptop, a two-monitor desk and a remote
workstation, and `xtm` can pick the right one automatically.

This README is the user guide. Behavioural detail lives in
[docs/reference.md](docs/reference.md), and notes for working on xtm itself in
[docs/development.md](docs/development.md). Six worked configurations, each
with its own README, are in [examples/](examples/).

---


## Vocabulary

Three words are used precisely throughout this document, the tool's messages
and its source, because "window" otherwise means two different things:

| Term | Meaning |
|---|---|
| **slot** | A position on screen, and the key of an entry under `sessions:` in a profile. |
| **session** | The tmux session attached to a slot. Its name comes from the profile's `prefix` plus the slot name, or from an explicit `session:` on the slot. |
| **tab** and **pane** | tmux windows and splits *inside* a session. `xtm` never touches these. |

A slot is where a terminal sits; a session is what is running in it.

## Scope

`xtm` does three things:

1. **Opens** terminals: one `xterm` per named `tmux` session, optionally
   running a chosen command in a chosen directory.
2. **Places** them: moves and resizes each window to the position recorded
   for it in the active profile.
3. **Records** layouts: reads the current on-screen positions back and saves
   them into a profile.

It is deliberately *not* a tmux session manager: it does not define windows,
panes or splits inside a session. It manages the desktop-level arrangement of
terminals, and leaves what happens inside each terminal to tmux.

## Requirements

Everything runs on the machine displaying the windows, using tools that are
already present wherever `xterm` and `tmux` are used:

| Component | Needed for | Required? |
|---|---|---|
| `python3` 3.6 or newer | everything | Yes |
| `xterm` | opening windows | Yes |
| `tmux` | sessions, and locating each window's tty | Yes |
| `xdotool`, `wmctrl` or `xwininfo` | reading window positions back | Only for `--update-profile`, `--new-profile`, `--focus`, `--verify`, and the live column of `--list` |
| `xdotool` or `wmctrl` | `--focus` specifically | `xwininfo` can query windows but cannot raise them |
| `xprop` | correcting captured positions for window decorations | Optional; skipped with a debug message when absent |
| `PyYAML` | nothing | Optional; used if importable, otherwise a built-in reader/writer is used |

No third-party Python packages are required, and nothing needs to be
installed system-wide.

## Installation

```bash
./install.sh                 # installs to ~/bin by default
./install.sh --prefix /usr/local/bin
./install.sh --completion    # also install the bash completion file
./install.sh --uninstall     # remove a previous installation
```

| Option | Argument | Effect |
|---|---|---|
| `--prefix` | `DIR` | Install into `DIR` (default `$HOME/bin`). |
| `--completion` | — | Also install the bash completion file. |
| `--completion-dir` | `DIR` | Completion target (default `$HOME/.local/share/bash-completion/completions`). Implies `--completion`. |
| `--uninstall` | — | Remove the installed program and completion file. Configuration and state are left in place. |
| `-h`, `--help` | — | Show usage. |

The installer verifies that `python3` is present and at least 3.6 before
copying anything, copies `xtm.py` to `<prefix>/xtm`, makes it executable, and
warns without failing if the target directory is not on `PATH`. It needs only
a POSIX shell and no network access. Manual installation is equivalent:

```bash
mkdir -p ~/bin
cp xtm.py ~/bin/xtm
chmod +x ~/bin/xtm
xtm --help
```

## Quick start

```bash
xtm                          # status of the current profile (default action)
xtm desk                     # switch to the "desk" profile and show its status
xtm desk --reset-all         # switch, then open/position everything
xtm --reset-all              # same, using whichever profile is current
```

Naming a profile is the most common thing xtm is asked to do, so it needs no
flag: `xtm desk` is exactly equivalent to `xtm --profile desk`, and the
selection is remembered until changed. Supplying both spellings at once is a
usage error rather than one silently winning.

On first run, a configuration is created at `~/.config/xtm/config.yaml`
containing a profile named `default`. That profile is **reserved**: it always
exists, is available on every machine, and is what xtm falls back to when
nothing else applies. Its contents are yours to edit freely; it simply cannot
be deleted or renamed, so the fallback can never silently move.

## Commands

Every action has a short and a long form. At most one action may be given;
with none, `xtm` prints the status of the current profile.

### Actions

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

### General options

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

### Logging options

| Short | Long | Argument | Description |
|---|---|---|---|
| `-d` | `--debug` | — | Verbose diagnostics on stderr: every subprocess call, parsing decision and file operation. |
| `-q` | `--quiet` | — | Log only warnings and errors. |
| — | `--log-level` | `LEVEL` | Set the stderr level explicitly. Overrides `--debug` and `--quiet`. |
| `-L` | `--log-file` | `PATH` | Also write full DEBUG-level logs to `PATH`. Off by default. |

Console logging is **on by default at INFO**, written to stderr. Data output
(`--list`, `--json`, profile names) goes to stdout, so it stays pipeable no
matter how verbose the log stream is:

```bash
xtm --list --json --quiet | jq -r '.sessions[].name'
```

The long forms used before version 0.3 — `-open`, `-reset`, `-resetall`,
`-updateprofile`, `-newprofile`, `-list`, `-listprofiles`, `-currentprofile`,
`-profile`, `-debug`, `-log-file`, `-version` — are still accepted, so
existing scripts and shell aliases keep working.

## Views

### The status view (`--list`)

This is what `xtm` prints with no arguments.

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

Field by field:

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
| Stray sessions | Attached sessions matching no slot in this profile. Those belonging to another profile are labelled with its name. `--reset` ignores strays; `--reset-all` stacks them. |

A trailing note appears when no geometry tool is installed, explaining that
live positions cannot be shown.

### The status view as JSON (`--list --json`)

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
| `sessions[].session` | string | The tmux session name the slot resolves to. Equal to `name` when the profile has no prefix and the slot sets no `session`. |
| `sessions[].shared` | boolean | Whether the slot names its session explicitly, and so is kept across a profile switch. |
| `sessions[].title` | string | The window title the slot resolves to. |
| `sessions[].status` | string | `attached`, `detached` or `not running`. |
| `sessions[].configured` | object | Recorded `x`, `y`, `width`, `height`. |
| `sessions[].live` | object or null | Current on-screen geometry, or `null` if unavailable. |
| `sessions[].command` | string or null | Configured launch command. |
| `sessions[].cwd` | string or null | Configured working directory. |
| `state_file` | string | Path of this machine's current-profile state file. |
| `strays` | array | Names of attached sessions with no configured position. |
| `stray_owners` | object | Maps a stray session to the profile that claims it, for those that belong to another profile. Empty when none do. |

Note that `sessions[].name` is the **slot** and `sessions[].session` is what
tmux calls it; they differ whenever the profile sets a `prefix` or the slot
sets a `session`.

### The profile list (`--list-profiles`)

```
desk *
laptop
```

The `*` marks the **effective** profile: the one this invocation would act
on. That is usually this machine's saved current profile, but when nothing
has been saved yet it is whichever profile was auto-selected or defaulted to,
so the mark always answers "which profile am I about to affect".

With `--json`, the top-level object carries `current` (the saved profile, or
`null`), `effective` (the profile in force) and `profiles`, an array in which
each entry has:

| Key | Type | Meaning |
|---|---|---|
| `name` | string | Profile name. |
| `current` | boolean | Whether this is the saved current profile. |
| `effective` | boolean | Whether this is the profile in force for this run. |
| `sessions` | number | How many sessions the profile defines. |
| `match` | object or null | The machine-binding block, if the profile has one. |
| `global` | boolean | Whether the profile is available on every machine, which is the same as having no `match` block. |

The top-level object also carries `hidden`, the number of profiles belonging
to other machines that were left out of the listing; `--all` includes them and
brings it to zero.

### The cross-profile view (`--list-profiles --verbose`)

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

`--all` extends this to profiles bound to other machines. With `--json`, the
same data is emitted as `{"profiles": [...], "unclaimed": [...], "config": ...}`,
each profile carrying `name`, `current`, `prefix`, `on_switch` and `slots`.

### The validation report (`--validate`)

```
Config: /home/user/.config/xtm/config.yaml
  desk                 OK
  laptop               OK
```

Every profile is checked, not just the active one. A profile that fails is
reported with the reason on the same line, and the command exits 1.

## Configuration

`~/.config/xtm/config.yaml`, created automatically on first run:

```yaml
profiles:
  desk:
    match:                 # optional: bind this profile to a machine
      hostname: workstation*
    prefix: desk-          # optional: session names for this profile's slots
    on_switch: detach      # optional: leave | detach | kill
    title: "{profile}: {slot}"   # optional: window title template
    stack:                 # default slot for sessions with no position
      x: 40
      y: 40
      width: 900
      height: 650
      offset_x: 40         # diagonal offset applied per stacked window
      offset_y: 40
    sessions:
      work1:
        x: 0
        y: 0
        width: 960
        height: 1180
      logs:
        x: 960
        y: 0
        width: 960
        height: 580
        command: journalctl -f
        title: Logs          # optional: overrides the profile template
      notes:
        session: notes       # explicit: never prefixed, so it can be shared
        x: 0
        y: 1180
        width: 960
        height: 400
      cluster:
        x: 960
        y: 600
        width: 960
        height: 580
        command: ssh headnode
        cwd: ~/projects
        xterm_args: [-bg, "#2a1a1a"]

  laptop:
    match: {hostname: [thinkpad, thinkpad-dock]}
    stack: {x: 20, y: 20, width: 700, height: 900, offset_x: 30, offset_y: 30}
    sessions:
      work1: {x: 0, y: 0, width: 1000, height: 1400}
```

Both the indented and the compact `{…}` form are accepted, with or without
PyYAML installed. Comments may appear on their own line or at the end of a
line.

### Session keys

| Key | Type | Required | Meaning |
|---|---|---|---|
| `x`, `y` | number | Yes | Window position in pixels. May be negative for monitors left of or above the origin. |
| `width`, `height` | number | Yes | Window size in pixels. Must be positive. |
| `command` | string | No | Shell command run in the session when it is **created**. Ignored when attaching to a session that already exists. |
| `cwd` | string | No | Working directory for a newly created session. `~` is expanded. |
| `xterm_args` | list of strings | No | Extra arguments appended to the `xterm` command line, for fonts, colours, scrollback and so on. |
| `session` | string | No | Tmux session name, used verbatim and **never prefixed**. This is how two profiles deliberately share one session. |
| `title` | string | No | Window title for this slot. Overrides the profile's template. |

### Profile keys

| Key | Type | Required | Meaning |
|---|---|---|---|
| `stack` | mapping | No | Placement for sessions with no recorded position. Needs `x`, `y`, `width`, `height`, `offset_x`, `offset_y`. Defaults are supplied if absent. |
| `sessions` | mapping | No | Session name to session settings. Defaults to empty. |
| `match` | mapping | No | Machine binding: a `hostname` glob, or a list of them. Present means machine-specific; absent means global. |
| `prefix` | string | No | Prepended to a slot name to form its session name. Absent means no prefix, so the session is named after the slot. |
| `on_switch` | string | No | What happens to this profile's windows when switching away: `leave`, `detach` or `kill`. Defaults to `detach`. |
| `title` | string | No | Window title template for every slot, using `{slot}`, `{session}` and `{profile}`. Defaults to `xtm:{session}`. |

Session and profile names may contain only letters, digits, `.`, `_` and `-`.

## Typical workflows

Sit down at a machine and restore everything:

```bash
xtm --profile desk --reset-all
```

Snap windows back after dragging them around:

```bash
xtm --reset            # named sessions only
xtm --reset-all        # named sessions, and re-stack strays
```

Save a new arrangement:

```bash
xtm --update-profile           # overwrite the current profile
xtm --new-profile desk-v2      # or capture it as a new profile
```

Adjust a single window without opening an editor:

```bash
xtm --set logs 960,0,960,580
xtm --reset
```

Jump to a terminal by name, and close one when done:

```bash
xtm --focus cluster
xtm --close cluster
```

Preview a change before making it:

```bash
xtm --dry-run --reset-all
```

## Exit status

| Code | Meaning |
|---|---|
| `0` | Success. |
| `1` | A runtime error: bad config, missing tool, a window that could not be placed, a session that could not be closed. |
| `2` | Usage error from argument parsing, such as two actions at once. |
| `130` | Interrupted with Ctrl-C. |

## The current profile and environment variables

A child process cannot set an environment variable in its parent shell, so a
live `$XTM_PROFILE` in an interactive shell is not something `xtm` can
provide. Instead `--profile NAME` persists the choice to a per-machine state
file, and `--current-profile` reads it back; this behaves like a persistent
environment variable across separate invocations.

For a real environment variable as well, wrap the command in `~/.bashrc`:

```bash
xtm() {
    command xtm "$@"
    export XTM_PROFILE="$(command xtm --current-profile --quiet 2>/dev/null)"
}
```

| Variable | Effect |
|---|---|
| `XTM_CONFIG_DIR` | Directory holding `config.yaml`. Overridden by `--config`. |
| `XTM_STATE_DIR` | Directory holding the current-profile state file. Overridden by `--state-dir`. |
| `DISPLAY` | Required to open windows. Not used for profile auto-selection, which matches on the hostname alone. |
| `VISUAL` | Editor used by `--edit`, checked before `EDITOR`. May carry arguments (`vim -p`); the config path is appended as the last one. |
| `EDITOR` | Editor used by `--edit` when `VISUAL` is unset. With neither set, `--edit` falls back to `vi`. |

---

---

## Further reading

| | |
|---|---|
| [docs/reference.md](docs/reference.md) | How placement works, window identification, frame compensation, every option in depth, profile auto-selection, YAML handling, limitations. |
| [docs/development.md](docs/development.md) | Architecture, design decisions, extending the tool, data structures, and how the test suite works. |
| [examples/](examples/) | Six worked configurations, each with a README. They run in CI, so they cannot drift from the tool. |
