# Future work

Ideas and open questions, in one place. This is a working list rather than a commitment:
nothing here is scheduled, and an item's presence means it has been thought about and
written down.

## Features not built

- **Fractional and monitor-relative geometry** (`x: 50%`, `monitor: HDMI-1`) derived from
  `xrandr`, so a profile survives a resolution change and is portable between machines.
- **Per-session tmux window and pane layouts**, which would make xtm a full workspace
  manager rather than a placement tool.
- **`--restore`**, reattaching windows to profile sessions that already exist in tmux after
  an X restart.
- **A placement backend using `xdotool`/`wmctrl`** instead of escape sequences, which would
  remove the dependency on `allowWindowOps` and open the door to terminal emulators other
  than xterm.
- **Z-order control**, so overlapping windows come out in a defined stacking order.
- **Renaming the `match:` block to `machine:`**, now that it is hostname-only and its purpose
  is binding rather than general matching.
- **A tmux server per profile** (`tmux -L <name>`), for hard isolation between contexts
  rather than the namespacing a `prefix` provides.
- **Per-slot tmux tab names** (`new-session -n`), distinct from the window title.
- **Further ways to bind a profile to a context** beyond the hostname, once real-world use
  shows which are actually wanted.

## Considered and deliberately not done

- **Splitting `xtm.py` into a package.** The tool must be copyable to a machine where
  nothing can be installed, and `install.sh` copies exactly one file. The repository grows
  structure around the file instead.
- **Threading a context parameter through every function** rather than the module-level
  `CONTEXT`. It would add an argument no call site reads to 126 signatures in exchange for a
  purity a single-invocation CLI gets nothing from. Revisit only if xtm ever needs two
  configurations live in one process.
- **A changelog.** The commit log is the release history and a tag names each release; a
  second record beside it would drift from the first.
- **Refusing a plain numeric key** such as `123:` in the config. PyYAML types it as an
  integer and the built-in reader as text, but `_normalize_config_keys` turns both into the
  same name, so there is nothing left to disagree about. The boolean and null spellings are
  refused, because those do not normalise to the same thing.
- **Re-binding a global profile to a machine.** `--make-global` is one-way on purpose:
  re-binding would hide a profile that other machines may already be relying on. Adding a
  `match` block by hand is the supported route.

## Open questions

- **Whether the parity guard should become a whitelist.** It is currently a blacklist of
  refused shapes, and two rounds of review found holes in it — each time in the seam between
  the pre-parse scan and the reader that follows it. A whitelist of accepted value shapes
  would close the class rather than the instance. The cost is that every legitimate value
  shape then has to be enumerated, and the last such enumeration took three passes to get
  right.
- **Whether the coverage floor should ratchet.** It sits at 95 against 95.4 achieved, which
  leaves about eleven uncovered units of slack — enough that a refactor does not fail the
  build before it reaches review, and small enough that a genuinely untested feature does.
  Raising it is worth doing as a deliberate push, with the tests that earn each step, rather
  than to match a number that drifted upward on its own.
