# 15 — Every key at once

A reference config using every profile and slot key xtm accepts.

This is not advice. A real config sets a few of these; the point here is to
have one place where each key appears in context, and one example that fails
if a key is renamed or dropped.

## What it shows

Profile keys: `match`, `prefix`, `on_switch`, `title`, `stack`, `sessions`.

Slot keys: `x`, `y`, `width`, `height`, `session`, `title`, `command`, `cwd`,
`xterm_args`.

Two relationships worth reading twice:

- **`prefix` versus `session`.** `editor` becomes `full-editor` because the
  profile owns it. `notes` stays `notes`, because a slot naming its session
  outright opts out of ownership — which is how two profiles share one.
- **`title` on the profile versus on the slot.** The profile's template
  applies to every slot; `logs` overrides it outright.

## Try it

```bash
XTM_CONFIG_DIR=examples/15-everything ./xtm.py --list
XTM_CONFIG_DIR=examples/15-everything ./xtm.py --list --json
```
