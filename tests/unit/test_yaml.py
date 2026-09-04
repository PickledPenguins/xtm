"""Built-in YAML reader and writer, and backend selection."""

import unittest

from tests.support import (
    xtm,
)


class TestSimpleYaml(unittest.TestCase):
    """The dependency-free YAML reader/writer used when PyYAML is absent."""

    def round_trip(self, data):
        return xtm._simple_yaml_load(xtm._simple_yaml_dump(data) + "\n")

    def test_nested_mapping_round_trip(self):
        data = {"profiles": {"one": {"stack": {"x": 1, "y": -2, "width": 3,
                                               "height": 4}, "sessions": {}}}}
        self.assertEqual(self.round_trip(data), data)

    def test_empty_mapping_round_trips_as_mapping(self):
        self.assertEqual(self.round_trip({"sessions": {}}), {"sessions": {}})

    def test_list_round_trip(self):
        data = {"a": {"xterm_args": ["-fa", "Mono", "-fs", "11"]}}
        self.assertEqual(self.round_trip(data), data)

    def test_empty_list_round_trip(self):
        self.assertEqual(self.round_trip({"a": {"b": []}}), {"a": {"b": []}})

    def test_reserved_words_stay_strings(self):
        for word in ("null", "Null", "yes", "no", "on", "off", "true", "False"):
            self.assertEqual(self.round_trip({"a": {"b": word}})["a"]["b"], word)

    def test_numeric_looking_string_stays_a_string(self):
        self.assertEqual(self.round_trip({"a": {"b": "42"}})["a"]["b"], "42")

    def test_string_with_colon_round_trips(self):
        data = {"a": {"command": "ssh host: true"}}
        self.assertEqual(self.round_trip(data), data)

    def test_string_with_quotes_round_trips(self):
        data = {"a": {"command": 'echo "hi" && echo \'there\''}}
        self.assertEqual(self.round_trip(data), data)

    def test_a_doubled_quote_closes_nothing_in_a_single_quoted_scalar(self):
        """'' is YAML's escape for a quote inside single quotes.

        The scan checks that a quoted value closes on its last character
        and nowhere earlier, so it has to know that the doubled quote is
        one character of text rather than the end of the scalar.
        """
        parsed = xtm._simple_yaml_load("a:\n  b: 'it''s here'\n")
        self.assertEqual(parsed["a"]["b"], "it's here")

    def test_a_newline_or_tab_survives_the_round_trip(self):
        """A value carrying one is escaped, not written raw.

        Written literally inside the quotes it split the line, and the
        reader -- which is line-based -- then refused the file the dumper
        had just produced.
        """
        for value in ("a\nb", "a\tb", "a\n\tb"):
            data = {"a": {"command": value}}
            self.assertEqual(self.round_trip(data), data,
                             "%r did not survive the round trip" % (value,))

    def test_a_literal_backslash_n_is_not_a_newline(self):
        """`a\\nb` is a backslash and an n, and stays that way.

        The escapes are resolved in one left-to-right pass for this reason:
        chained replaces would turn the escaped backslash's `n` into a
        newline.
        """
        data = {"a": {"command": "a\\nb"}}
        self.assertEqual(self.round_trip(data), data)
        self.assertEqual(xtm._unescape_double_quoted('a\\\\nb'), 'a\\nb')

    def test_leading_dash_string_round_trips(self):
        self.assertEqual(self.round_trip({"a": {"b": "-fa"}})["a"]["b"], "-fa")

    def test_negative_and_float_numbers(self):
        parsed = xtm._simple_yaml_load("a:\n  x: -40\n  y: 1.5\n")
        self.assertEqual(parsed["a"]["x"], -40)
        self.assertAlmostEqual(parsed["a"]["y"], 1.5)

    def test_inline_comment_is_stripped(self):
        text = ("profiles:\n"
                "  one:            # the layout for this desk\n"
                "    stack:        # default slot\n"
                "      x: 40       # left edge\n"
                "      y: 40\n")
        parsed = xtm._simple_yaml_load(text)
        self.assertEqual(parsed["profiles"]["one"]["stack"]["x"], 40)
        self.assertEqual(parsed["profiles"]["one"]["stack"]["y"], 40)

    def test_hash_inside_quoted_string_is_not_a_comment(self):
        parsed = xtm._simple_yaml_load('a:\n  command: "echo #1"\n')
        self.assertEqual(parsed["a"]["command"], "echo #1")

    def test_hash_without_leading_space_is_not_a_comment(self):
        parsed = xtm._simple_yaml_load("a:\n  b: c#d\n")
        self.assertEqual(parsed["a"]["b"], "c#d")

    def test_full_line_comment_ignored(self):
        parsed = xtm._simple_yaml_load("# leading\na:\n  # inner\n  b: 1\n")
        self.assertEqual(parsed, {"a": {"b": 1}})

    def test_flow_mapping(self):
        parsed = xtm._simple_yaml_load(
            "sessions:\n  work1: {x: 0, y: 0, width: 900, height: 700}\n")
        self.assertEqual(parsed["sessions"]["work1"],
                         {"x": 0, "y": 0, "width": 900, "height": 700})

    def test_flow_mapping_with_quoted_comma(self):
        parsed = xtm._simple_yaml_load(
            'a: {command: "sleep 1, then go", x: 1}\n')
        self.assertEqual(parsed["a"]["command"], "sleep 1, then go")
        self.assertEqual(parsed["a"]["x"], 1)

    def test_flow_mapping_with_nested_list(self):
        parsed = xtm._simple_yaml_load('a: {xterm_args: [-fa, Mono], x: 2}\n')
        self.assertEqual(parsed["a"]["xterm_args"], ["-fa", "Mono"])
        self.assertEqual(parsed["a"]["x"], 2)

    def test_empty_flow_mapping(self):
        self.assertEqual(xtm._simple_yaml_load("a: {}\n"), {"a": {}})

    def test_tabs_are_rejected(self):
        with self.assertRaises(xtm.XtmError):
            xtm._simple_yaml_load("a:\n\tb: 1\n")

    def test_odd_indentation_is_rejected(self):
        with self.assertRaises(xtm.XtmError):
            xtm._simple_yaml_load("a:\n   b: 1\n")

    def test_line_without_colon_is_rejected(self):
        with self.assertRaises(xtm.XtmError):
            xtm._simple_yaml_load("a:\n  bcd\n")

    def test_malformed_flow_mapping_is_rejected(self):
        with self.assertRaises(xtm.XtmError):
            xtm._simple_yaml_load("a: {x}\n")

    def test_dedent_back_to_top_level(self):
        parsed = xtm._simple_yaml_load("a:\n  b: 1\nc:\n  d: 2\n")
        self.assertEqual(parsed, {"a": {"b": 1}, "c": {"d": 2}})

    def test_strip_inline_comment_helper(self):
        self.assertEqual(xtm._strip_inline_comment("x: 1 # two").rstrip(), "x: 1")
        self.assertEqual(xtm._strip_inline_comment("#all"), "")
        self.assertEqual(xtm._strip_inline_comment("x: 1"), "x: 1")

    def test_split_top_level_respects_nesting(self):
        self.assertEqual(xtm._split_top_level("a, [b, c], {d: e}"),
                         ["a", " [b, c]", " {d: e}"])


class TestYamlBackendSelection(unittest.TestCase):
    """dump_yaml must survive a PyYAML too old for sort_keys=."""

    def test_falls_back_when_pyyaml_rejects_sort_keys(self):
        class OldPyYaml(object):
            YAMLError = ValueError

            @staticmethod
            def safe_dump(data, **kwargs):
                raise TypeError("unexpected keyword argument 'sort_keys'")

        original_flag, original_mod = xtm.HAVE_PYYAML, getattr(xtm, "_pyyaml", None)
        xtm.HAVE_PYYAML = True
        xtm._pyyaml = OldPyYaml
        try:
            text = xtm.dump_yaml({"profiles": {"a": {"sessions": {}}}})
        finally:
            xtm.HAVE_PYYAML = original_flag
            if original_mod is not None:
                xtm._pyyaml = original_mod
        self.assertIn("profiles:", text)
        self.assertIn("sessions: {}", text)


class TestPyYamlErrorPath(unittest.TestCase):

    @unittest.skipUnless(xtm.HAVE_PYYAML, "PyYAML is not installed")
    def test_invalid_yaml_reports_cleanly(self):
        with self.assertRaises(xtm.XtmError):
            xtm.load_yaml("profiles:\n  a: [unclosed\n")
