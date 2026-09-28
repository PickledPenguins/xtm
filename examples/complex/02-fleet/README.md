# complex/02 — a fleet in one file

Level: **complex**. Four machine classes, three binding shapes, and one
session shared across two of them.

## What is combined

- **Every way of binding a profile**: a list mixing exact names with a glob
  (`build`), a glob alone (`desk`), an exact name (`headnode`), and no
  binding at all (`default`, which is therefore global and never
  auto-selected).
- **Three disposal modes across four profiles**, each applying to the profile
  being left: `build` kills, `desk` detaches, `headnode` leaves.
- **A notebook shared between two machine classes.** `notes` is explicit in
  `build` and `desk`, so on a machine that is both — one matching
  `builder-*` and `workstation-*` cannot exist, but a laptop that visits both
  configs can — it is the same tmux session either way.
- **Visibility as a feature.** On a desk, `--list-profiles` shows `desk` and
  `default`; the others are hidden until `--all`. `--validate` checks all of
  them regardless, because a config broken for the build hosts is still
  broken.

## Seeing it from the wrong machine

**A bound profile cannot be selected on a machine it does not match**: on any
host but a build host, `xtm build` reports that it belongs to another machine.
Two ways in, and they answer different questions:

```bash
xtm --all --list-profiles                     # what exists, including elsewhere
xtm --hostname build-3 --auto --list          # what build-3 would actually do
```

`--all` reveals a profile for listing and management. `--hostname` goes
further and runs *as* that machine, so the `match` blocks are exercised rather
than bypassed — which is the only way to check that the binding itself is
right. This example carries a `flags` file with `--all`, because the runner
reports on `build` by name from a machine that is not one.

## Try it

```bash
XTM_CONFIG_DIR=examples/complex/02-fleet src/xtm.py --list-profiles
XTM_CONFIG_DIR=examples/complex/02-fleet src/xtm.py --all --list-profiles
XTM_CONFIG_DIR=examples/complex/02-fleet src/xtm.py --validate
```
