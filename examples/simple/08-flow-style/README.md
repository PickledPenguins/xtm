# simple/08 — Flow style

Level: **simple**. One line per profile, for a config you edit rather than generate.

## What it shows

- **Flow style (`{…}`) and block style mean exactly the same thing.**
- **A space after every colon is required.** `{x:0}` is not `{x: 0}`: YAML
  reads a colon as a separator only when a space follows it, so `{x:0}` is one
  key named `x:0` with no value, and xtm then finds no `x`.
- **A rewrite expands it.** Any command that saves the config — `--set`,
  `--update-profile`, the profile management commands — writes block style
  and drops comments. Flow style is fully supported as input; it is just not
  what comes back out.

## Try it

```bash
XTM_CONFIG_DIR=examples/simple/08-flow-style src/xtm.py --list
XTM_CONFIG_DIR=examples/simple/08-flow-style src/xtm.py --validate
```
