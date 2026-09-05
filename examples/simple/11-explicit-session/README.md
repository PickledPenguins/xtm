# simple/11 — an explicitly named session

Level: **simple**. The exception to the prefix, and the only way to share.

## What it shows

- **`session:` on a slot is used verbatim.** The profile's `prefix` does not
  apply to it, so this slot is the tmux session `notes`.
- **That is how two profiles share one session.** Give the slot the same
  `session:` in both, and switching between the profiles moves that window to
  its new position instead of closing and reopening it.
- The status view marks it `(shared, kept on profile switch)`, and `--list
  --json` reports `"shared": true` for it alone.

## Try it

```bash
XTM_CONFIG_DIR=examples/simple/11-explicit-session ./xtm.py --list
```
