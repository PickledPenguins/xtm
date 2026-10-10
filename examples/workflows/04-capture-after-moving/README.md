# workflows/04 — saving a layout arranged by hand

Level: **workflow**. `--update-profile` after the windows were moved.

- **Config:** `desk` with `work` (with `command` and `cwd`) and `logs`.
- **Script:** `xtm desk`, then both windows are moved (`move` lines), then
  `xtm -u`.
- **Expected:** both slots take the new positions; `work` keeps its
  `command` and `cwd`. The config had no `default` profile, so xtm adds it
  on this first write, and says so.

Runs against the fake tools, not a real X server; see `examples/README.md`.
