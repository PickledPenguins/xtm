# workflows/08 — checking a profile before switching to it

Level: **workflow**. `--check` with `-p` naming another profile changes
nothing.

- **Config:** `desk` and `lab`.
- **Commands:** `xtm desk`, `xtm -p lab --check`, `xtm -C`.
- **Expected:** the check reports on `lab` (exit 0, one warning: the fakes
  report no monitors). desk's window is not detached, and `-C` still answers
  `desk`.

Runs against the fake tools, not a real X server; see `examples/README.md`.
