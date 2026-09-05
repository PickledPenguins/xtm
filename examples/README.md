# Examples

Forty worked configurations and eight that are meant to fail. Every one is a test: each
directory holds a `config.yaml`, a `README.md` explaining what it demonstrates, and an
`expected.json` recording what `xtm` reports for it, so an example that stops matching the
tool fails the build rather than quietly rotting.

They come at three levels, and the point of having many is that you should be able to find
one close to what you want and change three values rather than starting from the manual.

- **simple/** — one idea, the smallest useful config. Start at `simple/01-one-screen`.
- **moderate/** — several features interacting, at the size people really have.
- **complex/** — every mechanism at once. No single feature is under test; the interaction is.

## Running them

```bash
make examples                                  # all of them, then the ones meant to fail
python3 examples/run.py simple/01-one-screen   # just one
python3 examples/run.py --show complex/03-day-in-the-life
python3 examples/run.py --negative             # the configs that must be refused
```

`--show` prints what `xtm` currently reports instead of comparing it, which is how you
update an `expected.json` after deliberately changing behaviour.

Nothing is installed, no X server is used and no window is opened: the runner points `xtm`
at the example's config and asks it to report, so running these is safe on any machine.

## Simple

| | Shows |
|---|---|
| [simple/01-one-screen](simple/01-one-screen/) | The smallest config that is actually useful: three windows in fixed |
| [simple/02-two-monitors](simple/02-two-monitors/) | Two monitors, with the secondary positioned to the **left** of the primary. |
| [simple/03-startup-commands](simple/03-startup-commands/) | What a window runs when it opens, where it starts, and what it looks like. |
| [simple/04-window-titles](simple/04-window-titles/) | What the window manager shows in the title bar, and how to control it. |
| [simple/05-stacking](simple/05-stacking/) | One configured window, and somewhere for everything else to go. |
| [simple/06-xterm-appearance](simple/06-xterm-appearance/) | Giving one window a different font, and another a dark background. |
| [simple/07-working-directories](simple/07-working-directories/) | Starting each session somewhere useful, with something already running. |
| [simple/08-flow-style](simple/08-flow-style/) | One line per profile, for a config you edit rather than generate. |
| [simple/09-quoting-values](simple/09-quoting-values/) | The four shapes that need quotes, in one config. |
| [simple/10-prefix](simple/10-prefix/) | One key, and the ownership model it creates. |
| [simple/11-explicit-session](simple/11-explicit-session/) | The exception to the prefix, and the only way to share. |
| [simple/12-on-switch](simple/12-on-switch/) | One key that decides what leaving a profile costs. |
| [simple/13-negative-coordinates](simple/13-negative-coordinates/) | A monitor left of the origin, and why the geometry format |
| [simple/14-empty-profile](simple/14-empty-profile/) | Valid, and the state every new machine starts in. |
| [simple/15-comments](simple/15-comments/) | They are supported on the way in, and lost on the way out. |

## Moderate

| | Shows |
|---|---|
| [moderate/01-machine-specific-profiles](moderate/01-machine-specific-profiles/) | A single config file synced across a laptop and a workstation, where each |
| [moderate/02-shared-sessions](moderate/02-shared-sessions/) | Two profiles that reuse the same slot names without fighting, plus one |
| [moderate/03-no-pyyaml](moderate/03-no-pyyaml/) | The same config read twice: once with PyYAML, once with it hidden. The two |
| [moderate/04-switching-profiles](moderate/04-switching-profiles/) | Three profiles, one per disposal mode, and a session shared by two of them. |
| [moderate/05-two-machines-one-layout](moderate/05-two-machines-one-layout/) | Binding profiles to hosts without duplicating them. |
| [moderate/06-every-key](moderate/06-every-key/) | A reference config using every profile and slot key xtm accepts. |
| [moderate/07-dev-and-prod](moderate/07-dev-and-prod/) | Two profiles for two contexts, made hard to confuse. |
| [moderate/08-laptop-and-dock](moderate/08-laptop-and-dock/) | Two layouts for one machine, switched by hand. |
| [moderate/09-monitoring-wall](moderate/09-monitoring-wall/) | Eight commands tiled, and somewhere for the ninth window. |
| [moderate/10-remote-work](moderate/10-remote-work/) | `command` doing the real work it was added for. |
| [moderate/11-big-grid](moderate/11-big-grid/) | The boilerplate for a tiled screen, and the arithmetic |
| [moderate/12-title-templates](moderate/12-title-templates/) | Three ways a window gets its title, in one profile. |
| [moderate/13-mixed-quoting](moderate/13-mixed-quoting/) | The four quoting rules, applied where they actually come |
| [moderate/14-captured-layout](moderate/14-captured-layout/) | What the tool writes when you let it, and how to read it. |
| [moderate/15-three-profiles-one-notes](moderate/15-three-profiles-one-notes/) | The shared-session pattern at the size people really use |

## Complex

| | Shows |
|---|---|
| [complex/01-everything-at-once](complex/01-everything-at-once/) | Every mechanism xtm has, in one config, interacting. |
| [complex/02-fleet](complex/02-fleet/) | Four machine classes, three binding shapes, and one |
| [complex/03-day-in-the-life](complex/03-day-in-the-life/) | What a real desktop looks like when everything is in use |
| [complex/04-overlapping-slot-names](complex/04-overlapping-slot-names/) | Collision, non-collision, and the migration case, in one |
| [complex/05-switch-matrix](complex/05-switch-matrix/) | Three disposal modes and two shared sessions, arranged so |
| [complex/06-quoting-minefield](complex/06-quoting-minefield/) | Nine values that mean two different things unquoted, all |
| [complex/07-deep-flow](complex/07-deep-flow/) | One line per profile, nested four deep, and legal. |
| [complex/08-stack-heavy](complex/08-stack-heavy/) | What happens when most windows are not in the profile. |
| [complex/09-edge-geometry](complex/09-edge-geometry/) | Every boundary value in one profile, all of them legal. |
| [complex/10-migration](complex/10-migration/) | Three generations of the same profile in one file, which |
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
