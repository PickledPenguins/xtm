# 13 — Quoting values

The four shapes that need quotes, in one config.

## What it shows

- **`"0755"`** — unquoted, PyYAML reads octal 493 and the built-in reader
  decimal 755.
- **`"yes"`** — unquoted, it is the boolean `true` in YAML 1.1.
- **`"1:30"`** — unquoted, PyYAML reads sexagesimal 90.
- **`'grep -e ^\d'`** — single quotes are literal text in YAML. Double quotes
  process escapes, which the built-in reader does not implement, so xtm
  refuses `"…\d…"` rather than letting the two disagree about what the
  command is.

Every one of these is refused unquoted, with a message naming the line, the
value and the fix. Quoted, both readers agree — and xtm's own writer quotes
anything that would need it, so a config it saved always reloads unchanged.

## Try it

```bash
XTM_CONFIG_DIR=examples/13-quoting-values ./xtm.py --validate
```

To see a refusal, drop the quotes around `"yes"` and run it again.
