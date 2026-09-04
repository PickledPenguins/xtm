# Examples

Sixteen worked configurations, each one a scenario you might actually have, and eight configs that are meant to fail.

They come at three levels. **Simple** examples show one idea on the smallest useful config -- start at 01. **Moderate** ones combine a few, which is where most real configs live. The **complex** one puts every mechanism in the tool into a single file, because the way they interact is the part no other example can show.

Every example is also a test. Each directory holds a `config.yaml`, a
`README.md` explaining what it demonstrates and why it is written that way,
and an `expected.json` recording what `xtm` reports for it. CI runs all of
them, so an example that stops matching the tool it documents fails the
build rather than quietly rotting.

## Running them

```bash
make examples          # run all of them
python3 examples/run.py 03-machine-specific-profiles    # just one
python3 examples/run.py --show 03-machine-specific-profiles
```

`--show` prints what `xtm` currently reports instead of comparing it,
which is how you update an `expected.json` after deliberately changing
behaviour.

Nothing is installed, no X server is used and no window is opened: the
runner points `xtm` at the example's config with `XTM_CONFIG_DIR` and asks
it to report, so running these is safe on any machine.

## The examples

| | Demonstrates |
|---|---|
| [01-single-desktop](01-single-desktop/) | The smallest useful config: three windows in fixed positions. Start here. |
| [02-multi-monitor](02-multi-monitor/) | Negative coordinates for a monitor left of the primary, and why `x,y,w,h` is used instead of X geometry strings. |
| [03-machine-specific-profiles](03-machine-specific-profiles/) | One config file shared across a laptop and a workstation, with `match.hostname` selecting the right layout per machine. |
| [04-shared-sessions](04-shared-sessions/) | `prefix` for profile-owned sessions, and an explicit `session:` for one deliberately shared between profiles. |
| [05-startup-commands](05-startup-commands/) | `command`, `cwd` and `xterm_args`, including colour-coding a production window differently from a development one. |
| [06-no-pyyaml](06-no-pyyaml/) | The dependency-free path: the same config read with PyYAML blocked, proving a config means one thing on every machine. |
| [07-window-titles](07-window-titles/) | `title:` as a profile template and as a per-slot override, and why a title is cosmetic rather than identity. |
| [08-stacking-strays](08-stacking-strays/) | `stack:` as the fallback position, and how offsets accumulate for each unplaced window. |
| [09-switching-profiles](09-switching-profiles/) | All three `on_switch` modes, and a session shared by two profiles surviving a switch. |
| [10-xterm-appearance](10-xterm-appearance/) | `xterm_args` for fonts, colours and scrollback, and why a font name with a colon must be quoted. |
| [11-working-directories](11-working-directories/) | `cwd` and `command`, when each applies, and the idiom for keeping a pane alive after the command exits. |
| [12-flow-style](12-flow-style/) | The compact `{…}` spelling, the space YAML requires after every colon, and what a rewrite does to it. |
| [13-quoting-values](13-quoting-values/) | The four shapes that need quotes, and the difference between single and double quotes. |
| [14-two-machines-one-layout](14-two-machines-one-layout/) | A hostname list and a glob, plus what makes a profile global and why a global one is never auto-selected. |
| [15-everything](15-everything/) | Every profile and slot key in one config, as a reference rather than as advice. |
| [complex-01-everything-at-once](complex-01-everything-at-once/) | **Complex.** Every mechanism at once: two ownership models, two disposal modes, three binding shapes, a shared session, and the quoting rules inside a working config. |

## Configs that are meant to fail

`negative/` holds eight configs xtm refuses, each with the message it is
supposed to produce recorded in `expected-error.txt`. They are documentation
as much as tests: the fastest way to understand a rule is to see the config
that breaks it and the sentence the tool answers with.

```bash
python3 examples/run.py --negative
```

| | Refused because |
|---|---|
| [negative/01-unquoted-boolean](negative/01-unquoted-boolean/) | `yes` unquoted is a boolean to PyYAML and a word to the built-in reader. |
| [negative/02-flow-mapping-without-spaces](negative/02-flow-mapping-without-spaces/) | `{x:0}` is one key named `x:0` to PyYAML, not `x` with the value 0. |
| [negative/03-merge-key](negative/03-merge-key/) | `<<:` merges under PyYAML and is an ordinary key without it, with no error either way. |
| [negative/04-tab-indentation](negative/04-tab-indentation/) | A tab means one thing to PyYAML and nothing the built-in reader can indent with. |
| [negative/05-two-slots-one-session](negative/05-two-slots-one-session/) | Two slots resolving to one session would fight over one window. |
| [negative/06-negative-size](negative/06-negative-size/) | A position may be negative; a size may not. |
| [negative/07-unknown-match-key](negative/07-unknown-match-key/) | `match` takes `hostname` and nothing else; `display:` was removed in 0.4. |
| [negative/08-document-marker](negative/08-document-marker/) | `---` loads under PyYAML and fails without it. |

## Writing a new one

Add a directory with `config.yaml` and `README.md`, then:

```bash
python3 examples/run.py --show NN-your-example > examples/NN-your-example/expected.json
```

Check the output says what you meant before committing it. The point of
`expected.json` is that a human agreed with it once.
