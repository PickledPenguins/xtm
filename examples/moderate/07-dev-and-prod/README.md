# moderate/07 — development and production

Level: **moderate**. Two profiles for two contexts, made hard to confuse.

## What it shows

- **A title template per profile**, so every window says which context it
  belongs to before you read what is in it.
- **Colour as a safety cue.** `prod` windows are red; `xterm_args` is
  per slot, so the pair is repeated deliberately rather than inherited.
- **Different disposal per profile.** `dev` detaches on switch and keeps its
  work; `prod` kills, so an ssh session to the gateway does not outlive the
  context you opened it in.
- **`cwd` and `command` together**, including the `exec bash` idiom that keeps
  a pane alive after the command finishes.

## Try it

```bash
XTM_CONFIG_DIR=examples/moderate/07-dev-and-prod ./xtm.py --list-profiles --verbose
XTM_CONFIG_DIR=examples/moderate/07-dev-and-prod ./xtm.py prod --dry-run
```
