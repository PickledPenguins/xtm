# Concepts

The vocabulary the rest of the documentation, the tool's own messages and the source all
use. Read this once and the other documents stop needing to re-explain themselves.

## Slot, session, tab

Three words are used precisely, because "window" otherwise means two different things:

| Term | Meaning |
|---|---|
| **slot** | A position on screen, and the key of an entry under `sessions:` in a profile. |
| **session** | The tmux session attached to a slot. Its name comes from the profile's `prefix` plus the slot name, or from an explicit `session:` on the slot. |
| **tab** and **pane** | tmux windows and splits *inside* a session. `xtm` never touches these. |

A slot is where a terminal sits; a session is what is running in it.

## Profile

A profile is a named screen layout: a set of slots, and the settings that apply to all of
them. Profiles live in one config file, and one of them is *effective* for any given
invocation — chosen explicitly, remembered from last time, or matched to this machine.

Every config has a reserved profile named `default`. It is created with the config,
recreated if it goes missing, and cannot be deleted, renamed, renamed onto or copied onto.
It is always global and may not carry a `match` block. Its contents are ordinary and fully
editable.

The point of it is stability: resolution ends at a profile guaranteed to exist under a
fixed name, so the fallback cannot silently change when the config is edited or reordered.

## What xtm does

Three things:

1. **Opens** terminals: one `xterm` per named `tmux` session, optionally running a chosen
   command in a chosen directory.
2. **Places** them: moves and resizes each window to the position recorded for it in the
   effective profile.
3. **Records** layouts: reads the current on-screen positions back and saves them into a
   profile.

It is deliberately *not* a tmux session manager. It does not define windows, panes or
splits inside a session; it manages the desktop-level arrangement of terminals and leaves
what happens inside each one to tmux.

## Ownership: prefix and session

By default a profile **owns** its sessions. Slot `work1` in a profile with `prefix: desk-`
is the session `desk-work1`, and the same slot name in a profile prefixed `lab-` is a
different session entirely. Two profiles therefore cannot collide by accident, and
switching between them is unambiguous.

A slot with an explicit `session:` is the exception. That name is used verbatim and is
never prefixed, so two profiles that both write `session: notes` land on the same tmux
session:

```yaml
desk:
  prefix: desk-
  sessions:
    work1: {x: 0, y: 0, width: 960, height: 1180}      # -> desk-work1
    notes: {session: notes, x: 960, y: 0, ...}         # -> notes

lab:
  prefix: lab-
  sessions:
    work1: {x: 0, y: 0, width: 900, height: 700}       # -> lab-work1
    notes: {session: notes, x: 900, y: 0, ...}         # -> notes, the same one
```

The rule in one sentence: **profile-owned sessions come and go with the profile; explicitly
named sessions persist across switches.**

Two slots in one profile resolving to the same session is a validation error, since they
would fight over the same window on every reset.

## Machine binding

A profile is **machine-specific** when it carries a `match` block and **global** when it
does not. There is one mechanism, not two: the `match` block *is* the binding.

Binding has two consequences:

- **Visibility.** A profile bound to another machine is hidden: it does not appear in
  `--list-profiles` and cannot be selected by name. `--all` reveals it for listing and for
  management commands, so a profile is never stranded somewhere you cannot reach it.
  `--validate` ignores visibility entirely — a config broken for another machine is still
  broken.
- **Auto-selection.** When nothing has been chosen explicitly and nothing is remembered —
  or under `--auto` — profiles are examined in sorted name order and the first whose
  hostname fits is used. A global profile is never auto-selected: being available
  everywhere is not evidence that it belongs *here*.

Matching is case-sensitive and compares against the short hostname, so
`workstation.corp.example.com` is matched as `workstation`.

See [configuration.md](configuration.md) for the `match` syntax and
[cli.md](cli.md) for `--all`, `--auto` and `--make-global`.

## Stray sessions

A **stray** is an attached tmux session that matches no slot in the effective profile. It
may belong to another profile — the status views say so when it does — or to nothing at
all.

`--reset` ignores strays. `--reset-all` also stacks them, diagonally, from the profile's
`stack` position. Nothing ever kills a stray that belongs to no profile.

## Effective profile

The profile an invocation acts on, resolved in this order:

1. `--profile NAME`, or the positional `xtm NAME`, which also remembers the choice.
2. The saved current profile for this machine, unless `--auto` was given.
3. The first profile whose `match` block fits this machine, in sorted name order.
4. The reserved `default` profile.

Only profiles visible on this machine are eligible, unless `--all` was given. The `*` in
`--list-profiles` marks the effective profile, which is why it answers "which profile am I
about to affect" rather than "which one is saved".
