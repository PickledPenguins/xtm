# complex/05 — the switch matrix

Level: **complex**. Three disposal modes and two shared sessions, arranged so
that every combination happens.

## What is combined

Leaving `detacher` for `keeper`:

- `d-owned` is **detached** — owned by the profile being left, whose mode is
  `detach`.
- `sd` is **left alone** — the profile being entered claims it too, and a
  session the new profile wants is never disposed of.

Leaving `killer` for `keeper`:

- `x-owned` is **killed**, taking its panes with it.
- `sk` **survives**, for the same reason `sd` did.

Leaving `keeper` for either:

- **Nothing happens at all.** `leave` means leave.

## Why this is one example

The rule "a session the new profile also claims is left alone" only shows
itself when the profile being left would otherwise have destroyed it. That
needs three profiles, two shared sessions and three modes in one file — any
smaller arrangement demonstrates the mode but not the exception.

## Try it

```bash
XTM_CONFIG_DIR=examples/complex/05-switch-matrix src/xtm.py detacher --dry-run
XTM_CONFIG_DIR=examples/complex/05-switch-matrix src/xtm.py killer --dry-run
XTM_CONFIG_DIR=examples/complex/05-switch-matrix src/xtm.py keeper --dry-run
```

Each dry run names what it would do to the profile it is leaving.
