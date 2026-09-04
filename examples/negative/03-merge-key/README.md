# 03 — The YAML merge key

PyYAML folds the named mapping into this one; the built-in reader treats
`<<` as an ordinary key. Neither reports anything, so the profile simply
has a `prefix` on one machine and not on the other.

**The fix:** write the keys out in full.
