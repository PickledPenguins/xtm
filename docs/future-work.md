# Future work

Ideas and open questions, in one place. This is a working list rather than a commitment:
nothing here is scheduled, and an item's presence means it has been thought about and
written down.

## Features not built

- **Fractional and monitor-relative geometry** (`x: 50%`, `monitor: HDMI-1`) derived from
  `xrandr`, so a profile survives a resolution change and is portable between machines.
- **Per-session tmux window and pane layouts**, which would make xtm a full workspace
  manager rather than a placement tool.
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
- **Hypothesis for the property tests.** It is the obvious choice and would give real
  shrinking, a corpus and statistics. It is absent on every interpreter here, and xtm's
  premise is running where nothing can be installed — a property test that skips because a
  dependency is missing tests nothing. `tests/property/engine.py` is forty lines and runs
  everywhere the suite does. Revisit if the shrinking becomes the bottleneck in reading a
  failure.
- **A mutation tool** (`mutmut`, `cosmic-ray`) instead of the checked-in catalog. Same
  reasoning, plus one more: naming the modules that should kill each mutant keeps a full run
  to a couple of minutes, where a tool would spend most of its time on lines nobody would
  break.
- **Re-binding a global profile to a machine.** `--make-global` is one-way on purpose:
  re-binding would hide a profile that other machines may already be relying on. Adding a
  `match` block by hand is the supported route.
- **Keeping a built-in YAML reader for hosts without PyYAML.** Until 2026-09-27 xtm read
  its config with PyYAML when importable and with its own parser otherwise, behind a
  guard that refused every input the two would read differently. That guard was 580 of
  2605 executable lines, the subject of most review findings, and the cause of the
  config's odd refusals (`title: yes`, `0755`, `---`, merge keys). The owner uses YAML
  deliberately and always has PyYAML, so xtm now requires it and reads real YAML. What
  was given up: running on a host where PyYAML cannot be installed. Revisit only if such a
  host becomes a real target — and then prefer a stdlib format over a second YAML reader.
## Open questions

- **Whether the coverage floor should reach 98.** Settled for now at 97 against 97.4
  achieved, after a deliberate push from 95.4 that added the command-failure, validation-
  message and untidy-screen tests. What is left is 39 statements and 37 partial branches of
  error handling that needs the filesystem to break underneath a running process, plus the
  `fcntl` import fallback. Reaching 98 from there means asserting on internals.
  Revisit if a way appears to reach those paths through behavior rather than through
  mocking.
