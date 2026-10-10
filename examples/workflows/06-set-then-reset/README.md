# workflows/06 — recording a position, then applying it

Level: **workflow**. `--set` edits the config only; `--reset` moves the
window.

- **Config:** `desk` with one slot, `work`.
- **Commands:** `xtm desk`, `xtm -s work 200,100,1200,900`, `xtm -r`.
- **Expected:** `-s` reports the slot updated and leaves the window where it
  is; `-r` repositions `desk-work` to the new place.

Runs against the fake tools, not a real X server; see `examples/README.md`.
