# Configuration

Every key the config file accepts, what validation enforces, and the YAML rules that keep a
config meaning the same thing on every machine.

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

Both the indented and the compact `{…}` form are accepted, with or without PyYAML
installed. Comments may appear on their own line or at the end of a line.

## Session keys

| Key | Type | Required | Meaning |
|---|---|---|---|
| `x`, `y` | number | Yes | Window position in pixels. May be negative for monitors left of or above the origin. |
| `width`, `height` | number | Yes | Window size in pixels. Must be positive. |
| `command` | string | No | Shell command run in the session when it is **created**. Ignored when attaching to a session that already exists. |
| `cwd` | string | No | Working directory for a newly created session. `~` is expanded. |
| `xterm_args` | list of strings | No | Extra arguments appended to the `xterm` command line, for fonts, colours, scrollback and so on. |
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

Matching on `$DISPLAY`, supported in 0.3, was removed in 0.4; a config still using it fails
validation with an explanation rather than silently never matching.

## Missing data

Handled uniformly. A profile with no `stack` gets the built-in default, a profile with no
`sessions` gets an empty mapping, and any geometry field that is absent, non-numeric,
boolean, infinite or NaN is a validation error naming the profile, session and field.

Validation runs on load, and also before any command writes the file: an edit that would
leave a profile unreadable is refused and the profile left untouched, so no command can
write a config that the next command cannot load.

## YAML handling

PyYAML is used when it is importable and a built-in reader runs when it is not. **The
contract is that a config means the same thing either way**, and it is enforced before
either backend parses: input the two would read differently is refused, with a message
naming the line and the fix.

Two details about the backends themselves:

- `safe_dump(sort_keys=...)` arrived in PyYAML 5.1. On older releases, still shipped by
  long-lived enterprise distributions, xtm falls back to its own dumper rather than
  rewriting the config in alphabetical order on every save.
- The built-in reader and writer handle the full shape xtm uses: nested mappings,
  flow-style mappings and lists, quoted strings, inline comments, and quoting of values
  that would otherwise be read back as booleans, nulls or numbers.

### Values that must be quoted

PyYAML implements YAML 1.1, which reads several unquoted forms in ways the built-in parser
does not — silently, since both readings are valid values that pass validation.

| Written as | PyYAML reads | Built-in reads |
|---|---|---|
| `0755`, `007`, `08` | octal `493` | decimal `755` |
| `0x1f`, `0b101`, `0o755` | `31`, `5`, `493` | text |
| `1_000` | `1000` | text |
| `1:30`, `1:30:00` | `90`, `5400` | text |
| `1e3`, `1.0e3` | text | `1000.0` |
| `.inf`, `.nan` | infinity, not-a-number | text |
| `yes`, `no`, `on`, `off` | `true` / `false` | text |
| `2026-01-01` | a date object | text |

Each is reported with the value, the reason and the fix: write a plain decimal number if
you meant a number, or quote it (`"0755"`, `"yes"`) if you meant the literal text. xtm's own
writer quotes anything that would need it, so a config xtm saved always reloads unchanged.

`true`, `false`, `null` and `~` are read identically by both parsers as *values* and need no
quoting. As **keys** they do not: see below.

### Escapes and quoting

A **single-quoted** scalar is literal text in YAML: `'a\tb'` is four characters, backslash
included. A **double-quoted** scalar is the one quoted form YAML processes escapes in, and
four of them are implemented on both sides — `\"`, `\\`, `\n` and `\t`. Every other escape
PyYAML knows (`\u00e9`, `\x41`, `\0`) is refused, because the built-in reader would hand
back the text and the two would disagree in silence.

`\n` and `\t` are implemented because xtm's own writer needs them: a value carrying a
newline or a tab is written escaped, since writing it raw would split the line that the
reader then has to parse.

Every **other** control character — a carriage return, a NUL, a bell — is refused outright,
quoted or not. Neither side can write one back safely: the built-in reader takes a raw one
in its stride and PyYAML refuses the file, so a config carrying one would load on half the
machines it was copied to.

A key is quoted text too, and carries the same escapes with the same rules.

### Keys

A key is a name, and xtm looks every name up as text. PyYAML types a key the way it types a
value, so some spellings would name different things on two machines:

