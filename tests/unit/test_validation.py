"""Config validation: names, geometry, session entries, profiles."""

import unittest

from tests.support import (
    xtm,
)


class TestNameValidation(unittest.TestCase):

    def test_valid_names(self):
        for name in ("work1", "a", "a.b-c_d", "123"):
            self.assertTrue(xtm.is_valid_session_name(name), name)

    def test_invalid_names(self):
        for name in ("", None, "a b", "a:b", "a/b", "work1\n", "a\tb", "ünïcode"):
            self.assertFalse(xtm.is_valid_session_name(name), repr(name))

    def test_validate_session_name_raises(self):
        with self.assertRaises(xtm.XtmError):
            xtm.validate_session_name("bad:name")

    def test_validate_profile_name_raises(self):
        with self.assertRaises(xtm.XtmError):
            xtm.validate_profile_name("bad name")


class TestGeometryValidation(unittest.TestCase):

    GOOD = {"x": 0, "y": 0, "width": 900, "height": 700}

    def test_accepts_good_geometry(self):
        xtm.validate_geometry(dict(self.GOOD), "ctx")

    def test_accepts_negative_position(self):
        entry = dict(self.GOOD, x=-1920, y=-100)
        xtm.validate_geometry(entry, "ctx")

    def test_rejects_missing_field(self):
        entry = dict(self.GOOD)
        del entry["width"]
        with self.assertRaises(xtm.XtmError):
            xtm.validate_geometry(entry, "ctx")

    def test_rejects_string(self):
        with self.assertRaises(xtm.XtmError):
            xtm.validate_geometry(dict(self.GOOD, x="40"), "ctx")

    def test_rejects_bool(self):
        with self.assertRaises(xtm.XtmError):
            xtm.validate_geometry(dict(self.GOOD, x=True), "ctx")

    def test_rejects_none(self):
        with self.assertRaises(xtm.XtmError):
            xtm.validate_geometry(dict(self.GOOD, y=None), "ctx")

    def test_rejects_infinity(self):
        with self.assertRaises(xtm.XtmError):
            xtm.validate_geometry(dict(self.GOOD, width=float("inf")), "ctx")

    def test_rejects_nan(self):
        with self.assertRaises(xtm.XtmError):
            xtm.validate_geometry(dict(self.GOOD, x=float("nan")), "ctx")

    def test_rejects_zero_width(self):
        with self.assertRaises(xtm.XtmError):
            xtm.validate_geometry(dict(self.GOOD, width=0), "ctx")

    def test_rejects_negative_height(self):
        with self.assertRaises(xtm.XtmError):
            xtm.validate_geometry(dict(self.GOOD, height=-10), "ctx")

    def test_offsets_required_only_when_asked(self):
        xtm.validate_geometry(dict(self.GOOD), "ctx")
        with self.assertRaises(xtm.XtmError):
            xtm.validate_geometry(dict(self.GOOD), "ctx", require_offsets=True)


