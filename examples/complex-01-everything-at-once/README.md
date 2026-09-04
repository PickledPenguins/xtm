# complex-01 — everything at once

Level: **complex**. Every mechanism xtm has, in one config, interacting.

Each part of this is shown on its own in an earlier example. None of those
examples can show what this one does: that the mechanisms compose, and compose
the way the documentation says they do.

## What is combined

- **Two ownership models in one file.** `desk` prefixes its slots; `notes`
  opts out with an explicit `session:` and is shared with `lab`. A switch
  between the two profiles must keep that one session and dispose of the rest.
- **Two disposal modes.** `desk` detaches on switch, `lab` kills. Which one
  applies depends on the profile being *left*, not the one being entered.
- **Machine binding of every shape.** `lab` is bound to a list mixing exact
  names with a glob, `remote` to one exact name, and `desk` and `default` carry
  no `match` at all — global, and for that reason never auto-selected. `desk`
  is deliberately the global one: a bound profile is invisible on every machine
  but its own, so an example that reported on one would report nothing here.
- **Every slot key.** Geometry alone; geometry with `command`, `cwd`,
  `xterm_args` and a per-slot `title`; and a slot whose session is named
  outright.
- **The quoting rules, inside a working config rather than in isolation.** A
  fontconfig name containing a colon, a title that YAML would otherwise read as
  octal, and a command carrying a backslash in single quotes. Any of the three
  written unquoted would mean two different things on two machines, so a config
  that uses all of them is the test that the guard does not fire on legitimate
  ones.
- **Two profiles claiming the same slot name.** `editor` exists in both, and
  resolves to `desk-editor` and `lab-editor` — the collision that prefixes
  exist to prevent.

## Expected

`expected.json` records the `--list` report for the `desk` profile: four slots,
`desk-` applied to three of them and not to `notes`, the per-slot title
overriding the profile template for `logs` only, and `shared: true` on `notes`
alone.

## Try it

```bash
XTM_CONFIG_DIR=examples/complex-01-everything-at-once ./xtm.py --list
XTM_CONFIG_DIR=examples/complex-01-everything-at-once ./xtm.py --list-profiles --verbose
XTM_CONFIG_DIR=examples/complex-01-everything-at-once ./xtm.py --validate
XTM_CONFIG_DIR=examples/complex-01-everything-at-once ./xtm.py lab --dry-run
```

The last one shows what switching away from `desk` would do: three windows
detached, `notes` left alone because `lab` claims it too.
