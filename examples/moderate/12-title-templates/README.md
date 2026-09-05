# moderate/12 — title templates

Level: **moderate**. Three ways a window gets its title, in one profile.

## What it shows

- **The profile template** applies to every slot: `{profile}`, `{slot}` and
  `{session}` are substituted.
- **A slot may override it with a fixed string** — `logs` here.
- **Or with a template of its own** — `notes` uses `{session}`, which for a
  shared slot is the name it is shared under rather than the local slot name.
- **Titles are cosmetic.** xtm finds its windows by instance name
  (`xtm-<session>`), which a running program cannot change, so a custom title
  — or `set-titles on` in `.tmux.conf` — cannot make it lose track of one.

## Try it

```bash
XTM_CONFIG_DIR=examples/moderate/12-title-templates ./xtm.py --list --json | grep title
```
