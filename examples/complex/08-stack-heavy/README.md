# complex/08 — leaning on the stack

Level: **complex**. What happens when most windows are not in the profile.

## What is combined

- **Two configured slots and an expectation of many strays.** `--reset` places
  the two; `--reset-all` also gathers every attached session that the profile
  does not name and stacks them.
- **Stack arithmetic that has to hold for a dozen windows.** The Nth stray is
  at `x + N*offset_x`, `y + N*offset_y`: the twelfth lands at `(795, 480)`,
  still on a 2880-wide screen. Larger offsets would walk it off, and there is
  no wraparound — xtm does not know the screen size.
- **Stray attribution.** A stray belonging to another profile is labelled with
  that profile's name in the listing, so an incident does not leave you
  guessing which context a window came from.
- **`--open` for an unconfigured session** uses the same stack, counted from
  the number of attached strays — so opening one during an incident lands it
  beside the others rather than on top of them.

## Try it

```bash
XTM_CONFIG_DIR=examples/complex/08-stack-heavy ./xtm.py --list
XTM_CONFIG_DIR=examples/complex/08-stack-heavy ./xtm.py --reset-all --dry-run
```
