# complex/06 — the quoting minefield

Level: **complex**. Nine values YAML would type as something other than text,
all quoted, in one profile that works.

## What is combined

Every spelling YAML reads as a number, a boolean or a date, used as a real
title rather than shown in a table: octal-looking (`0755`), sexagesimal
(`1:30`), a boolean (`yes`), a date (`2026-01-01`), digit separators
(`1_000`), scientific notation (`1e3`), a non-finite (`.inf`), a colon inside
a flow list item (a font name), and a command carrying backslashes.

## Why it is complex rather than simple

`simple/09` shows the rule with four values. This shows that quoting composes:
every one of them in one profile, each read back as exactly the text written.
Remove the quotes from a title and `--validate` names the slot, what YAML read
instead, and says to quote it.

## Try it

```bash
XTM_CONFIG_DIR=examples/complex/06-quoting-minefield src/xtm.py --validate
XTM_CONFIG_DIR=examples/complex/06-quoting-minefield src/xtm.py --list --json | grep title
```
