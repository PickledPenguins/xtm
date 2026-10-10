# Future work

Ideas and open questions, in one place. This is a working list rather than a commitment:
nothing here is scheduled, and an item's presence means it has been thought about and
written down.

## Features not built

- **Fractional and monitor-relative geometry** (`x: 50%`, `monitor: HDMI-1`) derived from
  `xrandr`, so a profile survives a resolution change and is portable between machines.
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
  dependency is missing tests nothing. `tests/property/engine.py` is under sixty lines and runs
  everywhere the suite does. Revisit if the shrinking becomes the bottleneck in reading a
  failure.
- **A mutation tool** (`mutmut`, `cosmic-ray`) instead of the checked-in catalog. Same
  reasoning, plus one more: naming the modules that should kill each mutant keeps a full run
  to a couple of minutes, where a tool would spend most of its time on lines nobody would
  break.
- **Re-binding a global profile to a machine.** `--make-global` is one-way on purpose:
  re-binding would hide a profile that other machines may already be relying on. Adding a
  `match` block by hand is the supported route.
- **Declarative tmux windows and panes per slot** (`windows: {build: {command: ...}}`).
  Chosen instead on 2026-09-28: a `setup` shell command run once when the session is
  created. It covers every layout tmux can express without a second schema in the config,
  and without xtm turning into a tmux session manager. The declarative form would also need
  xtm to decide when an existing session's contents are "wrong", which it cannot know.
- **Keeping a built-in YAML reader for hosts without PyYAML.** Until 2026-09-27 xtm read
  its config with PyYAML when importable and with its own parser otherwise, behind a
  guard that refused every input the two would read differently. That guard was 580 of
  2605 executable lines, the subject of most review findings, and the cause of the
  config's odd refusals (`title: yes`, `0755`, `---`, merge keys). The owner uses YAML
  deliberately and always has PyYAML, so xtm now requires it and reads real YAML. What
  was given up: running on a host where PyYAML cannot be installed. Revisit only if such a
  host becomes a real target — and then prefer a stdlib format over a second YAML reader.
- **Superscript cross-references with a `## References` table per document**, the
  convention the documentation audit of 2026-10-09 proposed. Declined: it would touch all
  eleven documents in `docs/`, whose plain inline links already say where a subject
  lives, and its checker (`doc-refs`) is a tool of this machine, not of
  the repository, so CI could only skip the gate or carry a copy of it. Cross-document
  mentions stay plain inline links to the owning section. Revisit if the set grows to
  where a reader cannot tell which document owns a subject.

## Open questions

- **Whether the coverage floor should reach 98.** Held at 97 by the owner (2026-10-09)
  against 98.1% achieved, after the pushes from 95.4 to 97.4 and then to 98.1 that added
  the command-failure, validation-message, untidy-screen and reachable-branch tests. What
  is left is 23 statements and 27 partial branches of defensive code
  ([testing.md](testing.md#coverage) lists them) that needs the filesystem or a tool to
  break underneath a running process. A floor at 98.1 would leave no margin for a refactor
  that moves one branch. Revisit if a way appears to reach those paths through behavior
  rather than through mocking.
- **Whether `--current-profile` should check that the profile still exists.** `xtm -C`
  prints the saved name and exits 0 even when that profile was deleted on another machine
  sharing the config (`cmd_current_profile`, `src/xtm.py`), while `--list` stops with
  "Current profile ... no longer exists". Checking would make `-C` exit 1 there and change
  its output; leaving it keeps `-C` a raw read of the state file, which is what a script
  asking "what did I last select" may want. Raised 2026-10-09.
- **How runtime error messages are documented.** 88 of the 100 messages xtm can print
  (`die`, `logger.error`, `logger.warning`) appear in no document
  (`doc-errors src/xtm.py --call die --call logger.error --call logger.warning`), and no
  test keeps [troubleshooting.md](troubleshooting.md) in step with the code. Options:
  troubleshooting entries for the messages that leave a user stuck (the attach timeout,
  "already has an xtm window", "Command %r timed out", the capture-collision warning); an
  in-repository test that fails on an undocumented message, with an explicit exempt list;
  or both. From the documentation audit of 2026-10-10.
- **No release is tagged.** The README says a tag names each release, and the repository
  has none. Tagging `v0.5` (the version in the README header) at the commit the owner
  calls a release makes the sentence true; nothing in the code depends on it. From the
  documentation audit of 2026-10-10.
