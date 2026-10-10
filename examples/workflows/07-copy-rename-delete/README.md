# workflows/07 — copying, renaming and deleting a profile

Level: **workflow**. Keeping a variant of a profile, then removing it.

- **Config:** `desk`.
- **Commands:** `xtm -p desk -C`, `--copy-profile desk desk-wide`, `-P`,
  `--rename-profile desk-wide wide`, `-P`, `--delete-profile wide`, `-P`.
- **Expected:** each step reports itself, and `-P` after each shows the
  profile list it left. `desk` stays current throughout, and the final
  config holds `desk` and the `default` xtm added on its first write.

Runs against the fake tools, not a real X server; see `examples/README.md`.
