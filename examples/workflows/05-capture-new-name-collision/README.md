# workflows/05 — capturing a stray session whose name is a slot's

Level: **workflow**. `--update-profile --capture-new` when a stray session
has the same name as a configured slot.

- **Config:** `desk`, prefix `desk-`, slot `work1` (session `desk-work1`,
  with `command` and `cwd`).
- **Script:** `xtm desk`; a stray session `work1` appears; `xtm -u
  --capture-new`.
- **Expected:** a warning, and the stray session recorded as slot `work1-2`
  with `session: work1`. Slot `work1` keeps its `command` and `cwd`. Before
  this was fixed, the capture overwrote the slot.

Runs against the fake tools, not a real X server; see `examples/README.md`.
