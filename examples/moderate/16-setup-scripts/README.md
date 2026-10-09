# moderate/16 — sessions built by a setup script

Level: **moderate**. `setup` giving each session its windows and splits.

## What it shows

- **`command` and `setup` together** — `editor` opens vim in its first pane,
  and `setup` adds a second tmux window named `shell`.
- **A line of tmux commands** — `build` splits its window and runs
  `make watch` in the new pane. YAML's folded `>-` keeps a long line
  readable; it is still one shell command.
- **A script** — `ops` calls a script, which gets `$XTM_SESSION` to target.
- **Once per session** — setup runs when xtm creates the session, in the
  slot's `cwd`. While the session keeps running, `xtm` only reattaches it, so
  nothing is built twice. After a reboot the sessions are new, and setup runs
  again.

A setup that fails is a warning: the window is already open.

## Try it

```bash
XTM_CONFIG_DIR=examples/moderate/16-setup-scripts src/xtm.py --list
```
