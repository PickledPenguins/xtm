# Configuration

Every key the config file accepts, what validation enforces, and how the YAML is read.

The file lives at `~/.config/xtm/config.yaml` and is created on first run. `--config` and
`XTM_CONFIG_DIR` change where it is read from; see [cli.md](cli.md).

## A complete example

```yaml
profiles:
  desk:
    match:                 # optional: bind this profile to a machine
      hostname: workstation*
    prefix: desk-          # optional: session names for this profile's slots
    on_switch: detach      # optional: leave | detach | kill
    title: "{profile}: {slot}"   # optional: window title template
    stack:                 # default slot for sessions with no position
      x: 40
      y: 40
      width: 900
      height: 650
      offset_x: 40         # diagonal offset applied per stacked window
      offset_y: 40
    sessions:
      work1:
        x: 0
        y: 0
        width: 960
        height: 1180
      logs:
        x: 960
        y: 0
        width: 960
        height: 580
        command: journalctl -f
        title: Logs          # optional: overrides the profile template
      notes:
        session: notes       # explicit: never prefixed, so it can be shared
        x: 0
        y: 1180
        width: 960
        height: 400
      cluster:
        x: 960
        y: 600
        width: 960
        height: 580
        command: ssh headnode
        cwd: ~/projects
        xterm_args: [-bg, "#2a1a1a"]

  laptop:
    match: {hostname: [thinkpad, thinkpad-dock]}
    stack: {x: 20, y: 20, width: 700, height: 900, offset_x: 30, offset_y: 30}
    sessions:
      work1: {x: 0, y: 0, width: 1000, height: 1400}
```

Both the indented and the compact `{…}` form are accepted. Comments may appear on their own
line or at the end of a line.

## Session keys

| Key | Type | Required | Meaning |
|---|---|---|---|
| `x`, `y` | number | Yes | Window position in pixels. May be negative for monitors left of or above the origin. |
| `width`, `height` | number | Yes | Window size in pixels. Must be positive. |
| `command` | string | No | Shell command run in the session when it is **created**. Ignored when attaching to a session that already exists. It replaces the pane's shell: when it exits the pane closes, unless it failed and tmux is 3.2 or newer, in which case the pane stays on screen with the exit status. |
| `cwd` | string | No | Working directory for a newly created session. `~` is expanded. |
| `xterm_args` | list of strings | No | Extra arguments appended to the `xterm` command line, for fonts, colors, scrollback and so on. |
| `session` | string | No | Tmux session name, used verbatim and **never prefixed**. This is how two profiles deliberately share one session. |
| `title` | string | No | Window title for this slot. Overrides the profile's template. |

## Profile keys

| Key | Type | Required | Meaning |
|---|---|---|---|
| `stack` | mapping | No | Placement for sessions with no recorded position. Needs `x`, `y`, `width`, `height`, `offset_x`, `offset_y`. Defaults are supplied if absent. |
| `sessions` | mapping | No | Slot name to slot settings. Defaults to empty. |
| `match` | mapping | No | Machine binding: a `hostname` glob, or a list of them. Present means machine-specific; absent means global. |
| `prefix` | string | No | Prepended to a slot name to form its session name. Absent means no prefix, so the session is named after the slot. |
| `on_switch` | string | No | What happens to this profile's windows when switching away: `leave`, `detach` or `kill`. Defaults to `detach`. |
| `title` | string | No | Window title template for every slot, using `{slot}`, `{session}` and `{profile}`. Defaults to `xtm:{session}`. |

Session and profile names may contain only letters, digits, `.`, `_` and `-`.

## Machine binding

```yaml
desk:
  match:
    hostname: workstation*          # one glob

build:
  match:
    hostname: [build-1, build-2]    # or several
```

`hostname` accepts a single shell glob or a list of them, and the profile is bound to any
machine matching at least one. That lets one profile serve several machines with the same
screen layout without duplicating it. What binding *means* is in
[concepts.md](concepts.md).

A `match` block that is malformed — an unknown key, an empty or non-string hostname —
leaves the profile **visible** rather than hidden, so the validation error is what you see
instead of the profile silently vanishing.

## Missing data

