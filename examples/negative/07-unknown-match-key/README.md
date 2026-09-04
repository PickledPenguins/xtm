# 07 — An unknown key in `match`

`match` accepts `hostname` and nothing else. A profile with a malformed
match block stays visible rather than being hidden, so the error is what
you see instead of the profile vanishing.

**The fix:** `match: {hostname: ...}`.
