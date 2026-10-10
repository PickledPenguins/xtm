# workflows/10 — a profile made from the windows already open

Level: **workflow**. `--new-profile` on a desktop set up by hand.

- **Script:** two attached sessions on screen (`mail`, `code`), one detached
  (`batch`); `xtm -n home`; `xtm -p home -l`.
- **Expected:** `home` gets two slots, each with its captured position and a
  `session:` key naming the existing session, and prefix `home-` for slots
  added later. The detached session has no window, so it is not captured.
  The listing shows both slots attached, with live positions matching.

Runs against the fake tools, not a real X server; see `examples/README.md`.