Handled uniformly. A profile with no `stack` gets the built-in default, a profile with no
`sessions` gets an empty mapping, and any geometry field that is absent, non-numeric,
boolean, infinite or NaN is a validation error naming the profile, session and field.

Validation runs on load, and also before any command writes the file: an edit that would
leave a profile unreadable is refused and the profile left untouched, so no command can
write a config that the next command cannot load.

## YAML handling

The config is YAML, read and written with PyYAML's safe loader and dumper. PyYAML is
required; see [guide.md](guide.md#installing). Everything YAML allows is available:
block and flow style, comments, a leading `---`, and anchors with merge keys for sharing
settings between slots (see [moderate/03](../examples/moderate/03-shared-sizes/)):

```yaml
sizes:
  quarter: &quarter {width: 960, height: 540}
profiles:
  grid:
    sessions:
      editor: {<<: *quarter, x: 0, y: 0}
      wide:   {<<: *quarter, x: 960, y: 0, width: 1200}   # a key beside the merge wins
```

A top-level key other than `profiles` is ignored, which makes it a place to keep anchors.

### Keys are names

Every key is read as the text written. YAML would read the key `on:` as the boolean
`true`, `0755:` as the number 493 and `~:` as null; xtm keeps `on`, `0755` and `~`, so a
profile or slot is always called what it says. The name rules then apply: profile and slot
names may contain only letters, digits, `.`, `_` and `-`.

A key written twice in one mapping is refused, with its line and column. YAML would keep
the last value and drop the first without a word, so a slot pasted in twice would
silently lose a position. A key written beside a merge key is not a duplicate: it
overrides the merged value, as YAML intends.

### Values mean what YAML says

Values are typed the way every YAML reader types them, which matters in two places:

| Written as | YAML reads | When text was meant |
|---|---|---|
| `yes`, `no`, `on`, `off`, `true`, `false` | a boolean | quote it: `title: "yes"` |
| `0755` | the octal number 493 | quote it, or write a decimal number |
| `1:30` | the number 90 | quote it |
| `2026-01-01` | a date | quote it |
| `~`, `null`, nothing at all | null | quote it |
| `"900"` | the text 900 | for a position or size, remove the quotes |

A field that must be text (`command`, `cwd`, `title`, `session`, `prefix`) and was read as
something else is refused with the field, what YAML read, and the advice to quote it. A
geometry field that is not a number is refused the same way. Entries in `xterm_args` may be
numbers (`-fs, 11`); they are passed to xterm as text.

xtm's own writer quotes anything YAML would read as something other than the text it is,
so a config xtm saved always reads back unchanged.

### Errors

A file YAML cannot parse — a tab in the indentation, an unclosed `{`, a list item where a
key belongs — is reported on one line with the line and column of the problem:

```
xtm: error: ~/.config/xtm/config.yaml line 4, column 1: found character that cannot start any token (while scanning for the next token)
```

## What a config rewrite changes

Any command that writes the config — `--set`, `--update-profile`, `--delete-session`,
`--make-global` and the profile management commands — rewrites the whole file. Two things
do not survive that:

- **Flow style becomes block style.** `work1: {x: 0, y: 0}` is read correctly but written
  back expanded over four lines. The values and their meaning are identical; only the
  layout changes.
- **Comments are dropped.** PyYAML does not preserve them.
- **Anchors and merge keys are expanded.** A slot built from `<<: *size` is written out
  with the size in full, and the anchor is gone.

All three are worth knowing before hand-formatting a config, and none affects reading.

## Concurrency and durability

Config writes are atomic: content is written to a temporary file in the same directory,
flushed, `fsync`ed, then renamed into place, with the parent directory synced afterwards. A
crash or a full disk leaves the old config intact rather than a truncated one.

Atomicity alone does not prevent a *lost update*, where two simultaneous `--update-profile`
runs each load the same starting config and the second write discards the first one's
changes. Commands that modify the config take an exclusive `flock` on a sibling
`config.yaml.lock` file for the whole read-modify-write cycle. A separate lock file is used
because the atomic rename replaces the config file's inode, which would detach a lock held
on it directly. If locking is unavailable, the run proceeds unlocked rather than failing.
