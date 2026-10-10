# workflows/02 — switching profiles, windows detached

Level: **workflow**. The default `on_switch: detach` when leaving a profile.

- **Config:** `desk` (two slots) and `lab` (one), each with a prefix.
- **Commands:** `xtm desk`, `xtm lab`, `xtm -C`, `xtm -p desk -l`.
- **Expected:** switching to `lab` closes desk's two windows and opens
  `lab-bench`; desk's sessions keep running, detached. `-C` answers `lab`.
  Listing desk with `-p` shows both slots `detached` and does not switch
  back: the state still names `lab`.

Runs against the fake tools, not a real X server; see `examples/README.md`.
