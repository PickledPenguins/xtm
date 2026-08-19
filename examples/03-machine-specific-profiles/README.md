# 03 — One config, several machines

A single config file synced across a laptop and a workstation, where each
machine gets the layout that fits its screen.

## What it shows

- **`match: {hostname: ...}` binds a profile to a machine.** A profile with a
  `match` block is machine-specific: it is hidden on every other host, so a
  laptop's layout does not clutter the workstation's `--list-profiles`. The
  pattern is a shell glob against the short hostname, so `thinkpad*` covers
  `thinkpad`, `thinkpad-2` and so on.
- **A list covers several machines.** `workstation` matches
  `[dev-box, dev-box-2, build-*]`, which is how one layout serves a group of
  identical machines without duplicating it.
- **Machine-specific profiles are auto-selected; global ones are not.**
  On a matching host, a bare `xtm` picks the matching profile. A global
  profile like `presentation` is never picked automatically — being
  available on every machine is no evidence that it belongs on *this* one —
  so you reach it by name.
- **`default` is reserved and must stay global.** It cannot carry a `match`
  block, because it is the last resort of resolution and has to exist
  everywhere.

## Try it

Profiles bound to other machines are hidden. `--all` reveals them:

```bash
XTM_CONFIG_DIR=examples/03-machine-specific-profiles ./xtm.py --list-profiles
XTM_CONFIG_DIR=examples/03-machine-specific-profiles ./xtm.py --list-profiles --all
```

The second command shows `laptop` and `workstation`; the first almost
certainly does not, because your hostname is unlikely to match either. That
is the feature working.

To see one anyway:

```bash
XTM_CONFIG_DIR=examples/03-machine-specific-profiles ./xtm.py --all laptop --list
```

## Note on this example's recorded output

`expected.json` records the `presentation` profile, not `laptop` or
`workstation`. It has to: `presentation` is the only one visible on every
machine, so it is the only one whose report is the same wherever the test
runs. That constraint is itself the point of the example.
