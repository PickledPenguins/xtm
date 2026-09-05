# complex/06 — the quoting minefield

Level: **complex**. Nine values that mean two different things unquoted, all
in one profile that works.

## What is combined

Every ambiguity the guard knows about, used as a real value rather than shown
in a table: octal-looking (`0755`), sexagesimal (`1:30`), a YAML 1.1 boolean
(`yes`), a date (`2026-01-01`), digit separators (`1_000`), scientific
notation (`1e3`), a non-finite (`.inf`), a colon inside a flow list item
(a font name), and a command carrying backslashes.

## Why it is complex rather than simple

`simple/09` shows the rule with four values. This shows that the rules
compose: a config can use every one of them at once and still be a config
that loads identically with and without PyYAML. It is also the file to check
against when adding a rule to the guard — if a new rule breaks one of these
slots, the rule is too broad.

Remove the quotes from any one of them and `--validate` names the line, the
value, and the fix.

## Try it

```bash
XTM_CONFIG_DIR=examples/complex/06-quoting-minefield ./xtm.py --validate
XTM_CONFIG_DIR=examples/complex/06-quoting-minefield ./xtm.py --list --json | grep title
```
