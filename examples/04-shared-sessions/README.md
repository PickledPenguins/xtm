# 04 — Slots, prefixes and shared sessions

Two profiles that reuse the same slot names without fighting, plus one
session deliberately shared between them.

## The distinction this example exists to explain

A **slot** is a position on screen. A **session** is the tmux session
attached to it. Keeping them separate is what lets `dev` and `ops` both have
a slot called `editor` that means different things.

## What it shows

- **`prefix:` makes a profile own its sessions.** With `prefix: dev-`, slot
  `editor` becomes tmux session `dev-editor`. The `ops` profile's `editor`
  slot becomes `ops-editor`. Same slot name, different sessions, no
  collision — and closing the `dev` profile cannot touch `ops` work.
- **An explicit `session:` is used verbatim and never prefixed.** Both
  profiles give their `notes` slot `session: scratchpad`, so both attach to
  the *same* tmux session, in different places on screen. This is the
  supported way to share.
- **Sharing changes what a profile switch does.** With
  `on_switch: detach`, switching from `dev` to `ops` detaches the
  profile-owned windows (`dev-editor`, `dev-shell`) but leaves `scratchpad`
  attached, because the new profile also claims it. It is repositioned
  rather than closed and reopened. A long-running job in the shared session
  survives the switch untouched.
- **`--list` says so.** A shared session is marked
  `(shared, kept on profile switch)`, because being shared changes what
  closing a profile will do to it and that should never be invisible.

## Try it

```bash
XTM_CONFIG_DIR=examples/04-shared-sessions ./xtm.py dev --list
XTM_CONFIG_DIR=examples/04-shared-sessions ./xtm.py ops --list
```

Note that `dev`'s `editor` reports session `dev-editor` while `ops`'s
reports `ops-editor`, and that both report `scratchpad` for `notes`.

## Choosing between them

Use a `prefix` by default: it is the safe option, and it means a profile can
be closed without side effects. Reach for an explicit `session:` only when
you actually want one window's work to survive across profiles.
