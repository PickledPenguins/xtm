# simple/14 — an empty profile

Level: **simple**. Valid, and the state every new machine starts in.

## What it shows

- **A profile with no slots is valid.** `--list` says so rather than
  erroring, and `--reset` logs a notice and exits 0.
- **`sessions: {}` is the empty mapping**, written the way xtm writes it. The
  key may also be left out entirely; xtm supplies the empty mapping on load.
- This is what `xtm --new-profile NAME` produces when nothing is on screen,
  and the shape you fill in with `xtm --set`.

## Try it

```bash
XTM_CONFIG_DIR=examples/simple/14-empty-profile ./xtm.py --list
XTM_CONFIG_DIR=examples/simple/14-empty-profile ./xtm.py --set work1 0,0,900,700
```

The second command writes the slot in, which is how a layout usually starts.
