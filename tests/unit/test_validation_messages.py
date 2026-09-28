"""Every validation refusal names the profile, the slot and the field.

The messages are the interface: a config is hand-edited, and what the user
gets back is the only thing that tells them which line to fix. These cases
are cheap to write and they are the difference between "invalid config" and
"profile 'desk' slot 'logs': 'title' must be text".

Structural refusals from the YAML reader live here too, for the same reason.
"""

import unittest

from tests.support import xtm


def geometry(**overrides):
    base = {"x": 0, "y": 0, "width": 900, "height": 700}
    base.update(overrides)
    return base


class TestSlotFieldTypes(unittest.TestCase):
    """A slot key with the wrong type is named, not just refused."""

    def refuse(self, entry, expected):
        with self.assertRaises(xtm.XtmError) as caught:
            xtm.validate_session_entry(entry, "profile 'desk' slot 'logs'")
        message = str(caught.exception)
        self.assertIn(expected, message)
        self.assertIn("logs", message,
                      "the message does not say which slot: %s" % (message,))

    def test_session_must_be_a_string(self):
        self.refuse(geometry(session=7), "'session' must be text")

    def test_title_must_be_a_string(self):
        self.refuse(geometry(title=["a"]), "'title' must be text")

    def test_command_must_be_a_string(self):
        self.refuse(geometry(command={"run": "x"}), "'command' must be text")

    def test_cwd_must_be_a_string(self):
        self.refuse(geometry(cwd=3.5), "'cwd' must be text")

    def test_xterm_args_must_be_a_list(self):
        self.refuse(geometry(xterm_args="-fn Monospace"),
                    "'xterm_args' must be a list")

    def test_an_xterm_arg_must_not_be_a_boolean(self):
        # bool is a subclass of int, so it would slip through a naive
        # isinstance check and reach xterm as "True".
        self.refuse(geometry(xterm_args=["-fn", True]),
                    "every entry in 'xterm_args' must be a string")

    def test_a_valid_entry_raises_nothing(self):
        xtm.validate_session_entry(
            geometry(session="notes", title="Notes", command="top",
                     cwd="/tmp", xterm_args=["-fn", "fixed", 11]),
            "profile 'desk' slot 'logs'")


class TestProfileFieldTypes(unittest.TestCase):

    def refuse(self, profile, expected):
        config = {"profiles": {"desk": profile}}
        with self.assertRaises(xtm.XtmError) as caught:
            xtm.validate_profile(config, "desk")
        message = str(caught.exception)
        self.assertIn(expected, message)
        self.assertIn("desk", message,
                      "the message does not say which profile: %s" % (message,))

    def test_stack_must_be_a_mapping(self):
        self.refuse({"stack": [40, 40], "sessions": {}},
                    "'stack' must be a mapping")

    def test_title_must_be_a_string(self):
        self.refuse({"title": 5, "sessions": {}}, "'title' must be text")

    def test_sessions_must_be_a_mapping(self):
        self.refuse({"sessions": ["work1"]}, "sessions")

    def test_a_title_template_naming_an_unknown_field_is_refused(self):
        self.refuse({"title": "{nonexistent}", "sessions": {}}, "title")


class TestStructuralRefusals(unittest.TestCase):
    """Shapes YAML itself refuses, reported as one line with a position."""

    def refuse(self, text, expected):
        with self.assertRaises(xtm.XtmError) as caught:
            xtm.load_yaml(text)
        self.assertIn(expected, str(caught.exception))
        self.assertRegex(str(caught.exception), r"line \d+, column \d+")

    def test_a_list_item_belonging_to_no_list(self):
        self.refuse("profiles:\n  a: 1\n- stray\n", "did not find expected key")

    def test_a_mapping_mixed_into_a_list(self):
        self.refuse("profiles:\n  a:\n    - one\n    b: 2\n", "line 4")

    def test_an_unbalanced_flow_collection(self):
        self.refuse("profiles:\n  a: {x: 1\n", "flow mapping")

    def test_text_after_a_closed_flow_collection(self):
        self.refuse("profiles:\n  a: [1, 2]tail\n", "line 2")


class TestEmptyAndMissingSections(unittest.TestCase):
    """A config may be sparse; it may not be a surprise."""

    def test_a_config_with_a_null_profiles_section_is_treated_as_empty(self):
        data = xtm.load_yaml("profiles:\n")
        self.assertEqual(data, {"profiles": None})

    def test_a_profile_without_sessions_validates_as_empty(self):
        config = {"profiles": {"desk": {}}}
        profile = xtm.validate_profile(config, "desk")
        self.assertEqual({}, profile["sessions"])
        self.assertIn("stack", profile,
                      "a profile with no stack should get the default one")


if __name__ == "__main__":
    unittest.main()
