# moderate/04 — Switching profiles

Level: **moderate**. Three profiles, one per disposal mode, and a session shared by two of them.

## What it shows

- **`on_switch` decides what happens to the profile you leave.** `detach`
  (the default) closes each window and leaves the session running; `leave`
  touches nothing; `kill` destroys the sessions.
- **A session the new profile also claims is never disposed of.** `work` and
  `reading` both name `session: notes`, so switching between them moves that
  one window to its new slot rather than closing and reopening it.
- **`--on-switch MODE` overrides the setting for one run**, which is the
  escape hatch when the default would close something you wanted to keep.

## Try it

```bash
XTM_CONFIG_DIR=examples/moderate/04-switching-profiles src/xtm.py --list-profiles --verbose
XTM_CONFIG_DIR=examples/moderate/04-switching-profiles src/xtm.py reading --dry-run
```

The dry run prints what switching would do to `work`'s windows without doing
any of it.
