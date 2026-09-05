# simple/10 — a prefix

Level: **simple**. One key, and the ownership model it creates.

## What it shows

- **`prefix:` is prepended to every slot name** to form the tmux session name.
  `editor` here is the session `desk-editor`.
- **That is what makes a profile own its sessions.** Another profile with
  `prefix: lab-` can have its own `editor` and never touch this one.
- The slot name is what you type: `xtm --focus editor`, not `desk-editor` —
  though both work, because every command that takes a name accepts either.

## Try it

```bash
XTM_CONFIG_DIR=examples/simple/10-prefix ./xtm.py --list
```

The `session:` line under each slot is the resolved name.
