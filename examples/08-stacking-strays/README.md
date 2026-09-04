# 08 — Stacking strays

One configured window, and somewhere for everything else to go.

## What it shows

- **`stack:` is the fallback position** for any session with no entry under
  `sessions:`. `xtm --open scratch` uses it, and so does `--reset-all` for
  every attached session the profile does not name.
- **Offsets accumulate by index.** The first stray is at `(100, 80)`, the
  second at `(160, 125)`, the third at `(220, 170)`. Without the offsets they
  would land exactly on top of one another.
- **There is no wraparound.** xtm does not know the screen size, so enough
  simultaneous strays eventually walk off the edge. Lower the offsets, or
  give the sessions explicit positions.
- **`--reset` ignores strays entirely**; only `--reset-all` gathers them.

## Try it

```bash
XTM_CONFIG_DIR=examples/08-stacking-strays ./xtm.py --list
```

With a real display, `--reset-all` places `main` and stacks anything else
that is attached.
