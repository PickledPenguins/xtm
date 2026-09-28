"""Reading and writing the config: PyYAML, with keys read as names."""

import unittest

from tests.support import (
    xtm,
)


def load(text):
    return xtm.load_yaml(text)


def round_trip(data):
    return xtm.load_yaml(xtm.dump_yaml(data))


class TestKeysAreNames(unittest.TestCase):
    """A key is the text written, never a boolean, a number or a null."""

    def test_boolean_spellings_stay_names(self):
        for word in ("on", "off", "yes", "no", "true", "False", "y", "n"):
            data = load("%s: 1\n" % (word,))
            self.assertEqual(list(data), [word])

    def test_number_spellings_keep_their_text(self):
        # YAML 1.1 reads 0755 as 493, 1:30 as 90 and 7. as 7.0; as names
        # they must stay exactly what was typed.
        for text in ("123", "0755", "1_000", "1:30", "7.", "0x1f", "1e3"):
            data = load("'%s': 1\n" % (text,) if ":" in text else "%s: 1\n" % (text,))
            self.assertEqual(list(data), [text])

    def test_null_spellings_stay_names(self):
        for text in ("~", "null", "Null"):
            self.assertEqual(list(load("%s: 1\n" % (text,))), [text])

    def test_a_date_key_stays_text(self):
        self.assertEqual(list(load("2026-01-01: 1\n")), ["2026-01-01"])

    def test_nested_keys_are_names_too(self):
        data = load("profiles:\n  on:\n    sessions:\n      0755: {x: 1}\n")
        self.assertEqual(list(data["profiles"]), ["on"])
        self.assertEqual(list(data["profiles"]["on"]["sessions"]), ["0755"])

    def test_a_quoted_key_is_its_unquoted_text(self):
        self.assertEqual(list(load('"a b": 1\n')), ["a b"])
        self.assertEqual(list(load("'it''s': 1\n")), ["it's"])

    def test_a_collection_as_a_key_is_refused(self):
        with self.assertRaises(xtm.XtmError) as caught:
            load("? [a, b]\n: 1\n")
        self.assertIn("a key must be a name", str(caught.exception))


class TestDuplicateKeys(unittest.TestCase):
    """YAML keeps the last of two equal keys without a word; xtm refuses."""

    def test_a_slot_written_twice_is_refused_with_its_line(self):
        text = ("profiles:\n"
                "  desk:\n"
                "    sessions:\n"
                "      logs: {x: 0, y: 0, width: 9, height: 9}\n"
                "      logs: {x: 5, y: 0, width: 9, height: 9}\n")
        with self.assertRaises(xtm.XtmError) as caught:
            load(text)
        message = str(caught.exception)
        self.assertIn("the key 'logs' appears twice", message)
        self.assertIn("line 5", message)

    def test_a_key_beside_a_merge_overrides_it_without_counting_twice(self):
        data = load("base: &b {width: 1, height: 2}\n"
                    "slot: {<<: *b, width: 9}\n")
        self.assertEqual(data["slot"], {"width": 9, "height": 2})

    def test_equal_keys_in_different_mappings_are_fine(self):
        data = load("a: {x: 1}\nb: {x: 2}\n")
        self.assertEqual(data, {"a": {"x": 1}, "b": {"x": 2}})


class TestValuesKeepYamlMeaning(unittest.TestCase):
    """Values are typed as every YAML reader would type them."""

    def test_booleans_and_numbers(self):
        data = load("a: yes\nb: 0755\nc: 1.5\nd: -3\ne: ~\n")
        self.assertEqual(data, {"a": True, "b": 493, "c": 1.5, "d": -3,
                                "e": None})

    def test_quoting_keeps_text(self):
        self.assertEqual(load("a: 'yes'\nb: \"0755\"\n"),
                         {"a": "yes", "b": "0755"})

    def test_anchors_aliases_and_merge_keys_work(self):
        text = ("base: &base {width: 900, height: 700}\n"
                "slot:\n"
                "  <<: *base\n"
                "  x: 10\n")
        self.assertEqual(load(text)["slot"],
                         {"width": 900, "height": 700, "x": 10})

    def test_comments_and_a_document_marker_are_fine(self):
        self.assertEqual(load("---\n# note\na: 1  # trailing\n"), {"a": 1})


class TestWriting(unittest.TestCase):
    """What xtm writes, it reads back unchanged, in the same order."""

    def test_nested_config_round_trips(self):
        data = {"profiles": {"one": {
            "stack": {"x": 1, "y": -2, "width": 3, "height": 4,
                      "offset_x": 5, "offset_y": 6},
            "sessions": {"a": {"x": 0, "y": 0, "width": 9, "height": 9,
                               "xterm_args": ["-fa", "Mono", "-fs", "11"]}},
        }}}
        self.assertEqual(round_trip(data), data)

    def test_key_order_is_kept_not_sorted(self):
        data = {"z": 1, "a": 2, "m": 3}
        self.assertEqual(list(round_trip(data)), ["z", "a", "m"])
        self.assertLess(xtm.dump_yaml(data).index("z:"),
                        xtm.dump_yaml(data).index("a:"))

    def test_text_that_looks_typed_is_quoted_on_the_way_out(self):
        data = {"a": {"b": "yes", "c": "0755", "d": "null", "e": "1:30",
                      "f": "2026-01-01", "g": "~"}}
        self.assertEqual(round_trip(data), data)

    def test_key_names_that_look_typed_survive(self):
        data = {"profiles": {"on": {}, "0755": {}, "true": {}}}
        self.assertEqual(round_trip(data), data)

    def test_block_style_and_no_aliases(self):
        shared = {"x": 1}
        text = xtm.dump_yaml({"a": shared, "b": shared})
        self.assertNotIn("&", text)
        self.assertNotIn("*", text)
        self.assertNotIn("{", text)

    def test_newlines_and_unicode_survive(self):
        data = {"a": {"command": "echo 'x'\nsecond", "title": "café ✓"}}
        self.assertEqual(round_trip(data), data)
        self.assertIn("café", xtm.dump_yaml(data))


class TestErrors(unittest.TestCase):

    def test_invalid_yaml_names_the_line_and_column(self):
        with self.assertRaises(xtm.XtmError) as caught:
            load("profiles:\n  a: {x: 1\n")
        message = str(caught.exception)
        self.assertRegex(message, r"line \d+, column \d+")
        self.assertNotIn("<unicode string>", message)

    def test_a_tab_in_the_indentation_names_its_line(self):
        with self.assertRaises(xtm.XtmError) as caught:
            load("profiles:\n\tdesk: {}\n")
        self.assertIn("line 2, column 1", str(caught.exception))

    def test_missing_pyyaml_says_how_to_install_it(self):
        saved = xtm.yaml
        xtm.yaml = None
        self.addCleanup(setattr, xtm, "yaml", saved)
        with self.assertRaises(xtm.XtmError) as caught:
            load("a: 1\n")
        self.assertIn("PyYAML", str(caught.exception))
        self.assertIn("pip install --user PyYAML", str(caught.exception))
        with self.assertRaises(xtm.XtmError):
            xtm.dump_yaml({"a": 1})


if __name__ == "__main__":
    unittest.main()
