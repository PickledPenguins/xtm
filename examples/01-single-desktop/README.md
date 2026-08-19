# 01 — Single desktop

The smallest config that is actually useful: three windows in fixed
positions on one screen.

Start here. Everything else in `examples/` adds one idea to this.

## What it shows

- **`sessions:` maps a slot name to a rectangle.** `x, y` is the top-left
  corner in pixels; `width, height` is the size. `editor` fills the left
  two-thirds of a 1920×1400 area, with `shell` and `logs` stacked down the
  right.
- **`stack:` is the fallback.** Any session xtm is asked to open that has no
  entry under `sessions:` is placed here instead, with each successive one
  offset by `offset_x`/`offset_y` so they do not land exactly on top of one
  another. Without this, `xtm --open scratch` would have nowhere to go.
- **The profile is named `default`.** That name is reserved: it always
  exists, is always global, and is the last resort of profile resolution.
  It is what makes a bare `xtm` work on a machine that has never been
  configured.

## Try it

```bash
XTM_CONFIG_DIR=examples/01-single-desktop ./xtm.py --list
XTM_CONFIG_DIR=examples/01-single-desktop ./xtm.py --validate
```

With a real X display, `--reset` opens all three windows and places them:

```bash
XTM_CONFIG_DIR=examples/01-single-desktop ./xtm.py --reset
```

## Adjusting it to your screen

The numbers assume a 1920×1400 work area. Rather than editing them by hand,
place the windows where you want them and let xtm record the result:

```bash
XTM_CONFIG_DIR=examples/01-single-desktop ./xtm.py --update-profile
```
