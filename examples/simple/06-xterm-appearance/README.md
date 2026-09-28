# simple/06 — xterm appearance

Level: **simple**. Giving one window a different font, and another a dark background.

## What it shows

- **`xterm_args:` is a list appended to the xterm command line**, verbatim.
  Anything xterm accepts works: `-fa`/`-fs` for a font, `-bg`/`-fg` for
  colors, `-sl` for scrollback.
- **A value containing a colon must be quoted.** `"Monospace:size=11"` is a
  fontconfig name; unquoted, YAML reads a colon as a key separator, and the
  two YAML backends would not agree on the result. xtm refuses the unquoted
  form rather than picking one reading.
- **A number that is really text should be quoted too**, which is why `"11"`
  and `"50000"` carry quotes here: xterm takes them as strings on its command
  line either way, and quoting says so.

## Try it

```bash
XTM_CONFIG_DIR=examples/simple/06-xterm-appearance src/xtm.py --validate
```
