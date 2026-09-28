# complex/09 — geometry at the edges

Level: **complex**. Every boundary value in one profile, all of them legal.

## What is combined

- **Negative coordinates in both axes** (`-3840, -2160`), for a monitor placed
  above and left of the primary one.
- **The smallest legal size**, one pixel by one. Zero is not legal; a size
  must be positive, and `--validate` says so.
- **A size larger than any screen.** xtm does not clamp it: placement is a
  request, and what a window manager does with an impossible one is its
  business.
- **Zero stack offsets**, so every stray lands in the same place — legal, and
  occasionally what you want on a small screen.
- **The origin itself**, which is the boundary the sign rules turn on.

## Why it is worth having

These are the values a hand-written config reaches by accident — a monitor
arrangement that puts the origin somewhere unexpected, a copied number with a
digit missing. Each is accepted here, which is the claim: xtm validates the
*shape* of geometry, not its plausibility, and the failure modes it does
refuse are the ones it can be sure about.

## Try it

```bash
XTM_CONFIG_DIR=examples/complex/09-edge-geometry src/xtm.py --validate
XTM_CONFIG_DIR=examples/complex/09-edge-geometry src/xtm.py --list
```
