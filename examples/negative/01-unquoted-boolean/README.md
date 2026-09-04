# 01 — An unquoted YAML 1.1 boolean

`title: yes` is the boolean `true` to PyYAML and the string `yes` to the
built-in reader, so the same config would title the window differently
depending on what is installed.

**The fix:** `title: "yes"`.
