# workflows/01 — the first run

Level: **workflow**. What happens on a machine with no config yet.

- **Commands:** `xtm -l`, `xtm -P`, `xtm -C`.
- **Expected:** the first command says it is creating a default config and
  lists the empty `default` profile; `-P` lists it as current (`*`); `-C`
  prints `default`. Nothing is saved as this machine's profile, since none
  was chosen.

Like every workflow, this runs against the fake tmux and xterm of the test
suite (`tests/support.py`), not a real X server: the windows are rows in a
file. `expected.txt` is the recorded transcript.
