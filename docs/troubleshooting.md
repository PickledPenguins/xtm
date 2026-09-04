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

## The config loads on one machine and fails on another

This is the PyYAML divergence xtm exists to prevent, and the message names the line and the
fix. It means the config contains something the two YAML readers would read differently —
an unquoted `0755`, `yes`, `1:30`, an anchor, a merge key, a tab, or three-space indentation.

Quote the value, or write it the way the message suggests. The full list is in
[configuration.md](configuration.md). `xtm --validate` checks every profile in the file,
including those bound to other machines.

## A profile is in the config but xtm says it does not exist

It is bound to another machine by its `match` block, and profiles bound elsewhere are hidden
rather than listed. `xtm --all --list-profiles` shows them, and `--all` also lets management
commands reach them. `xtm --make-global NAME` removes the binding for good.

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
