# moderate/13 — quoting inside a working config

Level: **moderate**. The four quoting rules, applied where they actually come
up rather than in isolation.

## What it shows

- **A font name with a colon** must be quoted: `"DejaVu Sans Mono:size=11"`.
  Unquoted, YAML reads the list item as a nested mapping.
- **A command with a backslash** goes in single quotes, which are literal text
  in YAML. Double quotes process escapes, and xtm implements only four of
  them, so it refuses the rest rather than reading a command two ways.
- **A title that looks like a number** — `"0755"` — is octal 493 to PyYAML and
  decimal 755 to the built-in reader unless quoted.
- **A config xtm saved always reloads unchanged**, because its writer quotes
  anything that would need it. These rules bite on hand-written configs.

## Try it

```bash
XTM_CONFIG_DIR=examples/moderate/13-mixed-quoting ./xtm.py --validate
```

Remove the quotes around `"0755"` and run it again to see the refusal.
