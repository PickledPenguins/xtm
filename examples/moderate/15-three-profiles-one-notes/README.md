# moderate/15 — one notebook, three contexts

Level: **moderate**. The shared-session pattern at the size people really use
it.

## What it shows

- **Three profiles, each owning its own sessions**, and all three naming the
  same `session: notes`.
- **The notes window survives every switch.** It is claimed by the profile you
  are entering, so it is repositioned rather than disposed of — even moving
  between `work` and `admin`, where `admin` kills what it leaves.
- **The same session at three different sizes.** Placing a window resizes it,
  so tmux reflows the panes inside; the content is the same, the shape is not.
- **`on_switch` differs per profile** and applies to the one being left, so
  leaving `admin` kills `admin-mail` and `admin-calendar` and still keeps
  `notes`.

## Try it

```bash
XTM_CONFIG_DIR=examples/moderate/15-three-profiles-one-notes src/xtm.py --list-profiles --verbose
XTM_CONFIG_DIR=examples/moderate/15-three-profiles-one-notes src/xtm.py study --dry-run
```
