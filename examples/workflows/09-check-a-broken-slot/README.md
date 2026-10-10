# workflows/09 — a slot whose working directory is missing

Level: **workflow**. `--check` finding a problem before anything opens.

- **Config:** `desk`; slot `work` has `cwd: /nonexistent/xtm-example`.
- **Commands:** `xtm -p desk --check`.
- **Expected:** exit 1; the `work` line is `fail`, saying tmux would start
  in the home directory instead; `logs` is `ok`. No session is started and
  no profile is saved.

Runs against the fake tools, not a real X server; see `examples/README.md`.
