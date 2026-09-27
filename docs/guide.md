# Guide

Workflows, in the order a user meets them. The vocabulary is in
[concepts.md](concepts.md); every flag is in [cli.md](cli.md).

## Installing

```bash
./install.sh                 # installs to ~/bin by default
./install.sh --prefix /usr/local/bin
./install.sh --completion    # also install the bash completion file
./install.sh --uninstall     # remove a previous installation
```

| Option | Argument | Effect |
|---|---|---|
| `--prefix` | `DIR` | Install into `DIR` (default `$HOME/bin`). |
| `--completion` | — | Also install the bash completion file. |
| `--completion-dir` | `DIR` | Completion target (default `$HOME/.local/share/bash-completion/completions`). Implies `--completion`. |
| `--uninstall` | — | Remove the installed program and completion file. Configuration and state are left in place. |
| `-h`, `--help` | — | Show usage. |

The installer verifies that `python3` is present and at least 3.6 before copying anything,
copies `src/xtm.py` to `<prefix>/xtm`, makes it executable, and warns without failing if the
target directory is not on `PATH`. It needs only a POSIX shell and no network access.

Manual installation is equivalent, and is the point of the single-file design:

```bash
mkdir -p ~/bin
cp src/xtm.py ~/bin/xtm
chmod +x ~/bin/xtm
xtm --help
```

## First run

```bash
xtm                          # status of the current profile (default action)
xtm desk                     # switch to the "desk" profile and show its status
xtm desk --reset-all         # switch, then open/position everything
xtm --reset-all              # same, using whichever profile is current
```

Naming a profile is the most common thing xtm is asked to do, so it needs no flag: `xtm
desk` is exactly `xtm --profile desk`, and the selection is remembered until changed.
Supplying both spellings at once is a usage error rather than one silently winning.

On first run a configuration is created at `~/.config/xtm/config.yaml` containing the
reserved `default` profile.

## Everyday workflows

Sit down at a machine and restore everything:

```bash
xtm --profile desk --reset-all
```

Snap windows back after dragging them around:

```bash
xtm --reset            # named sessions only
xtm --reset-all        # named sessions, and re-stack strays
```

Save a new arrangement:

```bash
xtm --update-profile           # overwrite the current profile
xtm --new-profile desk-v2      # or capture it as a new profile
```

Adjust a single window without opening an editor:

```bash
xtm --set logs 960,0,960,580
xtm --reset
```

Jump to a terminal by name, and close one when done:

```bash
xtm --focus cluster
xtm --close cluster
```

Preview a change before making it:

```bash
xtm --dry-run --reset-all
```

## Working on xtm itself

```bash
tests/run-tests.sh --install-hooks   # once: the fast suite runs before every commit
tests/run-tests.sh --fast            # ~25s, what the hook runs
tests/run-tests.sh --long            # integration, races and every example
make check                             # everything, plus a clean-tree assertion
```

The suite splits on whether a test is cheap enough to run before every commit; the hook
runs the fast half and the linter, and `git commit --no-verify` bypasses it.
[testing.md](testing.md) has what each half covers, and
[architecture.md](architecture.md) has where a change goes.

## Task recipes

| Task | Command |
|---|---|
| Restore the whole desktop | `xtm --reset-all` |
| Restore without touching unrelated terminals | `xtm --reset` |
| Set up a new machine's layout by hand | `xtm --new-profile thismachine`, then `xtm --set …` per window |
| Switch profile | `xtm desk` |
| Bind a profile to one machine | Add `match: {hostname: …}` to it |
| Bind one profile to several machines | `match: {hostname: [box-a, box-b]}` |
| Make a machine-specific profile universal | `xtm --make-global desk` |
| See profiles belonging to other machines | `xtm --all --list-profiles` |
| See what a reset would do first | `xtm --dry-run --reset-all --debug` |
| Find out why a window lands in the wrong place | `xtm --reset --verify --debug --log-file /tmp/xtm.log` |
| Save the layout after rearranging by hand | `xtm --update-profile` |
| Copy a layout to a second machine | `xtm --copy-profile desk desk-remote`, then edit positions |
| Script over the session list | `xtm --list --json --quiet \| jq …` |
| Tidy the screen, keep everything running | `xtm --close-all --detach-mode` |
| Tear down the day's work | `xtm --close-all` |
| Clean up sessions left by other profiles | `xtm --close-all --all` |
| See every profile and what is running | `xtm --list-profiles --verbose` |
| Share one session between two profiles | Give the slot the same `session:` in both |
| Switch without closing the old windows | `xtm lab --on-switch leave` |
| Adopt a window opened outside the profile | `xtm --update-profile --capture-new` |
| Recover from a broken config | `xtm --validate`, then `xtm --edit` |
| Work with a throwaway config | `xtm --config /tmp/test.yaml --state-dir /tmp/state --list` |

## Switching profiles

Switching away from a profile disposes of its windows according to its `on_switch` setting:

| Mode | Effect |
|---|---|
| `leave` | Windows stay exactly as they are. |
| `detach` | **Default.** Each window closes, but its session, tabs, panes and running jobs stay alive. Reattaching restores everything. |
| `kill` | Sessions are destroyed, taking their tabs and panes with them. |

Sessions the *new* profile also claims are left attached rather than closed and immediately
reopened, so a shared session simply moves to its new slot. `--on-switch MODE` overrides the
setting for one run, which is the escape hatch when the default would close something you
wanted to keep.

## Sessions with tabs and splits

xtm works one level above tmux's internals. It issues five tmux commands —
`new-session`, `list-sessions`, `list-clients`, `kill-session` and `detach-client` — and
never touches windows or panes inside a session. Four consequences:

- **`--update-profile` is pane-agnostic.** It captures four numbers per slot. A session
  split into six panes is saved exactly like a session with one.
- **Panes survive `--reset`.** The tmux server keeps running, so a session reattaches with
  its tabs, panes, history and jobs intact — even across the X session restarting.
- **`--close` destroys them.** Killing a session takes every tab and pane with it.
  `--detach` is the non-destructive alternative.
- **Resizing reflows them.** Placement sends a resize before a move, so tmux receives a
  `SIGWINCH` and reflows the panes. Nothing is lost, but a hand-tuned split shifts if the
  new slot is a different size.

## The current profile as an environment variable

A child process cannot set an environment variable in its parent shell, so a live
`$XTM_PROFILE` is not something xtm can provide. Instead `--profile NAME` persists the
choice to a per-machine state file and `--current-profile` reads it back, which behaves like
a persistent environment variable across separate invocations.

For a real environment variable as well, wrap the command in `~/.bashrc`:

```bash
xtm() {
    command xtm "$@"
    export XTM_PROFILE="$(command xtm --current-profile --quiet 2>/dev/null)"
}
```

## Adopting the newer profile keys

Existing configs keep working unchanged, and a profile with no `prefix` behaves exactly as
it always did: slot names are used as session names directly. Two things to be aware of
when adopting the keys added in 0.5:

- **Adding a `prefix:` renames a profile's sessions.** Sessions already running under the
  old names match no slot afterwards, so they appear as strays until closed or reattached.
  Either close them first, or give the affected slots an explicit `session:` naming what is
  already running.
- **`on_switch` defaults to `detach`**, so the first profile switch after upgrading closes
  the previous profile's windows. Nothing is destroyed — every session, tab, pane and job
  stays alive — but set `on_switch: leave` on a profile to keep the older behaviour.
