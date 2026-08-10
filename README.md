# xtm — xterm + tmux workspace manager

**Version 0.5**

`xtm` manages a set of `xterm` windows, each attached to a named `tmux`
session, and positions them on screen according to a named *profile* stored
in a small YAML file. One command restores an entire desktop layout; another
saves the current layout back into the profile.

Profiles are per machine and per monitor arrangement, so the same
configuration file can describe a laptop, a two-monitor desk and a remote
workstation, and `xtm` can pick the right one automatically.

---

- [Part 1 — User guide](#part-1--user-guide)
- [Part 2 — Advanced reference](#part-2--advanced-reference)
- [Part 3 — Developer guide](#part-3--developer-guide)

---

# Part 1 — User guide

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
| `-f` | `--focus` | `SESSION` | Raise and focus one session's window. |
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
| `-A` | `--all` | — | Widen the scope: include profiles belonging to other machines, and cover every profile with `--close-all`. |
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
  "profile": "desk",
  "sessions": [
    {
      "command": "journalctl -f",
      "configured": {"height": 580, "width": 960, "x": 960, "y": 0},
      "cwd": null,
      "live": {"height": 580, "width": 960, "x": 964, "y": 2},
      "name": "logs",
      "status": "attached"
    }
  ],
  "state_file": "/home/user/.local/state/xtm/current_profile.workstation",
  "strays": ["scratch"]
}
```

| Key | Type | Meaning |
|---|---|---|
| `config` | string | Path of the config file in use. |
| `frame_compensation` | boolean | Whether decoration compensation is enabled for this run. |
| `geometry_tool` | string or null | The geometry tool selected, or `null` if none was found. |
| `profile` | string | Profile in effect. |
| `sessions` | array | One object per session named in the profile, in config order. |
| `sessions[].name` | string | Session name. |
| `sessions[].status` | string | `attached`, `detached` or `not running`. |
| `sessions[].configured` | object | Recorded `x`, `y`, `width`, `height`. |
| `sessions[].live` | object or null | Current on-screen geometry, or `null` if unavailable. |
| `sessions[].command` | string or null | Configured launch command. |
| `sessions[].cwd` | string or null | Configured working directory. |
| `state_file` | string | Path of this machine's current-profile state file. |
| `strays` | array | Names of attached sessions with no configured position. |

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
| `DISPLAY` | Required to open windows; also used for profile auto-selection. |
| `VISUAL` | Editor used by `--edit`, checked before `EDITOR`. |
| `EDITOR` | Editor used by `--edit` when `VISUAL` is unset. With neither set, `--edit` falls back to `vi`. |

---

# Part 2 — Advanced reference

## How placement works

Every xterm that `xtm` opens is started with `-xrm 'XTerm*allowWindowOps:
true'`, titled `xtm:<session>`, and given the window instance name
`xtm-<session>`. To move or resize a window, `xtm` asks tmux which pty the
session's client is on and writes xterm's own window-control escape
sequences directly to that pty:

- `ESC [ 4 ; height ; width t` — resize the window, in pixels
- `ESC [ 3 ; x ; y t` — move the window to a pixel position

This is a standard if obscure xterm feature and needs nothing beyond `xterm`
and `tmux`. `allowWindowOps` is disabled by default in most distributions,
because an untrusted program printing escape sequences to a terminal should
not be able to move windows; `xtm` therefore enables it explicitly, and only
on the windows it launches itself.

Because placement is a *request* to the window manager, the result is
whatever the window manager decides to honour. Size increments, panel struts
and snapping can all adjust it. `--verify` makes that visible by re-reading
the geometry afterwards and reporting the difference.

## Window identification

Windows are found by their instance name (`WM_CLASS`) first and their title
second. The title alone is not reliable: `set-titles on` in `.tmux.conf`, and
many shell prompts, rewrite the terminal title at runtime, which would make
`xtm` lose track of its own windows. A running program cannot change the
instance name, so it survives.

All three tools identify windows by instance name, so `set-titles on` in
`.tmux.conf` and custom `title:` templates are both safe. `xwininfo` has no
search verb, so it enumerates the whole window tree and filters it; the tree
dump carries the instance name alongside the title, and geometry is then read
with `xwininfo -id`, never by title.

## Frame compensation

`ESC [ 3 ; x ; y t` asks the window manager to place the window; with the
default gravity the window manager applies that position to the *frame* it
draws around the client. The geometry tools, however, report the position of
the *client* window, which sits inside the frame. Saving what was read and
replaying it would therefore shift every window down and right by the
thickness of its title bar on each save/restore cycle.

`xtm` corrects for this on capture: it reads `_NET_FRAME_EXTENTS` with
`xprop` and subtracts the left and top extents from the captured position, so
the value stored in the profile is the frame position that placement expects.
The cycle is then stable. Sizes need no correction, since both the resize
escape sequence and the geometry tools work in client-area pixels.

When `xprop` is missing, or the window manager does not publish
`_NET_FRAME_EXTENTS`, compensation is skipped with a debug message and
capture proceeds uncorrected. `--no-frame-compensation` disables it
explicitly, which is the right choice for a window manager that draws no
decorations at all.

## Geometry tool selection

Tools are probed in the fixed order `xdotool`, `wmctrl`, `xwininfo`, and the
first one found on `PATH` is used for the whole run. They differ:

| Capability | `xdotool` | `wmctrl` | `xwininfo` |
|---|---|---|---|
| Find windows by instance name | Yes | Yes (`-lx`) | Yes (`-root -tree`) |
| Read geometry | Yes | Yes | Yes |
| Raise and focus a window | Yes | Yes | No |
| Subprocess calls to enumerate N windows | 2 + 2N | 1 | 1 |
| Subprocess calls to read N windows' geometry | N | 1 | N |

`xdotool` is preferred despite the highest enumeration cost, because it has
no ambiguity in how it matches windows and can also raise them. Its cost comes
from having no verb that prints a window's `WM_CLASS`: each window needs a
`getwindowname` call and an `xprop` call. Where `xprop` is absent the instance
name is unavailable, the title alone identifies the window, and the cost falls
to 2 + N.

Commands that need read-back fail with a clear message when none of the three
is installed; everything else continues to work, and the config file can
always be edited by hand.

## Option deep dive

### What each option does to the data

Options fall into three groups by their effect.

**Options that change what is read.** `--config` and `--state-dir` change
which files are consulted, before anything else happens. `--profile` selects
a profile *and writes* it to the state file as a side effect; `--auto`
suppresses the state file for this run so that the `match` blocks decide
instead. `--all` widens every command to include profiles bound to other
machines, which are otherwise hidden.

**Options that change what is written.** `--dry-run` intercepts every write:
config saves, state-file writes, window placement, xterm launches and session
kills all become log lines beginning `[dry-run]`, and nothing is modified.
`--yes` bypasses the confirmation prompt for `--close-all`.
`--no-frame-compensation` changes the numbers stored by `--update-profile`
and `--new-profile` by disabling the decoration correction described above.

**Options that change what is reported.** `--json` changes the format of the
four reporting commands and nothing else; it has no effect on actions.
`--verify` adds a read-back-and-compare step after each placement.
`--debug`, `--quiet`, `--log-level` and `--log-file` affect only the log
stream, never stdout.

### Fixed execution order

Every invocation runs the same pipeline, in this order:

1. **Parse arguments.** A usage error exits 2 before anything is read. A
   positional profile name is folded into `--profile` here, and supplying
   both spellings is rejected at this point.
2. **Configure logging.** `--log-level`, then `--debug`, then `--quiet`,
   otherwise INFO. The log file, if requested, is always opened at DEBUG.
3. **Resolve paths.** `--config` beats `XTM_CONFIG_DIR` beats the default;
   `--state-dir` beats `XTM_STATE_DIR` beats the default. The state file name
   gains this machine's short hostname.
4. **Set run modes.** `--dry-run`, `--verify` and `--no-frame-compensation`
   take effect from here on.
5. **Take the config lock**, for actions that write to the config file.
6. **Load and sanity-check the config**, creating one if it does not exist,
   and adding the reserved `default` profile if the file lacks it. That
   addition is saved immediately, so the first run against a config written
   by an older version modifies it.
7. **Resolve the profile:** `--profile`, then the saved current profile
   (unless `--auto`), then a `match` block, then the reserved `default`
   profile. Only profiles visible on this machine are eligible, unless
   `--all` was given.
8. **Validate that profile** in full, injecting default `stack` and
   `sessions` sections if absent.
9. **Run the action.**
10. **Save the config**, for actions that changed it.

Within `--reset` and `--reset-all`, the order is likewise fixed: named
sessions are processed in config file order; then, for `--reset-all`, strays
are stacked in alphabetical order. Within a single window, resize always
precedes move, because on some window managers a resize shifts the anchor
point, and moving last guarantees the requested final position either way.

### Per-option edge cases and missing data

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
| `--close-all` | Scoped to the current profile's sessions unless `--all` widens it to every visible profile's; sessions belonging to no profile are never touched. With nothing running, logs a notice and exits 0. When stdin is not a terminal, the confirmation prompt is skipped, because a scripted caller has already opted in and cannot answer. |
| `--focus` | Requires `xdotool` or `wmctrl`; with only `xwininfo` present, fails with an explanation. A session with no window exits 1. |
| `--update-profile` | Captures only windows whose session belongs to the current profile; others are counted and reported, and added only with `--capture-new`. With no xtm windows open, logs a notice and changes nothing. Windows whose geometry cannot be read are skipped with a warning, and the rest are still saved. Sessions in the profile with no window on screen keep their recorded position. New windows are added to the profile. |
| `--new-profile` | An existing name is an error. The new profile gets `prefix: <name>-` written explicitly, and every captured window is recorded with an explicit `session:` so that the sessions already running are not renamed out from under it. Stack settings are inherited from the current profile, falling back to the built-in defaults. Capturing an empty screen creates an empty profile and says so. |
| `--set` | Creates the session entry if it does not exist, and preserves `command`, `cwd` and `xterm_args` if it does. Rejects non-integer, non-finite and non-positive sizes. |
| `--delete-session` | A name that is not in the profile is an error. A running session is not killed; only its recorded position is removed. |
| `--delete-profile` | Deleting the current profile also clears the state file, so the next run falls back to matching or `default`. Deleting the last remaining profile is allowed. |
| `--copy-profile` | The target must not already exist. The copy is deep, so later edits to one do not affect the other. |
| `--rename-profile` | Updates the state file if the renamed profile was current. |
| `--edit` | A config left invalid after editing is reported and exits 1; the file is not reverted, so the edit is never lost. |
| `--dry-run` | Confirmation prompts are auto-accepted, since nothing will actually happen. |
| `--verify` | Without a geometry tool, warns once and skips verification rather than failing the placement. |
| `--json` | Applies only to `--list`, `--list-profiles`, `--current-profile` and `--validate`. With no profile saved, `--current-profile --json` reports `current_profile: null` alongside the `resolved_profile` that would be used. |

Missing config data is handled uniformly: a profile with no `stack` gets the
built-in default, a profile with no `sessions` gets an empty mapping, and any
geometry field that is absent, non-numeric, boolean, infinite or NaN is a
validation error naming the profile, session and field.

### A worked example

Start from this profile, with `logs` already on screen at a position that has
been dragged from where the profile says it should be:

```yaml
profiles:
  desk:
    stack: {x: 40, y: 40, width: 900, height: 650, offset_x: 40, offset_y: 40}
    sessions:
      logs: {x: 960, y: 0, width: 960, height: 580, command: journalctl -f}
```

Now run:

```bash
xtm --profile desk --reset-all --verify --debug
```

Step by step:

1. Logging is set to DEBUG on stderr.
2. Paths resolve to `~/.config/xtm/config.yaml` and
   `~/.local/state/xtm/current_profile.workstation`.
3. `desk` is validated and written to the state file.
4. `logs` is attached, so it is repositioned rather than opened. Its
   `command` is not used: the session already exists.
5. The client tty is resolved once: `/dev/pts/5`.
6. `ESC [ 4 ; 580 ; 960 t` is written to that pty, then
   `ESC [ 3 ; 960 ; 0 t`.
7. `--verify` waits briefly, reads the geometry back as `x=960 y=0
   960x580` after frame compensation, and reports no delta.
8. Strays are listed, sorted, and placed at stack slot 0 — `x=40, y=40,
   900x650` — slot 1 at `x=80, y=80`, and so on.

Then capture the result:

```bash
xtm --update-profile
```

9. Windows are enumerated by instance name; `xtm-logs` is found.
10. Its client geometry reads as `x=964 y=2`, because the window manager
    draws a 4-pixel border and a 2-pixel title bar edge.
11. `xprop` reports `_NET_FRAME_EXTENTS = 4, 4, 2, 4`, so 4 and 2 are
    subtracted, giving `x=960 y=0`.
12. The profile is rewritten with `x=960 y=0` — identical to what was there
    before, which is exactly the point: the round trip does not drift.

### Choosing between similar options

| Compare | Difference |
|---|---|
| `--reset` vs `--reset-all` | `--reset` touches only sessions named in the profile. `--reset-all` also gathers every attached session that is *not* named, and stacks them diagonally. Use `--reset` to avoid disturbing unrelated work. |
| `--open` vs `--reset` | `--open` handles exactly one session and refuses if it is already attached. `--reset` is idempotent: it repositions what is running and opens what is not. |
| `--update-profile` vs `--new-profile` | Both capture the screen. The first overwrites the current profile in place; the second creates a new one and fails if the name exists. Use `--new-profile` when the current layout is worth keeping alongside the old one. |
| `--set` vs `--update-profile` | `--set` writes one exact position from the command line and needs no geometry tool. `--update-profile` captures every window at once and does. |
| `--close` vs `--detach` | `--close` kills the session, destroying its tabs, panes and running jobs. `--detach` closes only the window and leaves everything running, so it is the reversible one. Use `--detach` to tidy the screen, `--close` to finish with a session. |
| `prefix:` vs `session:` | `prefix:` makes a slot's session belong to the profile, so two profiles never collide. An explicit `session:` opts one slot out of that and names the session outright, which is how two profiles share one. |
| `--close` vs `--delete-session` | `--close` kills a running session and its window, leaving the profile untouched. `--delete-session` removes the profile entry and leaves the running session alone. |
| `--dry-run` vs `--verify` | `--dry-run` runs before the fact and changes nothing. `--verify` runs after the fact and checks that what was requested actually happened. They can be combined, though `--verify` has nothing to check under `--dry-run`. |
| `--profile` vs `--auto` | `--profile` names a profile and remembers it. `--auto` forgets the remembered one for this run and re-derives the choice from hostname and `$DISPLAY`. |
| `--quiet` vs `--json` | `--quiet` silences the log stream on stderr. `--json` changes the data format on stdout. Combine them for clean machine-readable output. |

### Task-to-options recipes

| Task | Command |
|---|---|
| Restore the whole desktop | `xtm --reset-all` |
| Restore without touching unrelated terminals | `xtm --reset` |
| Set up a new machine's layout by hand | `xtm --new-profile thismachine` then `xtm --set …` per window |
| Switch profile | `xtm desk` |
| Bind a profile to one machine | Add `match: {hostname: …}` to it |
| Bind one profile to several machines | `match: {hostname: [box-a, box-b]}` |
| Make a machine-specific profile universal | `xtm --make-global desk` |
| See profiles belonging to other machines | `xtm --all --list-profiles` |
| See what a reset would do first | `xtm --dry-run --reset-all --debug` |
| Find out why a window lands in the wrong place | `xtm --reset --verify --debug --log-file /tmp/xtm.log` |
| Save the layout after rearranging by hand | `xtm --update-profile` |
| Copy a layout to a second machine | `xtm --copy-profile desk desk-remote`, then edit positions |
| Script over the session list | `xtm --list --json --quiet \| jq …` |
| Tidy the screen, keep everything running | `xtm --close-all --detach-mode` |
| Tear down the day's work | `xtm --close-all` |
| Clean up sessions left by other profiles | `xtm --close-all --all` |
| See every profile and what is running | `xtm --list-profiles --verbose` |
| Share one session between two profiles | Give the slot the same `session:` in both |
| Switch without closing the old windows | `xtm lab --on-switch leave` |
| Adopt a window opened outside the profile | `xtm --update-profile --capture-new` |
| Recover from a broken config | `xtm --validate`, then `xtm --edit` |
| Work with a throwaway config | `xtm --config /tmp/test.yaml --state-dir /tmp/state --list` |

## Profile auto-selection

### Machine-specific and global profiles

A profile is **machine-specific** when it carries a `match` block, and
**global** when it does not. There is one mechanism, not two: the `match`
block *is* the binding.

```yaml
desk:
  match:
    hostname: workstation*          # one glob

build:
  match:
    hostname: [build-1, build-2]    # or several
```

`hostname` accepts a single shell glob or a list of them, and the profile is
bound to any machine matching at least one. That lets a single profile serve
several machines with the same screen layout, without duplicating it.

Binding has two consequences:

- **Visibility.** A profile bound to another machine is hidden: it does not
  appear in `--list-profiles` and cannot be selected by name. `--all` reveals
  it for listing and for management commands, so a profile is never stranded
  somewhere you cannot reach it. `--validate` ignores visibility entirely and
  always checks every profile, since a config that is broken for another
  machine is still broken.
- **Auto-selection.** When nothing has been chosen explicitly and nothing is
  remembered — or under `--auto` — profiles are examined in sorted name order
  and the first whose hostname fits is used. A global profile is never
  auto-selected: being available everywhere is not evidence that it belongs
  *here*.

`--make-global NAME` removes the binding. It is deliberately one-way: there
is no command to re-bind a global profile, because doing so would hide a
profile that other machines may already be relying on. Add a `match` block by
hand if that is genuinely what you want.

A `match` block that is malformed — an unknown key, an empty or non-string
hostname — leaves the profile **visible** rather than hidden, so that the
validation error is what you see instead of the profile silently vanishing.

Matching is case-sensitive on Linux, and compares against the short hostname:
`workstation.corp.example.com` is matched as `workstation`.

### How profiles and sessions relate

By default a profile **owns** its sessions. Slot `work1` in a profile with
`prefix: desk-` is the session `desk-work1`, and the same slot name in a
profile prefixed `lab-` is a different session entirely. Two profiles
therefore cannot collide by accident, and switching between them is
unambiguous.

A slot with an explicit `session:` is the exception. That name is used
verbatim, never prefixed, so two profiles that both write `session: notes`
land on the same tmux session. That is the supported way to keep one session
alive across profiles:

```yaml
desk:
  prefix: desk-
  sessions:
    work1: {x: 0, y: 0, width: 960, height: 1180}      # → desk-work1
    notes: {session: notes, x: 960, y: 0, ...}         # → notes

lab:
  prefix: lab-
  sessions:
    work1: {x: 0, y: 0, width: 900, height: 700}       # → lab-work1
    notes: {session: notes, x: 900, y: 0, ...}         # → notes, the same one
```

The rule in one sentence: **profile-owned sessions come and go with the
profile; explicitly named sessions persist across switches.**

Two slots in one profile resolving to the same session is rejected by
validation, since they would fight over the same window on every reset.

### Switching profiles

Switching away from a profile disposes of its windows according to its
`on_switch` setting:

| Mode | Effect |
|---|---|
| `leave` | Windows stay exactly as they are. |
| `detach` | **Default.** Each window closes, but its session, tabs, panes and running jobs stay alive. Reattaching restores everything. |
| `kill` | Sessions are destroyed, taking their tabs and panes with them. |

Sessions the *new* profile also claims are left attached rather than closed
and immediately reopened, so a shared session simply moves to its new slot.

`--on-switch MODE` overrides the setting for one run, which is the escape
hatch when the default would close something you wanted to keep.

### Sessions with tabs and splits

`xtm` works one level above tmux's internals. It issues only four tmux
commands — `new-session`, `list-sessions`, `list-clients` and `kill-session`,
plus `detach-client` — and never touches windows or panes inside a session.
That has four consequences worth knowing:

- **`--update-profile` is pane-agnostic.** It captures four numbers per slot
  and writes only those. A session split into six panes is saved exactly like
  a session with one.
- **Panes survive `--reset`.** The tmux server keeps running, so a session
  reattaches with its tabs, panes, history and jobs intact — even across the
  X session restarting.
- **`--close` destroys them.** Killing a session takes every tab and pane with
  it. `--detach` is the non-destructive alternative.
- **Resizing reflows them.** Placing a window sends a resize before a move, so
  tmux receives a `SIGWINCH` and reflows the session's panes to the new size.
  Nothing is lost, but a hand-tuned split will shift if the new slot is a
  different size.

### The reserved `default` profile

`default` is created with the config and recreated if it goes missing. It
cannot be deleted, renamed, renamed onto or copied onto, and it may not carry
a `match` block — it is always global. Its contents are ordinary and fully
editable.

The point is stability: because resolution ends at a profile that is
guaranteed to exist under a fixed name, the fallback can never silently
change when the config is edited or reordered.

Matching on `$DISPLAY`, supported in 0.3, was removed in 0.4; a config still
using it fails validation with an explanation rather than silently never
matching.

## Concurrency and durability

Config writes are atomic: content is written to a temporary file in the same
directory, flushed, `fsync`ed, and then renamed into place, with the parent
directory synced afterwards. A crash or a full disk therefore leaves the old
config intact rather than a truncated one.

Atomicity alone does not prevent a *lost update*, where two simultaneous
`--update-profile` runs each load the same starting config and the second
write discards the first one's changes. Commands that modify the config take
an exclusive `flock` on a sibling `config.yaml.lock` file for the whole
read-modify-write cycle. A separate lock file is used because the atomic
rename replaces the config file's inode, which would detach a lock held on it
directly. If locking is unavailable, the run proceeds unlocked rather than
failing.

## YAML handling

`PyYAML` is used when importable. Two details are worth knowing:

- `safe_dump(sort_keys=...)` arrived in PyYAML 5.1. On older releases, still
  shipped by long-lived enterprise distributions, `xtm` falls back to its own
  dumper rather than rewriting the config in alphabetical order on every
  save.
- The built-in reader and writer handle the full shape `xtm` uses: nested
  mappings, flow-style mappings and lists, quoted strings, inline comments,
  and quoting of values that would otherwise be read back as booleans, nulls
  or numbers.

The built-in parser requires indentation in multiples of two spaces and
rejects tabs, both with an explicit message naming the offending line.

### What a config rewrite changes

Any command that writes the config — `--set`, `--update-profile`,
`--delete-session`, `--make-global` and the profile management commands —
rewrites the whole file. Two things do not survive that:

- **Flow style becomes block style.** `work1: {x: 0, y: 0}` is read
  correctly, but is written back expanded over four lines. The values and
  their meaning are identical; only the layout changes.
- **Comments are dropped.** Neither backend preserves them.

Both are worth knowing before hand-formatting a config, and neither affects
reading: flow style and comments are fully supported as input.

## Upgrading from an earlier version

Existing configs keep working unchanged. A profile with no `prefix` behaves
exactly as before: slot names are used as session names directly.

Two things to be aware of when adopting the new keys:

- **Adding a `prefix:` renames a profile's sessions.** Sessions already
  running under the old names match no slot afterwards, so they appear as
  strays until closed or reattached. Either close them first, or give the
  affected slots an explicit `session:` naming what is already running.
- **`on_switch` defaults to `detach`**, so the first profile switch after
  upgrading will close the previous profile's windows. Nothing is destroyed —
  every session, tab, pane and job stays alive — but set `on_switch: leave`
  on a profile to keep the old behaviour.

## Assumptions and limitations

- **Linux.** Matching a tmux client to the xterm that spawned it reads
  `/proc/<pid>/stat`.
- **X11.** There is no Wayland support; the placement mechanism is an X
  window operation.
- **xterm specifically.** Placement relies on xterm's `allowWindowOps`
  escape sequences. Other terminal emulators are not supported.
- **One client per session.** When several tmux clients are attached to one
  session, `xtm` can identify its own only for a window it opened in the same
  invocation. For `--reset` on a session opened by an earlier run, it acts on
  whichever client tmux lists first. A PID is deliberately not persisted
  across invocations, because a reused PID would be actively misleading.
- **No screen-size awareness.** Stack offsets can eventually push a window
  off-screen; there is no wraparound. Lower `offset_x`/`offset_y`, or give
  the sessions explicit positions.
- **Absolute pixels only.** Positions do not scale between different
  resolutions, which is why profiles are per machine.
- **`command` runs only at creation.** Attaching to an existing session
  ignores it, as does tmux itself. The command replaces the pane's shell, so
  the pane closes when the command exits; use something like
  `bash -lc 'cmd; exec bash'` to keep the pane alive afterwards.
- **`command` is executed by a shell**, so it supports pipes and `&&`, and
  should be treated with the same care as any line in a shell profile.
- **Windows opened by hand are invisible to xtm**, since they do not carry an
  `xtm-` instance name.
- **`xtm` manages sessions, not their contents.** Tabs, panes and layouts
  inside a session are tmux's business; see "Sessions with tabs and splits".

---

# Part 3 — Developer guide

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
| Statements | 1649 |
| Line coverage | **95.0%** (1566 / 1649) |
| Branches | 734 |
| Branch coverage | **92.1%** (676 / 734) |
| Tests | 420, all passing |

```bash
pip install coverage
mkdir -p /tmp/covsite && printf 'import coverage\ncoverage.process_startup()\n' > /tmp/covsite/sitecustomize.py
COVERAGE_PROCESS_START=$PWD/.coveragerc PYTHONPATH=/tmp/covsite \
    python3 -m coverage run -m unittest test_xtm
python3 -m coverage combine && python3 -m coverage report -m
```

The `sitecustomize.py` step is what makes the subprocess integration tests
count toward coverage; without it the measured figure drops sharply, because
most command code runs in a child process.

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
test_xtm.py            Test suite: unit tests, integration tests, fake tools.
install.sh             Installer: copies to a PATH directory, optional completion.
xtm-completion.bash    Bash completion for actions, profiles and session names.
config.yaml.example    Annotated two-profile example configuration.
README.md              This document.
CHANGELOG.md           Release history.
.coveragerc            Coverage configuration: branch and parallel modes.
```

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