class TestSessionEntryValidation(unittest.TestCase):

    BASE = {"x": 0, "y": 0, "width": 800, "height": 600}

    def test_optional_fields_accepted(self):
        xtm.validate_session_entry(
            dict(self.BASE, command="htop", cwd="~/src",
                 xterm_args=["-fa", "Mono"]), "ctx")

    def test_rejects_non_string_command(self):
        with self.assertRaises(xtm.XtmError):
            xtm.validate_session_entry(dict(self.BASE, command=5), "ctx")

    def test_rejects_non_string_cwd(self):
        with self.assertRaises(xtm.XtmError):
            xtm.validate_session_entry(dict(self.BASE, cwd=["/tmp"]), "ctx")

    def test_rejects_non_list_xterm_args(self):
        with self.assertRaises(xtm.XtmError):
            xtm.validate_session_entry(dict(self.BASE, xterm_args="-fa"), "ctx")

    def test_rejects_bad_xterm_arg_element(self):
        with self.assertRaises(xtm.XtmError):
            xtm.validate_session_entry(
                dict(self.BASE, xterm_args=["-fa", {"a": 1}]), "ctx")

    def test_match_validation(self):
        xtm.validate_match({"hostname": "desk*"}, "ctx")
        xtm.validate_match({"hostname": ["desk*", "build-1"]}, "ctx")
        with self.assertRaises(xtm.XtmError):
            xtm.validate_match({"hostnam": "typo"}, "ctx")
        with self.assertRaises(xtm.XtmError):
            xtm.validate_match({"hostname": 5}, "ctx")
        with self.assertRaises(xtm.XtmError):
            xtm.validate_match({"hostname": ["ok", 5]}, "ctx")
        with self.assertRaises(xtm.XtmError):
            xtm.validate_match({"hostname": ""}, "ctx")
        with self.assertRaises(xtm.XtmError):
            xtm.validate_match({"hostname": []}, "ctx")
        with self.assertRaises(xtm.XtmError):
            xtm.validate_match({}, "ctx")
        with self.assertRaises(xtm.XtmError):
            xtm.validate_match("desk1", "ctx")

    def test_display_matching_was_removed(self):
        # A 0.3 config must fail with an explanation, not a generic
        # unknown-key error that leaves the user guessing.
        with self.assertRaises(xtm.XtmError) as caught:
            xtm.validate_match({"display": ":0*"}, "ctx")
        self.assertIn("display", str(caught.exception))
        self.assertIn("0.4", str(caught.exception))


class TestGeometrySpec(unittest.TestCase):

    def test_parses_valid_spec(self):
        self.assertEqual(xtm.parse_geometry_spec("0,0,900,700"),
                         {"x": 0, "y": 0, "width": 900, "height": 700})

    def test_allows_whitespace_and_negatives(self):
        self.assertEqual(xtm.parse_geometry_spec(" -1920, 0 , 900,700 "),
                         {"x": -1920, "y": 0, "width": 900, "height": 700})

    def test_rejects_wrong_field_count(self):
        for spec in ("1,2,3", "1,2,3,4,5", ""):
            with self.assertRaises(xtm.XtmError):
                xtm.parse_geometry_spec(spec)

    def test_rejects_non_numeric(self):
        with self.assertRaises(xtm.XtmError):
            xtm.parse_geometry_spec("0,0,wide,700")

    def test_rejects_non_positive_size(self):
        with self.assertRaises(xtm.XtmError):
            xtm.parse_geometry_spec("0,0,0,700")


class TestProfileValidation(unittest.TestCase):

    def test_injects_defaults(self):
        config = {"profiles": {"p": {}}}
        profile = xtm.validate_profile(config, "p")
        self.assertEqual(profile["sessions"], {})
        self.assertEqual(profile["stack"], xtm.DEFAULT_STACK)

    def test_unknown_profile_raises(self):
        with self.assertRaises(xtm.XtmError):
            xtm.validate_profile({"profiles": {}}, "nope")

    def test_non_mapping_profile_raises(self):
        with self.assertRaises(xtm.XtmError):
            xtm.validate_profile({"profiles": {"p": "oops"}}, "p")

    def test_non_mapping_sessions_raises(self):
        with self.assertRaises(xtm.XtmError):
            xtm.validate_profile({"profiles": {"p": {"sessions": []}}}, "p")

    def test_bad_session_entry_raises(self):
        config = {"profiles": {"p": {"sessions": {"a": 5}}}}
        with self.assertRaises(xtm.XtmError):
            xtm.validate_profile(config, "p")

    def test_session_geometry_drops_launch_settings(self):
        entry = {"x": 1, "y": 2, "width": 3, "height": 4, "command": "htop"}
        self.assertEqual(xtm.session_geometry(entry),
                         {"x": 1, "y": 2, "width": 3, "height": 4})


