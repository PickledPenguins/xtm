# 02 — A flow mapping with no space after the colon

In YAML a colon separates a key from its value only when a space follows
it, so `{x:0, y:0}` is two keys named `x:0` and `y:0` with no values at all.
xtm then finds no `x` in the slot.

**The fix:** `{x: 0, y: 0, width: 900, height: 700}`.
