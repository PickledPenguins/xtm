# moderate/10 — three remote hosts and one local shell

Level: **moderate**. `command` doing the real work it was added for.

## What it shows

- **`ssh` as a slot command**, one per host, so a whole working set comes back
  with one `xtm --reset-all`.
- **`-t` on the remote commands.** Without it the remote side gets no tty and
  anything interactive misbehaves. `exec bash -l` keeps the pane alive after
  the initial command.
- **A local slot with `cwd` and no command**, because when a host is
  unreachable you still want a terminal that works.
- **What happens on a reset.** The commands run only when the session is
  *created*; reattaching leaves the existing ssh alone, which is what makes
  `--reset` safe to run repeatedly.

## Try it

```bash
XTM_CONFIG_DIR=examples/moderate/10-remote-work ./xtm.py --list
```
