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

## The reported profile

`build` is what `expected.json` records, and reaching it needs `--all` — which
is the point worth taking away. **A bound profile cannot be selected on a
machine it does not match**, so on any host but a build host, `xtm build`
fails with "belongs to another machine" and `--all` is the only way in. This
example carries a `flags` file saying so, because the runner would otherwise
be unable to report on it at all.

There is no way to *preview* a bound profile as the machine it belongs to:
`--all` reaches it, but auto-selection cannot be exercised from elsewhere, so
the `match:` block itself is only really testable on the machine it names.

## Try it

```bash
XTM_CONFIG_DIR=examples/complex/02-fleet ./xtm.py --list-profiles
XTM_CONFIG_DIR=examples/complex/02-fleet ./xtm.py --all --list-profiles
XTM_CONFIG_DIR=examples/complex/02-fleet ./xtm.py --validate
```
