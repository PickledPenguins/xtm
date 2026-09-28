# moderate/11 — a twelve-cell grid

Level: **moderate**. The boilerplate for a tiled screen, and the arithmetic
that goes with it.

## What it shows

- **Twelve slots on a regular grid**, named by row and column so the config
  reads like the screen looks.
- **The arithmetic is yours.** xtm places what the profile says; it does not
  know the screen size and does no tiling of its own. Cell width is screen
  width over columns, and each `x` is the column index times that.
- **Copy this and edit the numbers** — that is what this example is for. A
  2560-wide screen with three columns is `853`-wide cells and the same shape.
- **Order matters on a reset**: slots are processed in config order, so the
  file's order is the order windows appear.

## Try it

```bash
XTM_CONFIG_DIR=examples/moderate/11-big-grid src/xtm.py --list
```
