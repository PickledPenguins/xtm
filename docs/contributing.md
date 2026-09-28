# Contributing

Changing xtm itself: where a change goes, what will stop you, and the recipes for the
changes people actually make.

## Before anything

```bash
tests/run-tests.sh --install-hooks   # once, per clone
```

That runs the linter and the fast half of the suite before every commit, in about 25
seconds. `git commit --no-verify` bypasses it when you need to; nothing else does.

## Where a change goes

`src/xtm.py` is one file in named sections, ordered bottom-up: primitives, then the layers
built on them, then the commands, then the CLI. [architecture.md](architecture.md) has the
layer table and the responsibility of each section.

| If you are changing | Go to |
|---|---|
| What a command does | its `cmd_*` function, and the `COMMANDS` table for how it is dispatched |
| What a config key means | `validate_session_entry` or `validate_profile`, then wherever it is consumed |
| How a window is placed | the placement section; [placement.md](placement.md) explains the mechanism |
| How the config is read or written | the YAML region — read its map first, at the head of the section |
| What a message says | the `die()` call that produces it, and the failure catalog that pins it |

## What enforces what

Every one of these fails the build rather than warning, and each is a real gate that has
caught something:

| Gate | Enforces | Runs in |
|---|---|---|
| `pyflakes`, `py_compile` | No unused imports, no syntax errors | `--lint`, the hook |
| The fast suite | Unit, differential, property, smoke, safety | the hook, `--fast` |
| The long suite | Integration, concurrency, fault injection, examples | `--long` |
| `[tool.coverage.report] fail_under` in `pyproject.toml` | The coverage floor, on the combined figure | `--coverage` |
| `tests/mutation/run.py` | That the suite would notice each pinned decision breaking | `--mutation` |
| `tests/test_documentation.py` | Every option, exit code, env var, config key and JSON key documented; the README cap; no dead links; the coverage table current | the fast suite |
| `tests/examples/` | Every example matches its recorded output; each level has enough cases; every README names its level | the long suite |
| `make check` | All of the above, then that the tree is unchanged by a full run | before a release |

## Recipes

### Add a command

1. Add the option to `build_parser`, in the action group.
2. Write `cmd_<name>(args, config, profile_name) -> int`. Return an exit code; never call
   `sys.exit`. Raise user-facing failures with `die()`.
3. Add `(dest, handler, mutates_config, needs_profile)` to `COMMANDS`. `mutates_config`
   decides whether the run holds the config lock; `needs_profile` decides whether an
   unresolvable profile is fatal.
4. If it edits a profile, do it inside `edit_profile`.
5. Document it in [cli.md](cli.md) — the documentation test fails until you do.
6. Add an example under `examples/simple/`, and a failure case under `examples/negative/`
   if it can be used wrongly.
7. Add the bash completion entry; a test asserts every parser option is offered.

### Add a config key

1. Accept and validate it in `validate_session_entry` or `validate_profile`, with a message
   naming the profile, the slot and the field.
2. Consume it where it applies.
3. Check it survives a save: `load_yaml(dump_yaml(config)) == config`.
4. Document it in [configuration.md](configuration.md) and add it to `examples/config.yaml.example`.
5. Add it to an example that already uses neighboring keys.

### Write an example

See [testing.md](testing.md) and the [examples index](../examples/README.md). Three levels,
a README per case naming its level, and `python3 examples/run.py --record <prefix>` to
record what it reports.

**Keep a note of anything that fights you while writing one.** You know how the tool works
better than any user ever will, so whatever gives you trouble is something they cannot do
at all. Each note is a defect to fix, a missing affordance to report, or a documentation
gap to close — the last four of those produced `--hostname`, `--record`, the reserved-profile
fix and this document.

## House rules

- **Single file, standard library only.** xtm is copied to machines where nothing can be
  installed; `install.sh` copies exactly one file.
- **Python 3.6 syntax and API only.** The module docstring lists what that rules out.
- **stdout is data, stderr is logs.** Anything a script might parse goes through `print`.
- **Every external command goes through `run()`**, which supplies the timeout, the C
  locale, debug logging and a clean error for a missing program.
- **Comment intent, not mechanism.** The code already says what it does.
- **The commit log is the release history.** There is no changelog. Version bumps move the
  last component by one (`0.9` → `0.10`, never `1.0`).
