# xtm reference

How placement actually works, how windows are identified, what every option does, and the assumptions the tool makes. For everyday use see the [README](../README.md); for working on xtm itself see [development.md](development.md).

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
rejects tabs, both with an explicit message naming the offending line. It
reads block sequences (`- item`) in either indent style, so a list written by
PyYAML and one written by hand both load.

### Values that must be quoted

`PyYAML` implements YAML 1.1, which reads several unquoted forms in ways the
built-in parser does not. Left alone, that would make the *same* config mean
different things depending on whether PyYAML happened to be installed — and
silently, since both readings are valid numbers that pass validation. `xtm`
therefore refuses these forms before either parser sees them:

| Written as | PyYAML reads | Built-in reads |
|---|---|---|
| `0755`, `007`, `08` | octal `493` | decimal `755` |
| `0x1f`, `0b101`, `0o755` | `31`, `5`, `493` | text |
| `1_000` | `1000` | text |
| `1:30`, `1:30:00` | `90`, `5400` | text |
| `1e3`, `1.0e3` | text | `1000.0` |
| `.inf`, `.nan` | infinity, not-a-number | text |
| `yes`, `no`, `on`, `off` | `true` / `false` | text |
| `2026-01-01` | a date object | text |

Each is reported with the value, the reason, and the fix. Write a plain
decimal number if you meant a number, or quote it (`"0755"`, `"yes"`) if you
meant the literal text. Quoted values are never ambiguous and are always
accepted, and `xtm`'s own writer quotes anything that would need it, so a
config `xtm` saved always reloads unchanged.

`true`, `false`, `null` and `~` are read identically by both parsers and need
no quoting.

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

