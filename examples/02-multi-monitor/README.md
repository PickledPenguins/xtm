# 02 — Multi-monitor with negative coordinates

Two monitors, with the secondary positioned to the **left** of the primary.

## What it shows

- **Negative `x` is normal and supported.** A monitor left of the primary
  occupies negative coordinates, so `reference` and `notes` sit at
  `x: -1920`. Width and height must be positive; `x` and `y` need not be.
- **Why xtm takes `x,y,width,height` rather than an X geometry string.** In
  the classic X form `WxH+X+Y`, a leading `-` means "measured from the
  opposite edge" — so `-1920` would mean 1920 pixels in from the right, not
  a genuinely negative coordinate. The two meanings collide exactly where a
  multi-monitor desktop needs them not to, so xtm uses an unambiguous
  comma-separated form instead.
- **A named profile alongside `default`.** `dual` is global (no `match:`
  block), so it is available everywhere but never auto-selected. Reach it by
  name: `xtm dual`.

## Try it

```bash
XTM_CONFIG_DIR=examples/02-multi-monitor ./xtm.py dual --list
```

Setting geometry from the command line uses the same comma form:

```bash
XTM_CONFIG_DIR=examples/02-multi-monitor ./xtm.py dual --set notes -1920,1000,1900,400
```

## Finding your own coordinates

Ask the X server where a window actually is, rather than guessing:

```bash
xdotool getdisplaygeometry
xrandr --listmonitors
```

Or place the windows by hand once and capture the result with
`--update-profile`.
