#!/usr/bin/env python3
"""The catalogue: decisions that must not be silently reversible.

Coverage says a line ran. A mutant says the line's behaviour is pinned. Each
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

YAML = ["tests.differential.test_yaml_backends", "tests.unit.test_yaml"]
CONFIG = ["tests.unit.test_config", "tests.unit.test_validation"]
NAMING = ["tests.unit.test_commands", "tests.integration.test_basics"]
WINDOWS = ["tests.integration.test_windows"]
BASICS = ["tests.integration.test_basics"]

CATALOGUE = [
    # ---- YAML parity: the most defect-prone area in the project ----
    Mutant(
        "yaml-colon-needs-space",
        'if ch == ":" and line[i + 1:i + 2] in ("", " "):',
        'if ch == ":":',
        YAML,
        "A colon separates a key from a value only before a space or the end "
        "of the line. Splitting at every colon reads `119%:~5:` as a "
        "different key than PyYAML does."),
    Mutant(
        "yaml-quote-only-at-item-start",
        'if ch in "\\"\'" and at_item_start:\n            quote = ch\n        elif ch in "[{":',
        'if ch in "\\"\'":\n            quote = ch\n        elif ch in "[{":',
        YAML,
        "A quote opens a quoted scalar only where a scalar may begin. "
        "Honouring one mid-scalar refuses `{title: \"v}w\"}` that PyYAML "
        "reads happily."),
    Mutant(
        "yaml-split-quote-only-at-item-start",
        'if ch in "\\"\'" and at_item_start:\n            quote = ch\n            current.append(ch)',
        'if ch in "\\"\'":\n            quote = ch\n            current.append(ch)',
        YAML,
        "The same rule in the splitter: `{command: echo \"hi, there\"}` must "
        "split at the comma, as PyYAML does, rather than reading one value."),
    Mutant(
        "yaml-indicator-set",
        '_YAML_INDICATOR_CHARS = "&*!%@`|>,]}?:"',
        '_YAML_INDICATOR_CHARS = "&*!"',
        YAML,
        "Every character in the indicator set is one PyYAML treats as "
        "structure and the built-in reader as text."),
    Mutant(
        "yaml-ambiguous-words",
        '_YAML_AMBIGUOUS_WORDS = ("yes", "no", "on", "off")',
        '_YAML_AMBIGUOUS_WORDS = ("yes",)',
        YAML,
        "All four YAML 1.1 booleans diverge, not just the first."),
    Mutant(
        "yaml-typed-key-spellings",
        '_YAML_TYPED_KEY_SPELLINGS = ("~", "null", "Null", "NULL",',
        '_YAML_TYPED_KEY_SPELLINGS = ("~",',
        YAML,
        "A null or boolean KEY normalises differently under the two "
        "backends, so every spelling has to be refused, not just `~`."),
    Mutant(
        "yaml-escape-rule",
        """if following not in ('"', "\\\\"):""",
        "if False:",
        YAML,
        "An escape the built-in reader does not implement means \"a\\tb\" is "
        "a tab on one machine and a backslash and a t on the other."),
    Mutant(
        "yaml-empty-value-is-null",
        'pending[1][pending[2]] = None',
        'pending[1][pending[2]] = {}',
        YAML,
        "A bare `key:` is null, as PyYAML reads it. An empty mapping makes "
        "a profile with no body valid without PyYAML and fatal with it."),

    # ---- placement ----
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
        "        yield\n        validate_profile(config, profile_name)",
        "        yield",
        CONFIG + BASICS,
        "An edit that would leave a profile unloadable must be refused. "
        "Without the re-validation, --set can write a config every later "
        "command rejects."),
    Mutant(
        "config-keys-normalised-to-text",
        'config["profiles"] = dict((str(k), v) for k, v in profiles.items())',
        'config["profiles"] = profiles',
        CONFIG,
        "PyYAML types a numeric key as an int; every lookup in xtm is by "
        "text, so a profile named 123 would be unreachable."),
]


def main(argv):
    pattern = argv[1] if len(argv) > 1 else ""
    selected = [m for m in CATALOGUE if pattern in m.name]
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
