# Lifecycle

What happens, in what order, when xtm runs. Every invocation follows the same pipeline;
what differs is which action step 9 dispatches to.

## One invocation

1. **Parse arguments.** A usage error exits 2 before anything is read. A positional profile
   name is folded into `--profile` here, and supplying both spellings is rejected.
2. **Configure logging.** `--log-level`, then `--debug`, then `--quiet`, otherwise INFO. A
   log file, if requested, is always opened at DEBUG regardless of the console level.
3. **Resolve paths.** `--config` beats `XTM_CONFIG_DIR` beats the default; `--state-dir`
   beats `XTM_STATE_DIR` beats the default. The state file's name gains this machine's
   short hostname — or the one `--hostname` names.
4. **Set run modes.** `--dry-run`, `--verify` and `--no-frame-compensation` take effect
   from here on, and whether this command may write the config is decided.
5. **Take the config lock**, for actions that write to it. Held across load, modify and
   save, so two concurrent runs cannot lose each other's edits.
6. **Load and sanity-check the config**, creating one if it does not exist. A config
   lacking the reserved `default` profile is given one for the run; that addition is
   written back only by a command that writes the config anyway, so reading a config never
   rewrites it.
7. **Resolve the profile**: `--profile` or the positional name, then the saved current
   profile unless `--auto`, then the first `match` block that fits, then `default`. Only
   profiles visible on this machine are eligible unless `--all` was given.
8. **Validate that profile** in full, injecting the default `stack` and an empty `sessions`
   if absent. Validation is by profile, not by file: a profile bound to another machine is
   checked by `--validate` and ignored by everything else.
9. **Run the action.**
10. **Save the config**, for actions that changed it — through an edit guard that re-runs
    validation and restores the profile if the edit would not survive it.

## Placing a set of windows

`--reset` and `--reset-all` process named sessions in **config file order**, then
`--reset-all` stacks strays in **alphabetical order**. Both orders are asserted by tests,
because a layout that comes back in a different order every time is a layout you cannot
trust.

For each session in turn:

1. If it is not running, open it: spawn an xterm, wait for tmux to report a client, and
   fail with a message naming the session if it never attaches.
2. Resolve the client's tty once, and reuse it for both operations — two lookups could
   disagree if a client attached or detached between them.
3. **Resize, then move.** On some window managers a resize shifts the anchor point, so
   moving last is what makes the final position the requested one.
4. Under `--verify`, wait briefly, read the geometry back, and report any difference.

A failure on one session is collected and reported rather than aborting the rest; the
command exits 1 at the end.

## Switching profiles

Switching happens before the new profile's windows are touched, and it acts on the profile
being **left**:

1. Read that profile's `on_switch`, or `--on-switch` if given.
2. `leave` stops here.
3. Otherwise, collect its sessions, minus any the new profile also claims — a shared
   session moves to its new slot rather than being closed and reopened.
4. Detach or kill each one. A failure is a warning, not an error: the switch itself
   succeeded, and a window left open is cosmetic.

## Writing the config

Every write is the same four steps, whatever triggered it:

1. Validate the edited profile, and restore it if the edit would not survive a reload.
2. Serialise, quoting anything the parity guard would refuse unquoted.
3. Write to a temporary file in the same directory, flush, `fsync`, rename into place, and
   sync the parent directory. A crash leaves the old config, never half of a new one.
4. Release the lock.

Under `--dry-run`, steps 2 to 4 become log lines beginning `[dry-run]` and nothing is
written — including the lock file, which is itself a write.

## Capturing a layout

`--update-profile` and `--new-profile` run the pipeline in reverse:

1. Enumerate windows by instance name (`xtm-<session>`), not by title.
2. Read each one's geometry with the first available geometry tool.
3. Subtract the window manager's frame extents, read with `xprop`, so what is stored is the
   frame position that placement expects. Skipped with a debug message when `xprop` is
   absent or the window manager publishes nothing.
4. Update the matching slots, preserving `command`, `cwd`, `xterm_args`, `session` and
   `title` — only placement is captured.
5. Save, through the same guarded write as above.
