#!/usr/bin/env python3
"""The catalog: decisions that must not be silently reversible.

Coverage says a line ran. A mutant says the line's behavior is pinned. Each
entry below breaks one decision the tool depends on, and names the test
modules that ought to fail because of it.

Rules for this file:

* A surviving mutant is a MISSING TEST. Write the test; never soften the
  mutant to make the number look better.
* A stale mutant means the code moved. Re-point it at the code as it is now,
  after checking the decision is still being made somewhere.
* Only decisions worth pinning belong here. A mutant nothing could reasonably
  detect teaches nothing, and one that breaks the parser everywhere teaches
  nothing either.

    python3 tests/mutation/run.py            # every mutant
    python3 tests/mutation/run.py yaml       # those whose name matches
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from tests.mutation.harness import Mutant, run_mutant  # noqa: E402

YAML = ["tests.unit.test_yaml"]
CONFIG = ["tests.unit.test_config", "tests.unit.test_validation"]
NAMING = ["tests.unit.test_commands", "tests.integration.test_basics"]
WINDOWS = ["tests.integration.test_windows"]
BASICS = ["tests.integration.test_basics"]

CATALOG = [
    # ---- Reading the config: names are names, nothing is lost silently ----
    Mutant(
        "yaml-keys-are-names",
        "                mapping[key_node.value] = self.construct_object(value_node,",
        "                mapping[self.construct_object(key_node)] = self.construct_object(value_node,",
        YAML,
        "YAML 1.1 reads the slot `on:` as True and `0755:` as 493; a key "
        "must be the text written or the slot silently becomes another name."),
    Mutant(
        "yaml-duplicate-key-refused",
        "                if key_node.value in written:",
        "                if False:",
        YAML,
        "YAML keeps the last of two equal keys without a word, so a slot "
        "pasted in twice would lose its first position."),
    Mutant(
        "yaml-writer-keeps-order",
        "                                        list(data.items()))",
        "                                        sorted(data.items()))",
        YAML + ["tests.property.test_properties"],
        "Slots are placed in the order they are written; a save must not "
        "alphabetize them."),
    Mutant(
        "tmux-field-separator-survives-the-c-locale",
        '_FIELD_SEPARATOR = ":"',
        '_FIELD_SEPARATOR = "\\t"',
        ["tests.integration.test_reporting"],
        "tmux 3.6 rewrites control characters to _ in the C locale xtm runs "
        "it in, so a tab separator makes every session look not running."),
    Mutant(
        "only-xtms-own-clients-are-its-windows",
        '    return os.path.isdir("/proc/self")',
        "    return False",
        ["tests.integration.test_windows"],
        "A session attached in someone else's terminal is not an xtm window: "
        "counting it opened no window, wrote escapes into that terminal, "
        "stacked it as a stray and detached it."),
    Mutant(
        "wait-for-the-xterm-just-spawned",
        "            if proc is None or (pid is not None and _is_descendant_of(pid, proc.pid)):",
        "            if True:",
        ["tests.integration.test_windows"],
        "Waiting for any client was satisfied at once by one attached "
        "elsewhere, and xtm then placed that terminal instead of its own."),
    Mutant(
        "bare-xtm-brings-the-profile-up",
        '    if args.json or not profile["sessions"]:',
        "    if True:",
        ["tests.integration.test_basics"],
        "The morning command is one word: a bare `xtm` must restore the "
        "profile, not only report on it."),
    Mutant(
        "a-window-off-screen-is-reported",
        "    if fit == ON_SCREEN:",
        "    if True:",
        ["tests.integration.test_windows"],
        "A profile for another screen put windows where nobody could see "
        "them, and nothing said so."),
    Mutant(
        "a-title-bar-out-of-reach-is-partly-off-screen",
        "        return PARTLY_OFF_SCREEN",
        "        return ON_SCREEN",
        ["tests.unit.test_geometry"],
        "A window whose top-left corner is off every monitor cannot be "
        "dragged back by hand."),
    Mutant(
        "a-failing-start-command-is-held",
        "        if hold_failures:",
        "        if False:",
        ["tests.unit.test_cli", "tests.integration.test_windows"],
        "A slot command that failed took its pane, session and window with "
        "it, so a window was just missing in the morning."),
    Mutant(
        "check-fails-on-a-missing-cwd",
        "        if cwd and not os.path.isdir(os.path.expanduser(str(cwd))):",
        "        if False:",
        ["tests.integration.test_reporting"],
        "tmux silently starts a session whose cwd is missing in $HOME; the "
        "preflight is the only place that says so."),
    Mutant(
        "placement-resize-before-move",
        "send_window_op(tty, _CSI_RESIZE_WINDOW_PX_TEMPLATE.format(height=height,\n"
        "                                                             width=width))\n"
        "    send_window_op(tty, _CSI_MOVE_WINDOW_TEMPLATE.format(x=x, y=y))",
        "send_window_op(tty, _CSI_MOVE_WINDOW_TEMPLATE.format(x=x, y=y))\n"
        "    send_window_op(tty, _CSI_RESIZE_WINDOW_PX_TEMPLATE.format(height=height,\n"
        "                                                             width=width))",
        WINDOWS,
        "Resize must precede move: on some window managers a resize shifts "
        "the anchor, so moving last is what makes the final position the "
        "requested one."),
    Mutant(
        "placement-frame-compensation-sign",
        'geometry["x"] = int(geometry["x"]) - left',
        'geometry["x"] = int(geometry["x"]) + left',
        WINDOWS,
        "Frame extents are SUBTRACTED on capture. Adding them makes every "
        "save/restore cycle drift the window down and right."),
    Mutant(
        "placement-stack-offset",
        '"x": stack_cfg["x"] + index * stack_cfg["offset_x"],',
        '"x": stack_cfg["x"] + stack_cfg["offset_x"],',
        ["tests.unit.test_geometry"],
        "Stack offsets multiply by the slot index; a constant offset piles "
        "every stray window in one place."),

    # ---- naming and ownership ----
    Mutant(
        "naming-explicit-session-is-never-prefixed",
        'if isinstance(slot_cfg, dict) and slot_cfg.get("session"):\n'
        '        return str(slot_cfg["session"])',
        'if False:\n'
        '        return str(slot_cfg["session"])',
        NAMING,
        "An explicit `session:` is used verbatim. Prefixing it anyway breaks "
        "the one mechanism two profiles have for sharing a session."),
    Mutant(
        "switch-keeps-sessions-the-new-profile-claims",
        "keep = set(profile_sessions(new_profile)) if isinstance(new_profile, dict) else set()",
        "keep = set()",
        WINDOWS,
        "A session the new profile also claims is left attached rather than "
        "closed and immediately reopened. Without the keep set, a shared "
        "session dies on every switch."),

    # ---- config safety ----
    Mutant(
        "edit-profile-validates-before-saving",
        "    try:\n        validate_profile(config, profile_name)\n"
        "    except XtmError as e:",
        "    try:\n        pass\n"
        "    except XtmError as e:",
        CONFIG + BASICS,
        "An edit that would leave a profile unloadable must be refused. "
        "Without the re-validation, --set can write a config every later "
        "command rejects."),
    Mutant(
        "edit-profile-restores-on-an-unexpected-error",
        "    except BaseException:\n"
        "        # Anything else out of the validator is a bug rather than",
        "    except (ValueError,):\n"
        "        # Anything else out of the validator is a bug rather than",
        CONFIG,
        "A failure inside the validator must still leave the profile as it "
        "was; the edit was never accepted."),
    Mutant(
        "reading-does-not-rewrite-the-config",
        "        if CONTEXT.may_write_config:",
        "        if True:",
        ["tests.integration.test_basics"],
        "A reporting command supplies the reserved profile in memory. "
        "Saving it there rewrites a config the user asked only to read, "
        "expanding their flow style and dropping their comments."),
    Mutant(
        "hostname-override-reaches-the-state-file",
        "    name = CONTEXT.hostname or platform.node().split(\".\")[0] or \"unknown\"",
        "    name = platform.node().split(\".\")[0] or \"unknown\"",
        ["tests.integration.test_environments"],
        "--hostname must reach everything that depends on which machine "
        "this is, or a run selects one machine's profile and remembers it "
        "as another's."),
    Mutant(
        "rename-follows-every-machine",
        "    for path in sorted(CONTEXT.state_dir.glob(\"current_profile.*\")):",
        "    for path in [CONTEXT.state_file] if CONTEXT.state_file.exists() else []:",
        ["tests.integration.test_basics"],
        "A rename that updates only this machine's state file leaves every "
        "other machine sharing the home directory naming a profile that no "
        "longer exists, and stopping on its next run."),
    Mutant(
        "config-lock-prevents-lost-updates",
        "                fcntl.flock(self._handle.fileno(),\n"
        "                            fcntl.LOCK_EX | fcntl.LOCK_NB)\n"
        "            except (IOError, OSError):",
        "                pass\n"
        "            except (IOError, OSError):",
        ["tests.concurrency.test_contention"],
        "Without the exclusive lock, two writers each load the same config "
        "and the second save discards the first one's slot."),
]


def main(argv):
    pattern = argv[1] if len(argv) > 1 else ""
    selected = [m for m in CATALOG if pattern in m.name]
    if not selected:
        print("no mutant matches %r" % (pattern,))
        return 2
    survived, stale = [], []
    for index, mutant in enumerate(selected, 1):
        outcome, detail = run_mutant(mutant)
        print("[%2d/%2d] %-45s %-9s %s"
              % (index, len(selected), mutant.name, outcome, detail[:60]))
        if outcome == "survived":
            survived.append(mutant)
        elif outcome == "stale":
            stale.append(mutant)
    print("\n%d mutant(s): %d killed, %d survived, %d stale"
          % (len(selected), len(selected) - len(survived) - len(stale),
             len(survived), len(stale)))
    for mutant in survived:
        print("\nSURVIVED  %s\n  %s\n  A test is missing, not the mutant's "
              "fault." % (mutant.name, mutant.why))
    for mutant in stale:
        print("\nSTALE     %s\n  %s\n  The code moved; re-point the mutant "
              "after checking the decision is still made." % (mutant.name, mutant.why))
    return 1 if survived or stale else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
