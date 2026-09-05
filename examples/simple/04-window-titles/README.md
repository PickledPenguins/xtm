# simple/04 — Window titles

Level: **simple**. What the window manager shows in the title bar, and how to control it.

## What it shows

- **`title:` on the profile is a template.** `{slot}`, `{session}` and
  `{profile}` are substituted for each window. The default is
  `xtm:{session}`.
- **`title:` on a slot overrides it.** `logs` gets a fixed string rather than
  the template.
- **Titles are cosmetic, not identity.** xtm finds its windows by their
  instance name (`xtm-<session>`), which a running program cannot change, so
  a custom title — or `set-titles on` in `.tmux.conf` — cannot make xtm lose
  track of a window.

## Try it

```bash
XTM_CONFIG_DIR=examples/simple/04-window-titles ./xtm.py --list --json | grep title
```
