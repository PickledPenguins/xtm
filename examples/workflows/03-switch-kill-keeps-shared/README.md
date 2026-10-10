# workflows/03 — switching with on_switch: kill, a shared session kept

Level: **workflow**. A profile that kills its sessions when left, and a
session both profiles name.

- **Config:** `desk` (`on_switch: kill`) with `desk-work` and `notes`; `lab`
  with `lab-bench` and `notes`.
- **Commands:** `xtm desk`, `xtm lab`.
- **Expected:** the switch kills `desk-work` only. `notes` is claimed by
  `lab`, so it is neither killed nor detached: it is repositioned into lab's
  slot, with its jobs and scrollback intact.

Runs against the fake tools, not a real X server; see `examples/README.md`.
