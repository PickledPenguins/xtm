# moderate/05 — One layout, several machines

Level: **moderate**. Binding profiles to hosts without duplicating them.

## What it shows

- **`match: {hostname: [a, b]}` binds one profile to several machines.** The
  same layout serves both build boxes; there is no second copy to keep in
  step.
- **`hostname` accepts a glob.** `workstation-*` covers a fleet.
- **A profile with no `match` is global**, available everywhere — and never
  auto-selected, because being available everywhere says nothing about
  belonging here.
- **Profiles bound elsewhere are hidden.** On a machine called neither
  `build-1`, `build-2` nor `workstation-*`, `--list-profiles` shows only
  `default`; `--all` reveals the rest, and `--validate` checks all of them
  regardless, since a config broken for another machine is still broken.

## Try it

```bash
XTM_CONFIG_DIR=examples/moderate/05-two-machines-one-layout src/xtm.py --list-profiles
XTM_CONFIG_DIR=examples/moderate/05-two-machines-one-layout src/xtm.py --all --list-profiles
XTM_CONFIG_DIR=examples/moderate/05-two-machines-one-layout src/xtm.py --validate
```

The first two differ by exactly the profiles this machine is not.
