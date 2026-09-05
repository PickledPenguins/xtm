# xtm — xterm + tmux workspace manager

**Version 0.5**

`xtm` manages a set of `xterm` windows, each attached to a named `tmux` session, and
positions them on screen according to a named *profile* stored in a small YAML file. One
command restores an entire desktop layout; another saves the current layout back into the
profile.

Profiles are per machine and per monitor arrangement, so the same configuration file can
describe a laptop, a two-monitor desk and a remote workstation, and `xtm` picks the right
one automatically.

This README is an index. Each subject is owned by one document under [docs/](docs/), listed
at the bottom.

## What it does

Three things: **opens** terminals — one `xterm` per named `tmux` session, optionally running
a chosen command in a chosen directory; **places** them at the position recorded in the
profile; and **records** layouts by reading the current positions back into the profile.

It is deliberately *not* a tmux session manager: it does not define windows, panes or splits
inside a session. It manages the desktop-level arrangement of terminals and leaves what
happens inside each one to tmux.

Three words are used precisely throughout: a **slot** is a position on screen and the key of
an entry under `sessions:`; the **session** is the tmux session attached to it; **tabs** and
**panes** are tmux's business, inside a session, and xtm never touches them. See
[docs/concepts.md](docs/concepts.md).

## Requirements

Everything runs on the machine displaying the windows, using tools already present wherever
`xterm` and `tmux` are used.

| Component | Needed for | Required? |
|---|---|---|
| `python3` 3.6 or newer | everything | Yes |
| `xterm` | opening windows | Yes |
| `tmux` | sessions, and locating each window's tty | Yes |
| `xdotool`, `wmctrl` or `xwininfo` | reading window positions back | Only for `--update-profile`, `--new-profile`, `--focus`, `--verify`, and the live column of `--list` |
| `xdotool` or `wmctrl` | `--focus` specifically | `xwininfo` can query windows but cannot raise them |
| `xprop` | correcting captured positions for window decorations | Optional; skipped with a debug message when absent |
| `PyYAML` | nothing | Optional; used if importable, otherwise a built-in reader/writer is used |

No third-party Python packages are required, and nothing needs to be installed
system-wide — the whole tool is one file.

## Install

```bash
./install.sh                 # installs to ~/bin by default
./install.sh --prefix /usr/local/bin
./install.sh --completion    # also install the bash completion file
```

Or by hand, which is equivalent and is the point of the single-file design:

```bash
cp xtm.py ~/bin/xtm && chmod +x ~/bin/xtm
```

Options and prerequisites are in [docs/guide.md](docs/guide.md).

## Quick start

```bash
xtm                          # status of the current profile (default action)
xtm desk                     # switch to the "desk" profile and show its status
xtm desk --reset-all         # switch, then open and position everything
xtm --reset                  # snap the named windows back where they belong
xtm --update-profile         # save the current arrangement into the profile
```

On first run a configuration is created at `~/.config/xtm/config.yaml` containing a reserved
profile named `default`. A profile looks like this:

```yaml
profiles:
  desk:
    match: {hostname: workstation*}    # optional: bind to a machine
    prefix: desk-                      # optional: session names for this profile
    sessions:
      work1: {x: 0, y: 0, width: 960, height: 1180}
      logs:  {x: 960, y: 0, width: 960, height: 580, command: journalctl -f}
```

Then `xtm desk --reset-all` opens `desk-work1` and `desk-logs` and places them.

## Documentation

| Document | Owns |
|---|---|
| [docs/concepts.md](docs/concepts.md) | The vocabulary: slot, session, profile, ownership, machine binding, strays |
| [docs/guide.md](docs/guide.md) | Installing, everyday workflows, task recipes, switching profiles, upgrading |
| [docs/cli.md](docs/cli.md) | Every action, option, view, JSON key, exit status and environment variable |
| [docs/configuration.md](docs/configuration.md) | Every config key, validation, and the YAML rules both readers enforce |
| [docs/placement.md](docs/placement.md) | How a window is moved, identified and captured, and why positions need correcting |
| [docs/troubleshooting.md](docs/troubleshooting.md) | What breaks, what it looks like, and what to do |
| [docs/architecture.md](docs/architecture.md) | How xtm is built, the design decisions, and how to extend it |
| [docs/testing.md](docs/testing.md) | Running the suite, what each part covers, coverage |
| [docs/future-work.md](docs/future-work.md) | Planned work, and decisions taken not to build something |
| [examples/](examples/) | Forty worked configurations at three levels, plus eight meant to fail. They run as tests |

The commit log is the release history; a tag names each release.
