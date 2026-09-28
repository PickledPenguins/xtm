# complex/10 — a config mid-migration

Level: **complex**. Three generations of the same profile in one file, which
is what adopting `prefix:` actually looks like.

## What is combined

- **`legacy` has no prefix**, so `editor` is the session `editor`. This is how
  every config looked before 0.5 and still works unchanged.
- **`halfway` has a prefix and two explicit `session:` names.** That is the
  documented way to add a prefix without renaming sessions that are already
  running: the old windows keep working, and anything added later is owned by
  the profile.
- **`modern` is the end state**, with a title template as well.
- **The hazard is visible here.** `legacy` and `halfway` both resolve `editor`
  to the session `editor`, so they share it — not by intent but by history.
  Switching between them keeps that window; switching to `modern` detaches it,
  because `mo-editor` is a different session entirely.

## Why it is worth having

The migration note in the guide describes this in prose. Reading the three
profiles side by side is faster, and the file is directly copyable as the
intermediate step: add the prefix, pin the running sessions, move them over
one at a time.

## Try it

```bash
XTM_CONFIG_DIR=examples/complex/10-migration src/xtm.py --list-profiles --verbose
XTM_CONFIG_DIR=examples/complex/10-migration src/xtm.py halfway --list
```
