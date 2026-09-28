# complex/07 — everything in flow style

Level: **complex**. One line per profile, nested four deep, and legal.

## What is combined

- **Mappings inside mappings inside mappings**: profile → `sessions` → slot →
  geometry, all in `{…}`, plus a `[…]` list of xterm arguments inside that.
- **A quoted value inside a nested flow mapping** (`"#101018"`), which is
  where the two YAML readers most easily disagree, and a quoted list item
  containing no colon to sit beside the ones that do.
- **`match: {hostname: "*"}`** — a glob that matches everything, written
  inline.

## Why it exists

Flow style is fully supported as input and is what a generated config tends
to look like, but every other example writes block style. This is the one that
proves the compact spelling means exactly the same thing, and the one to
compare against if a config produced by a script stops loading.

**A rewrite expands it.** Run `--set` against this config and it comes back
block-style with the comments gone. That is not a bug; it is the cost of the
tool owning the file.

## Try it

```bash
XTM_CONFIG_DIR=examples/complex/07-deep-flow src/xtm.py --list
XTM_CONFIG_DIR=examples/complex/07-deep-flow src/xtm.py --validate
```
