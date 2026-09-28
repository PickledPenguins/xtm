# simple/09 — Quoting values

Level: **simple**. The shapes that need quotes, in one config.

## What it shows

- **`"0755"`** — unquoted, YAML reads the octal number 493.
- **`"yes"`** — unquoted, it is the boolean `true`.
- **`"1:30"`** — unquoted, YAML reads the number 90.
- **`'grep -e ^\d'`** — single quotes are literal text in YAML. Double quotes
  process escapes, and `\d` is not one YAML knows, so the double-quoted form
  is refused as invalid YAML.

Unquoted, the titles are refused with a message naming the slot, what YAML
read, and saying to quote it. xtm's own writer quotes anything that would need
it, so a config it saved always reloads unchanged.

## Try it

```bash
XTM_CONFIG_DIR=examples/simple/09-quoting-values src/xtm.py --validate
```

To see a refusal, drop the quotes around `"yes"` and run it again.
