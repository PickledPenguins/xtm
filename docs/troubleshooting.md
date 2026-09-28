# Troubleshooting

What breaks, what it looks like, and what to do about it.

## Nothing opens, and the error mentions `DISPLAY`

xtm needs an X display to open a window. Over SSH without X forwarding, or from a virtual
console, there is nothing to place a window on.

Reporting commands still work: `--list`, `--list-profiles`, `--validate` and every
config-editing command need no display. Only opening, placing, focusing and reading
geometry back do.

## "Reading window positions needs xdotool, wmctrl or xwininfo"

`--update-profile`, `--new-profile`, `--verify` and the live column of `--list` read the
screen, which needs one of the three geometry tools. Install any one of them; see
[placement.md](placement.md) for what each can do.

Placement itself does **not** need them — `--reset` works with none installed — so a
machine without any can still restore a layout from a config edited by hand or with
`--set`.

## "Focusing a window needs xdotool or wmctrl"

`xwininfo` can query windows but cannot raise them, so `--focus` refuses before looking for
the window. The message names the capability rather than reporting a missing window, which
would be misleading.

## Windows open but do not move

Placement writes xterm's own escape sequences, which xterm ignores unless `allowWindowOps`
is enabled. xtm sets it on the windows it opens itself, so this normally only affects
windows *not* opened by xtm — which xtm does not manage anyway, since they carry no `xtm-`
instance name.

If windows xtm opened do not move:

- Confirm the terminal really is xterm. Other emulators do not implement these sequences,
  and are not supported.
- Check for a system-wide xterm resource forcing `allowWindowOps: false`, which overrides
  the per-window setting.
- Run `xtm --reset --verify --debug`: the debug log shows the exact bytes written and the
  tty they went to, and `--verify` reports what the window manager actually did.

## "... is off screen on this screen"

The profile puts a window where no connected monitor is — typically a layout written for a
desk with two monitors, used on the same laptop undocked, or a monitor that has not woken
up yet. The window is still placed as the profile says, so it is running but out of sight.

- `xtm --clamp` brings every such window onto a monitor for this run.
- `xtm NAME` switches to a profile written for this screen; `xtm --new-profile NAME`
  captures one from the windows as they are.
- `xtm --list` marks each slot that does not fit with `[off screen]` or
  `[partly off screen]`.

## Windows land near, but not exactly at, the requested position

Placement is a *request* to the window manager. Size increments, panel struts, snapping and
tiling window managers can all adjust it. `--verify` reports the difference; there is
nothing xtm can do to force the issue.

## Every save drifts the windows down and to the right

Frame compensation is not working: the position being saved is the client window's rather
than the frame's, so each save/restore cycle adds the height of the title bar. Either
`xprop` is missing, or the window manager does not publish `_NET_FRAME_EXTENTS`.

Install `xprop`, or if the window manager draws no decorations at all, pass
`--no-frame-compensation` so nothing is subtracted. `xtm --update-profile && xtm --reset
--verify` reporting no delta is the check that the cycle is stable.

## "xtm reads its config with PyYAML, which is not installed"

PyYAML is xtm's one dependency. Install it for the `python3` that runs xtm — the system
package is usually `python3-yaml` or `python3-pyyaml`, or `python3 -m pip install --user
PyYAML` — and run xtm again. `--help` and `--version` work without it.

## "line N, column M: ..." when the config is read

The file is not valid YAML, and the message is PyYAML's own, placed at the problem. The
common ones: a tab in the indentation ("found character that cannot start any token"), an
unclosed `{` or `[`, a list item or a stray line where a key belongs ("did not find
expected key"), and a key written twice in one mapping, which xtm refuses because YAML
would silently keep only the last one.

## "must be text, but YAML read ... as a boolean"

YAML types unquoted values: `yes`, `no`, `on` and `off` are booleans, `0755` is a number
and `2026-01-01` is a date. Quote the value (`title: "yes"`). The reverse also happens: a
position written as `"900"` is text, and xtm asks for a number. See
[configuration.md](configuration.md#values-mean-what-yaml-says).

## A profile is in the config but xtm says it does not exist

It is bound to another machine by its `match` block, and profiles bound elsewhere are hidden
rather than listed. Two commands answer two different questions:

```bash
xtm --all --list-profiles              # what exists, including elsewhere
xtm --hostname build-3 --auto --list   # what build-3 would actually do
```

`--all` reveals a hidden profile for listing and lets management commands reach it.
`--hostname` runs *as* that machine, so the `match` block is exercised rather than
bypassed — which is the only way to check that a binding is right without going to the
machine. `xtm --make-global NAME` removes the binding for good.

## "Current profile 'x' no longer exists"

The profile named in this machine's state file was deleted or renamed. xtm
reports it rather than falling back, because acting on a different profile than
the one last chosen is worse than stopping. Choose one with `xtm NAME`, which
also rewrites the state file.

The same message appears when a saved profile becomes *hidden* — someone added a
`match` block binding it to another machine. `xtm --all --list-profiles` shows it, and
`xtm --hostname NAME --list` shows what the machine it now belongs to would do with it.

## A session shows "attached elsewhere", or a reset opens a second terminal for it

The session is attached in a terminal xtm did not open — an SSH login, a Konsole, an xterm
started by hand. xtm leaves that terminal alone and opens its own window beside it, so both
show the session. tmux sizes a session to its most recently active client, so the other
terminal may reflow while the xtm window is in use. `tmux detach-client -t TTY` closes the
other one if it was left behind; `tmux list-clients` shows which ttys are attached.

## A session shows as a stray after adding a prefix

Adding `prefix:` renames every slot's session, so sessions already running under the old
names match no slot. Close them and let `--reset` reopen them, or give the affected slots
an explicit `session:` naming what is already running.

## `--close-all` did not close something

It is scoped to the effective profile's sessions. `--all` widens it to every visible
profile's sessions; sessions belonging to no profile are never touched, deliberately.

## A pane closes as soon as the window opens

`command` replaces the pane's shell, so the pane closes when the command exits. Use
something like `bash -lc 'cmd; exec bash'` to keep the pane alive afterwards.

## An edit was rejected with "would leave profile ... invalid"

A command that edits a profile re-runs validation before saving. The edit would have
produced a config that no later command could load — most often two slots resolving to the
same session — so nothing was written. The message carries the underlying validation error.

## Everything is confusing and the config may be wrong

```bash
xtm --validate                                  # check every profile
xtm --list-profiles --verbose                   # what exists, what is running, who owns it
xtm --dry-run --reset-all --debug               # what a reset would do, changing nothing
xtm --reset --verify --debug --log-file /tmp/xtm.log
```

The log file is always written at DEBUG regardless of the console level, so
`--quiet --log-file` gives a quiet terminal and a full trace.
