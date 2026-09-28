# 03 — A slot written twice

`logs` appears twice. A YAML reader keeps the last value and silently drops
the first, so the window would open at the second position and the first
would simply be gone. xtm refuses the file instead and names the line of the
second one.

**The fix:** rename one of the slots, or delete the one you did not mean.
