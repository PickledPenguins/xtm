# simple/03 — Startup commands

Level: **simple**. What a window runs when it opens, where it starts, and what it looks like.

## What it shows

- **`cwd:` sets the working directory.** `~` is expanded. The `editor` slot
  opens in `~/src/project`.
- **`command:` runs something on startup.** It is passed to tmux as a single
  argument, so tmux hands it to a shell and pipelines work exactly as
  written: `journalctl -f | grep -v DEBUG`.
- **`xterm_args:` passes extra arguments to xterm**, inserted before `-e`.
  The `production` slot uses this to give itself a dark red background. That
  is the real motivation for the feature: it is hard to type into the wrong
  terminal when the wrong terminal is bright red.
- **`title:` is configurable at two levels.** The profile sets a template
  (`"{profile}: {slot}"`, with `{slot}`, `{session}` and `{profile}`
  available); a slot can override it, as `production` does. Invalid
  templates are caught by `--validate`, not at window-open time.
- **The title is cosmetic; the identity is not.** Windows are found by their
  instance name (`WM_CLASS`), which is derived from the session name and is
  not configurable. A program running inside the terminal can rewrite the
  title at any moment but cannot touch `WM_CLASS`, so a custom title can
  never break window discovery.

## The timing rule that catches people out

`command`, `cwd` and `xterm_args` apply when the session is **created**.
xtm attaches with `tmux new-session -A`, which attaches to an existing
session and creates one otherwise — and tmux ignores `-c` and the startup
command when attaching. So changing any of these takes effect after the
session is closed and reopened, not on the next `--reset`:

```bash
src/xtm.py --close logs && src/xtm.py --open logs   # picks up the new command
src/xtm.py --reset                                # does not
```

## Try it

```bash
XTM_CONFIG_DIR=examples/simple/03-startup-commands src/xtm.py work --list
XTM_CONFIG_DIR=examples/simple/03-startup-commands src/xtm.py work --validate
```

`--list` shows `command:` and `cwd:` on their own lines, but only for the
slots that set them, so the common case stays one line per window.
