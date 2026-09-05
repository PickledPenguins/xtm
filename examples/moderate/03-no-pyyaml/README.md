# moderate/03 — The dependency-free path

Level: **moderate**. The same config read twice: once with PyYAML, once with it hidden. The two
readings must be identical.

## Why this exists

xtm is meant to run unmodified on machines where you cannot install
anything, so it uses PyYAML when it is importable and its own parser when it
is not. That means **every config value has two implementations behind it**,
and a difference between them is a bug that only appears on some machines.

Those bugs are real and were shipped. PyYAML implements YAML 1.1, which
reads `0755` as octal 493 where the built-in parser reads decimal 755, and
`1:30` as sexagesimal 90 where the built-in parser reads text. Neither
warns: both readings are valid numbers that pass validation, so the same
profile placed a window at two different coordinates on two machines. xtm
now refuses every value the two backends disagree about — see
[the README's "Values that must be quoted"](../../README.md#values-that-must-be-quoted).

Separately, PyYAML writes lists in block style (`- -fn`), which the built-in
parser did not understand at all: a config saved on a machine *with* PyYAML
could not be loaded on one without it.

## What it shows

The config deliberately uses every construct the built-in parser has to
handle:

- flow mappings (`{x: 0, y: 0}`) and block mappings (one key per line)
- flow sequences (`[-fn, fixed]`) and block sequences (`- -fn`)
- comments on their own line and trailing a value
- a quoted `"yes"`, because unquoted `yes` is a boolean to PyYAML and text
  to the built-in parser, so xtm rejects the ambiguous spelling outright

## How it is tested

The example runner runs this one with `PYTHONPATH` pointing at a stub that
raises `ImportError` on `import yaml`, then compares the result against the
recorded `expected.json` — which was produced the normal way, with PyYAML
available. If the two parsers ever disagree about anything in this file, the
example fails.

The runner also verifies that PyYAML really is unimportable before trusting
the result. Without that check, a broken stub would make this example
silently test PyYAML twice and still pass, while claiming to prove something
about the built-in parser.

## Try it

```bash
# Normally
XTM_CONFIG_DIR=examples/moderate/03-no-pyyaml ./xtm.py portable --list

# With PyYAML hidden
mkdir -p /tmp/nopyyaml
echo "raise ImportError('hidden')" > /tmp/nopyyaml/yaml.py
PYTHONPATH=/tmp/nopyyaml XTM_CONFIG_DIR=examples/moderate/03-no-pyyaml ./xtm.py portable --list
```

The two outputs must match. `--debug` reports which backend was used:

```bash
XTM_CONFIG_DIR=examples/moderate/03-no-pyyaml ./xtm.py portable --list --debug 2>&1 | grep PyYAML
```
