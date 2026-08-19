# Examples

Six worked configurations, each one a scenario you might actually have.

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

## Writing a new one

Add a directory with `config.yaml` and `README.md`, then:

```bash
python3 examples/run.py --show NN-your-example > examples/NN-your-example/expected.json
```

Check the output says what you meant before committing it. The point of
`expected.json` is that a human agreed with it once.
