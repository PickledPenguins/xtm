# 02 — A flow mapping with no space after the colon

PyYAML reads `{x:0, y:0}` as two keys named `x:0` and `y:0` with no values
at all; the built-in reader split on the colon and saw x=0, y=0.

**The fix:** `{x: 0, y: 0, width: 900, height: 700}`.
