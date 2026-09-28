# 01 — An unquoted YAML boolean

YAML reads an unquoted `yes`, `no`, `on` or `off` as a boolean, so
`title: yes` is `true`, not the word. xtm says which field wanted text and
what YAML read instead.

**The fix:** `title: "yes"`.