class TestSessionNaming(unittest.TestCase):
    """Slot-to-session resolution: prefixes, overrides and titles."""

    def test_prefix_is_applied_to_a_plain_slot(self):
        profile = {"prefix": "desk-", "sessions": {"work1": {}}}
        self.assertEqual(xtm.resolve_session_name(profile, "work1"), "desk-work1")

    def test_no_prefix_means_the_slot_name(self):
        profile = {"sessions": {"work1": {}}}
        self.assertEqual(xtm.resolve_session_name(profile, "work1"), "work1")

    def test_explicit_session_is_used_verbatim(self):
        # The override is literal: no prefix is applied to it, which is
        # what makes it usable for sharing a session between profiles.
        profile = {"prefix": "desk-", "sessions": {"notes": {"session": "notes"}}}
        self.assertEqual(xtm.resolve_session_name(profile, "notes"), "notes")

    def test_explicit_session_may_differ_entirely_from_the_slot(self):
        profile = {"prefix": "desk-",
                   "sessions": {"left": {"session": "anything-i-want"}}}
        self.assertEqual(xtm.resolve_session_name(profile, "left"),
                         "anything-i-want")

    def test_two_profiles_share_one_session_by_naming_it(self):
        a = {"prefix": "desk-", "sessions": {"notes": {"session": "notes"}}}
        b = {"prefix": "lab-", "sessions": {"notes": {"session": "notes"}}}
        self.assertEqual(xtm.resolve_session_name(a, "notes"),
                         xtm.resolve_session_name(b, "notes"))

    def test_two_profiles_do_not_collide_without_an_override(self):
        a = {"prefix": "desk-", "sessions": {"work1": {}}}
        b = {"prefix": "lab-", "sessions": {"work1": {}}}
        self.assertNotEqual(xtm.resolve_session_name(a, "work1"),
                            xtm.resolve_session_name(b, "work1"))

    def test_slot_lookup_by_session(self):
        profile = {"prefix": "desk-", "sessions": {"work1": {}, "n": {"session": "x"}}}
        self.assertEqual(xtm.slot_for_session(profile, "desk-work1"), "work1")
        self.assertEqual(xtm.slot_for_session(profile, "x"), "n")
        self.assertIsNone(xtm.slot_for_session(profile, "nope"))

    def test_default_title(self):
        profile = {"prefix": "desk-", "sessions": {"work1": {}}}
        self.assertEqual(
            xtm.resolve_window_title(profile, "desk", "work1", "desk-work1"),
            "xtm:desk-work1")

    def test_profile_title_template(self):
        profile = {"title": "{profile}: {slot}", "sessions": {"work1": {}}}
        self.assertEqual(
            xtm.resolve_window_title(profile, "desk", "work1", "work1"),
            "desk: work1")

    def test_slot_title_overrides_the_profile_template(self):
        profile = {"title": "{profile}: {slot}",
                   "sessions": {"work1": {"title": "Editor"}}}
        self.assertEqual(
            xtm.resolve_window_title(profile, "desk", "work1", "work1"), "Editor")

    def test_invalid_template_is_rejected_by_validation(self):
        with self.assertRaises(xtm.XtmError):
            xtm.validate_title_template("{nosuchfield}", "ctx")
        with self.assertRaises(xtm.XtmError):
            xtm.validate_title_template("{unclosed", "ctx")

    def test_prefix_validation(self):
        xtm.validate_prefix("desk-", "ctx")
        xtm.validate_prefix("", "ctx")
        with self.assertRaises(xtm.XtmError):
            xtm.validate_prefix("bad prefix ", "ctx")
        with self.assertRaises(xtm.XtmError):
            xtm.validate_prefix(7, "ctx")

    def test_switch_mode_validation(self):
        for mode in ("leave", "detach", "kill"):
            xtm.validate_switch_mode(mode, "ctx")
        with self.assertRaises(xtm.XtmError):
            xtm.validate_switch_mode("destroy", "ctx")

    def test_switch_mode_defaults_to_detach(self):
        self.assertEqual(xtm.switch_mode({}), "detach")
        self.assertEqual(xtm.switch_mode({"on_switch": "leave"}), "leave")
