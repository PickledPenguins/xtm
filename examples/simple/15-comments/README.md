# simple/15 — comments

Level: **simple**. They are supported on the way in, and lost on the way out.

## What it shows

- **Comments work anywhere**: on their own line, or after a value. Both YAML
  backends read them the same way.
- **A rewrite drops them.** Any command that saves the config — `--set`,
  `--update-profile`, the profile management commands — writes the whole file
  back, and neither backend preserves comments.
- **The same is true of flow style**: `{x: 0, y: 0}` comes back expanded over
  four lines. The values are identical; only the layout changes.

Worth knowing before you hand-format a config you intend to keep editing with
`--set`.

## Try it

```bash
XTM_CONFIG_DIR=examples/simple/15-comments src/xtm.py --list
```
