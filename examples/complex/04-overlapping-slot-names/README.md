# complex/04 — the same slot names in three profiles

Level: **complex**. Collision, non-collision, and the migration case, in one
file.

## What is combined

- **`editor` and `shell` exist in all three profiles** and resolve to five
  different sessions: `a-editor`, `b-editor`, `a-shell`, `b-shell` — and
  plain `editor` and `shell` in `legacy`, which has no prefix.
- **`legacy` is what a pre-prefix config looks like**, and it still works: no
  prefix means the slot name is the session name. It also means `legacy` and
  a future unprefixed profile *would* collide, which is the reason prefixes
  exist.
- **`notes` deliberately collides**, and is the only one that does: both
  `alpha` and `beta` name `shared-notes`, so switching between them keeps
  that window and disposes of the rest.
- **Adding a prefix to `legacy` would rename its sessions.** Anything running
  under `editor` would become a stray until closed or given an explicit
  `session:` — the migration hazard, visible here rather than described.

## Try it

```bash
XTM_CONFIG_DIR=examples/complex/04-overlapping-slot-names src/xtm.py --list-profiles --verbose
XTM_CONFIG_DIR=examples/complex/04-overlapping-slot-names src/xtm.py beta --list
```
