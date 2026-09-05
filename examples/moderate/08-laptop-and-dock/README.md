# moderate/08 — laptop and dock

Level: **moderate**. Two layouts for one machine, switched by hand.

## What it shows

- **Two profiles sharing one prefix.** Both use `m-`, so `m-editor` is the
  same session in both and simply moves when you switch. This is the pattern
  for "same work, different screen", as against two separate contexts.
- **`on_switch: leave` on the docked profile**, because undocking is not a
  reason to close anything — the windows vanish with the monitor and come
  back when you re-dock and reset.
- **Why there is no `match:` here.** Both arrangements are the same host, so
  the hostname cannot tell them apart. Machine binding is for different
  machines; this is `xtm docked` and `xtm mobile` by hand.

## Try it

```bash
XTM_CONFIG_DIR=examples/moderate/08-laptop-and-dock ./xtm.py docked --list
XTM_CONFIG_DIR=examples/moderate/08-laptop-and-dock ./xtm.py mobile --list
```
