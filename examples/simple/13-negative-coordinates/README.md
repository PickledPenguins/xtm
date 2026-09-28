# simple/13 — negative coordinates

Level: **simple**. A monitor left of the origin, and why the geometry format
is what it is.

## What it shows

- **`x` and `y` may be negative.** A monitor placed left of or above the
  primary one has negative coordinates, and xtm places windows there
  normally.
- **Sizes may not be negative** — a width of `-900` is a validation error.
- **Why `x,y,width,height` and not `WxH+X+Y`.** In an X geometry string a
  leading `-` means "measured from the opposite edge", which collides with a
  genuinely negative coordinate. The comma form has no such ambiguity.

## Try it

```bash
XTM_CONFIG_DIR=examples/simple/13-negative-coordinates src/xtm.py --list
XTM_CONFIG_DIR=examples/simple/13-negative-coordinates src/xtm.py --validate
```