| Written as | Why it is refused |
|---|---|
| `yes:`, `no:`, `on:`, `off:` | The YAML 1.1 booleans, which normalise to `True`/`False` under PyYAML and to the word itself without it |
| `~:`, `null:`, `true:`, `false:` | Null and boolean spellings, for the same reason |
| `0755:`, `1:30:`, `1_000:` | The ambiguous numeric spellings above |
| `7.:`, `07:` | A number whose *text* changes when YAML reads it — `7.` becomes `7.0` |
| A key opening with an indicator | `&`, `*`, `!`, `%`, `@`, `` ` ``, `,`, `|`, `>`, `[`, `]`, `{`, `}` |

A plain numeric key such as `123:` is **accepted**: both backends end up naming it `"123"`
after normalisation, so nothing diverges. Quoting is the fix for everything else: `"yes":`
is the name `yes` on every machine.

### YAML features the config does not use

The structural machinery, which the built-in reader does not implement, is refused outright
rather than letting a config depend on PyYAML being present:

| Written as | Why it is refused |
|---|---|
| `&anchor`, `*alias` | PyYAML resolves them; the built-in parser reads the text |
| `!tag`, `!!str 7` | PyYAML applies the tag; the built-in parser reads the text |
| `<<: {…}` | The merge key. PyYAML folds the mapping in and the built-in parser treats `<<` as an ordinary key — **with no error either way**, so the same profile has a setting on one machine and not on the other |
| `|`, `>` | Block scalar headers, which the built-in parser cannot continue onto the following lines |
| `---`, `...` | Document markers. xtm's config is always a single document |
| `%`, `@`, `` ` ``, `,`, `]`, `}` opening a value | Reserved indicators PyYAML refuses outright |
| `?` or `:` opening a value | Complex-key and value indicators, which PyYAML refuses |
| `<<` or `=` as a whole value | Reserved in YAML 1.1; PyYAML refuses them |
| `- item:` under a list item | A mapping inside a block sequence, which the built-in parser has nowhere to put |
| A tab anywhere in a line | PyYAML refuses one inside a plain scalar; the built-in parser cannot indent with them |
| Indentation that is not a multiple of two spaces | The built-in parser's rule, applied to both backends so a config cannot load on only one |

Only the **first** character of a value is an indicator, so ordinary settings are untouched:
`command: tail -f x | grep y`, `cwd: ~/work`, `xterm_args: [-fn, Monospace]` and a
`user@host` or `50%` anywhere in a value all read the same under both backends.

### Flow collections

Inside `[ ]` and `{ }` the rules are stricter, because brackets, braces and commas are
structure there:

| Written as | Why it is refused |
|---|---|
| `{x:0, y:0}` | A colon only separates a key from its value when a space follows it, so PyYAML reads this as two keys named `x:0` and `y:0` with no values. Write `{x: 0, y: 0}` |
| `{a}` | An entry with no value at all: a set entry to PyYAML, unrepresentable here |
| `{a: 1,}`, `[a, b,]` | The trailing comma is an empty entry to one reader and nothing to the other |
| `[a: 1]`, `[a:]` | A list item containing a colon, which PyYAML may read as a nested mapping. Quote it — `["Monospace:size=11"]` is a font name and works |
| `[a{b]`, `{k: a]b}` | A plain scalar carrying a bracket or a brace, which ends the scalar in flow context |
| `{k: - x}`, `[- x]` | A block sequence indicator inside a flow collection. A bare `-` is ordinary text and is accepted |

Quoting is the escape hatch throughout: a quoted scalar means the literal text to both
backends and is always accepted.

## What a config rewrite changes

Any command that writes the config — `--set`, `--update-profile`, `--delete-session`,
`--make-global` and the profile management commands — rewrites the whole file. Two things
do not survive that:

- **Flow style becomes block style.** `work1: {x: 0, y: 0}` is read correctly but written
  back expanded over four lines. The values and their meaning are identical; only the
  layout changes.
- **Comments are dropped.** Neither backend preserves them.

Both are worth knowing before hand-formatting a config, and neither affects reading: flow
style and comments are fully supported as input.

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
