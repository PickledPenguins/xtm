# Placement

How a window is actually moved, how xtm finds its own windows again, and why captured
positions need correcting.

## How placement works

Every xterm that xtm opens is started with `-xrm 'XTerm*allowWindowOps: true'`, titled
`xtm:<session>`, and given the window instance name `xtm-<session>`. To move or resize a
window, xtm asks tmux which pty the session's client is on and writes xterm's own
window-control escape sequences directly to that pty:

- `ESC [ 4 ; height ; width t` — resize the window, in pixels
- `ESC [ 3 ; x ; y t` — move the window to a pixel position

This is a standard if obscure xterm feature and needs nothing beyond `xterm` and `tmux`.
`allowWindowOps` is disabled by default in most distributions, because an untrusted program
printing escape sequences to a terminal should not be able to move windows; xtm therefore
enables it explicitly, and only on the windows it launches itself.

**Resize always precedes move.** On some window managers a resize shifts the anchor point,
so moving last guarantees the requested final position either way. A side effect worth
knowing: the resize sends `SIGWINCH`, so tmux reflows the session's panes. Nothing is lost,
but a hand-tuned split shifts if the new slot is a different size.

Because placement is a *request* to the window manager, the result is whatever the window
manager decides to honor. Size increments, panel struts and snapping can all adjust it.
`--verify` makes that visible by re-reading the geometry afterwards and reporting the
difference.

## Window identification

Windows are found by their instance name (`WM_CLASS`) first and their title second. The
title alone is not reliable: `set-titles on` in `.tmux.conf`, and many shell prompts,
rewrite the terminal title at runtime, which would make xtm lose track of its own windows.
A running program cannot change the instance name, so it survives.

All three geometry tools identify windows by instance name, so `set-titles on` and custom
`title:` templates are both safe. `xwininfo` has no search verb, so it enumerates the whole
window tree and filters it; the tree dump carries the instance name alongside the title,
and geometry is then read with `xwininfo -id`, never by title.

A window opened by hand is invisible to xtm, since it carries no `xtm-` instance name.

## Frame compensation

`ESC [ 3 ; x ; y t` asks the window manager to place the window; with the default gravity
the window manager applies that position to the *frame* it draws around the client. The
geometry tools, however, report the position of the *client* window, which sits inside the
frame. Saving what was read and replaying it would therefore shift every window down and
right by the thickness of its title bar on each save/restore cycle.

xtm corrects for this on capture: it reads `_NET_FRAME_EXTENTS` with `xprop` and subtracts
the left and top extents from the captured position, so the value stored in the profile is
the frame position that placement expects. The cycle is then stable. Sizes need no
correction, since both the resize escape sequence and the geometry tools work in
client-area pixels.

When `xprop` is missing, or the window manager does not publish `_NET_FRAME_EXTENTS`,
compensation is skipped with a debug message and capture proceeds uncorrected.
`--no-frame-compensation` disables it explicitly, which is the right choice for a window
manager that draws no decorations at all.

## Geometry tool selection

Tools are probed in the fixed order `xdotool`, `wmctrl`, `xwininfo`, and the first one found
on `PATH` is used for the whole run.

| Capability | `xdotool` | `wmctrl` | `xwininfo` |
|---|---|---|---|
| Find windows by instance name | Yes | Yes (`-lx`) | Yes (`-root -tree`) |
| Read geometry | Yes | Yes | Yes |
| Raise and focus a window | Yes | Yes | No |
| Subprocess calls to enumerate N windows | 2 + 2N | 1 | 1 |
| Subprocess calls to read N windows' geometry | N | 1 | N |

`xdotool` is preferred despite the highest enumeration cost, because it has no ambiguity in
how it matches windows and can also raise them. Its cost comes from having no verb that
prints a window's `WM_CLASS`: each window needs a `getwindowname` call and an `xprop` call.
Where `xprop` is absent the instance name is unavailable, the title alone identifies the
window, and the cost falls to 2 + N.

Commands that need read-back fail with a clear message when none of the three is installed;
everything else continues to work, and the config file can always be edited by hand.
`--focus` needs one that can *raise* a window, so `xwininfo` alone is refused before the
window is looked for — the reason given is the missing capability rather than a missing
window.

## Stacking

A session with no recorded position is placed at the profile's `stack` position, offset
diagonally by `offset_x`/`offset_y` per window already stacked. Strays are stacked in
alphabetical order; `--open` for an unconfigured session counts from the number of stray
xtm windows.

Offsets can eventually push a stacked window off screen; there is no wraparound. Such a
window is reported like any other (see below), and `--clamp` brings it back.

## Windows off screen

Before placing a window, xtm checks it against the connected monitors, read from `xrandr
--listmonitors` — or, without `xrandr`, the root window's size from `xwininfo -root`, which
cannot see the gaps between monitors of different sizes. A window is:

| State | Meaning |
|---|---|
| on screen | Its top-left corner is on a monitor. |
| partly off screen | It overlaps a monitor, but its top-left corner is on none, so its title bar cannot be grabbed to drag it back. |
| off screen | It overlaps no monitor at all. |

Either of the last two is reported as a warning naming the window and the monitors, and
the window is placed where the profile says. With `--clamp` it is moved onto the monitor it
overlaps most — or the first, when it overlaps none — keeping its size unless it is larger
than that monitor. `--list` shows the state beside each slot, and its JSON carries
`monitors` and `sessions[].placement`. When no monitor information is available nothing is
checked.

## A worked example

Start from this profile, with `logs` already on screen at a position that has been dragged
from where the profile says it should be:

```yaml
profiles:
  desk:
    stack: {x: 40, y: 40, width: 900, height: 650, offset_x: 40, offset_y: 40}
    sessions:
      logs: {x: 960, y: 0, width: 960, height: 580, command: journalctl -f}
```

Now run `xtm --profile desk --reset-all --verify --debug`:

1. Logging is set to DEBUG on stderr.
2. Paths resolve to `~/.config/xtm/config.yaml` and
   `~/.local/state/xtm/current_profile.workstation`.
3. `desk` is validated and written to the state file.
4. `logs` has an xtm window, so it is repositioned rather than opened. Its `command` is not
   used: the session already exists.
5. One `tmux list-clients` finds the clients of every session; the one whose parent xterm
   was started with `-name xtm-logs` is xtm's, on `/dev/pts/5`.
6. `ESC [ 4 ; 580 ; 960 t` is written to that pty, then `ESC [ 3 ; 960 ; 0 t`.
7. `--verify` waits briefly, reads the geometry back as `x=960 y=0 960x580` after frame
   compensation, and reports no delta.
8. Stray xtm windows are listed, sorted, and placed at stack slot 0 — `x=40, y=40, 900x650` — slot 1
   at `x=80, y=80`, and so on.

Then capture the result with `xtm --update-profile`:

9. Windows are enumerated by instance name; `xtm-logs` is found.
10. Its client geometry reads as `x=964 y=2`, because the window manager draws a 4-pixel
    border and a 2-pixel title bar edge.
11. `xprop` reports `_NET_FRAME_EXTENTS = 4, 4, 2, 4`, so 4 and 2 are subtracted, giving
    `x=960 y=0`.
12. The profile is rewritten with `x=960 y=0` — identical to what was there before, which
    is exactly the point: the round trip does not drift.
