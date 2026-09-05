# moderate/09 — a monitoring wall

Level: **moderate**. Eight commands tiled, and somewhere for the ninth window.

## What it shows

- **A grid of `command` slots**, each a long-running watch, laid out on a
  3840-wide screen in two rows of four.
- **The `stack` block earning its place.** During an incident you open a
  ninth terminal; it has no configured position, so it lands at the stack
  position and each further one is offset from the last.
- **A title template of just `{slot}`**, because on a wall the profile name
  is the same everywhere and only the slot distinguishes a window.
- **One slot with no command**, kept for typing in — the difference between a
  dashboard and a workspace.

## Try it

```bash
XTM_CONFIG_DIR=examples/moderate/09-monitoring-wall ./xtm.py --list
```
