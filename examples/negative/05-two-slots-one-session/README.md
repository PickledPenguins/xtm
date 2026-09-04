# 05 — Two slots resolving to one session

`alpha` names `desk-beta` explicitly and `beta` resolves to it through the
prefix, so both slots claim the same window and every reset would fight
over where it goes.

**The fix:** rename one of them, or drop the
explicit `session:`.
