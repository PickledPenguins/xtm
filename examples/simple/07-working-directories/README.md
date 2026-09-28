# simple/07 — Working directories

Level: **simple**. Starting each session somewhere useful, with something already running.

## What it shows

- **`cwd:` is where a newly created session starts.** `~` is expanded.
- **`command:` runs when the session is created**, and is ignored when
  attaching to one that already exists — the same rule tmux itself follows.
- **A command replaces the pane's shell.** When it exits, the pane closes.
  `bash -lc 'cmd; exec bash'` is the idiom for keeping the shell afterwards,
  and `build` shows it.
- **The command is run by a shell**, so pipes and `&&` work, and it deserves
  the same care as any line in a shell profile.

## Try it

```bash
XTM_CONFIG_DIR=examples/simple/07-working-directories src/xtm.py --list
```

The listing shows `command:` and `cwd:` for each slot that sets them.
