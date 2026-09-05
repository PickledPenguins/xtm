# simple/12 — on_switch

Level: **simple**. One key that decides what leaving a profile costs.

## What it shows

- **`on_switch` applies to the profile being left**, not the one being
  entered.
- **`detach`** — the default. The window closes; the tmux session, its tabs,
  panes and running jobs stay alive, and reattaching restores everything.
- **`leave`** touches nothing. **`kill`** destroys the sessions.
- `--on-switch MODE` overrides it for one run, which is the escape hatch when
  the default would close something you wanted.

## Try it

```bash
XTM_CONFIG_DIR=examples/simple/12-on-switch ./xtm.py --list --json | grep on_switch
```
