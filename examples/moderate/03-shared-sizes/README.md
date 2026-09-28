# moderate/03 — one size, many slots

Level: **moderate**. YAML anchors and merge keys sharing one window size
between four slots.

## What it shows

- `&quarter` names a mapping; `<<: *quarter` copies it into a slot. Changing
  the size in one place changes every slot that uses it.
- A key written beside the merge wins: `wide` takes the shared height and
  sets its own width.
- A top-level key other than `profiles` (here `sizes`) is a convenient home
  for anchors. xtm reads only `profiles` and leaves the rest alone.

A save rewrites the file (see [configuration](../../docs/configuration.md#what-a-config-rewrite-changes)):
the merged values are written out in full and the anchors are gone, so keep
a config like this one hand-edited, or edit it again after `--update-profile`.

## Try it

```bash
XTM_CONFIG_DIR=examples/moderate/03-shared-sizes src/xtm.py grid --list
```
