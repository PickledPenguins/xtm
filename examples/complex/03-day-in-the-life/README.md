# complex/03 — a day in the life

Level: **complex**. What a real desktop looks like when everything is in use
at once.

## What is combined

- **Eight slots across two monitors of different sizes**, with the geometry
  arithmetic that implies — the right monitor's slots start at x=1920 because
  the left one is that wide.
- **Four `command` slots and four without.** The commands are the ones people
  actually run: a log tail, a metrics poll, a database shell, a test watcher —
  each with the `exec bash` idiom or not, depending on whether the pane should
  survive the command.
- **`cwd` on the slots that need it**, and not on the ones that do not.
- **Two shared sessions**, `notes` and `scratch`, which persist across a
  switch to any other profile that names them.
- **Per-slot appearance** on the one window that is read rather than typed in.

## Why it is one example rather than eight

Each piece appears alone in `simple/`. What this shows is the shape of a real
config: which slots get commands, which get a working directory, which are
shared, and how the numbers relate to the monitors. It is the file to copy
when starting your own.

## Try it

```bash
XTM_CONFIG_DIR=examples/complex/03-day-in-the-life src/xtm.py --list
XTM_CONFIG_DIR=examples/complex/03-day-in-the-life src/xtm.py --list --json
```
