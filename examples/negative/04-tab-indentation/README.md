# 04 — A tab in the file

The built-in reader cannot indent with tabs, and PyYAML refuses one inside
a plain scalar. Either way the file means something different depending on
which reader opens it.

**The fix:** indent with two spaces per level.
