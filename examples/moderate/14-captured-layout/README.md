# moderate/14 — a captured layout

Level: **moderate**. What the tool writes when you let it, and how to read it.

## What it shows

- **Every slot has an explicit `session:`.** `--new-profile` and
  `--update-profile --capture-new` write it because those sessions already
  exist under those names; applying the profile's prefix afterwards would
  rename them out from under their windows.
- **The numbers are not round.** They came off the screen, after frame
  compensation subtracted the window manager's border, and they are what a
  round trip preserves — capture, reset, capture again gives the same values.
- **The prefix is written explicitly**, so a slot you add by hand later is
  owned by the profile in the usual way while the captured ones stay shared.

## Try it

```bash
XTM_CONFIG_DIR=examples/moderate/14-captured-layout src/xtm.py --list
```

Every slot reports `(shared, kept on profile switch)`, which is the visible
consequence of the explicit names.
