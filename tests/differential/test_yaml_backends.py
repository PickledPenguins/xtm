"""The two YAML backends must read every config identically."""

import unittest

from tests.support import (
    REPO_ROOT,
    xtm,
)


class TestYamlBackendEquivalence(unittest.TestCase):
    """The two YAML backends must read every config identically.

    This is the test whose absence let a whole class of bug through. xtm
    picks PyYAML when it is installed and its own parser when it is not, so
    every config value has two implementations behind it. PyYAML implements
    YAML 1.1, which reads 0755 as octal 493, 1:30 as sexagesimal 90 and
    `yes` as boolean True; the built-in parser reads none of those that way.
    A config could therefore place a window at two different coordinates
    depending only on what happened to be installed -- silently, since both
    readings pass validation.

    Rather than assert a specific reading, these tests assert AGREEMENT:
    whatever the two backends do with a value, they must do the same thing.
    That is the property users actually depend on, and it stays meaningful
    as either backend changes.
    """

    # Values that must survive unchanged and mean the same thing to both.
    AGREE = [
        "0", "1", "-1", "1.5", "0.5", ".5",
        "true", "True", "false", "null", "~", "Null", "None",
        "y", "n", "1,2", "'quoted'", '"quoted"',
        # Real-world values that merely LOOK numeric at the edges. These are
        # the false positives an over-eager ambiguity guard would break.
        ".config/xtm", "-e", "2>&1", "my-session", "2026-01-01-backup",
        "/home/user/work", "tmux-1.9", "session_01",
    ]

    # Values the two backends read differently. Every one of these must be
    # refused, so neither backend gets to act on its own interpretation.
    REJECTED = [
        "0755", "007", "08", "09",        # leading zero: octal vs decimal
        "0x1f", "0b101", "0o755",         # base prefixes: PyYAML only
        "1_000",                          # digit separators: PyYAML only
        "1:30", "1:30:00", "-1:30",       # sexagesimal: PyYAML only
        "1e3", "1.0e3",                   # scientific notation: disagree
        ".inf", "-.inf", ".nan",          # non-finite: PyYAML only
        "yes", "no", "on", "off",         # YAML 1.1 booleans: PyYAML only
        "2026-01-01",                     # date object: PyYAML only
    ]

    def setUp(self):
        if not xtm.HAVE_PYYAML:
            self.skipTest("PyYAML is not installed, so there is nothing to "
                          "compare the built-in parser against")
        self._saved = xtm.HAVE_PYYAML
        self.addCleanup(setattr, xtm, "HAVE_PYYAML", self._saved)

    def _read(self, text, use_pyyaml):
        """Parse `text` with one specific backend, or report its refusal."""
        xtm.HAVE_PYYAML = use_pyyaml
        try:
            return ("ok", xtm.load_yaml(text))
        except xtm.XtmError as e:
            return ("rejected", str(e))

    def _both(self, text):
        return self._read(text, False), self._read(text, True)

    def test_agreeing_values_are_read_identically(self):
        for token in self.AGREE:
            text = "profiles:\n  a: %s\n" % (token,)
            builtin, pyyaml_ = self._both(text)
            self.assertEqual(
                builtin, pyyaml_,
                "backends disagree on %r: built-in gave %r, PyYAML gave %r"
                % (token, builtin, pyyaml_))
            self.assertEqual(builtin[0], "ok",
                             "%r should be accepted, got %r" % (token, builtin))

    def test_ambiguous_values_are_refused_by_both(self):
        for token in self.REJECTED:
            text = "profiles:\n  a: %s\n" % (token,)
            builtin, pyyaml_ = self._both(text)
            self.assertEqual(
                builtin[0], "rejected",
                "built-in parser accepted ambiguous %r as %r" % (token, builtin))
            self.assertEqual(
                pyyaml_[0], "rejected",
                "PyYAML path accepted ambiguous %r as %r" % (token, pyyaml_))

    def test_ambiguous_values_are_allowed_when_quoted(self):
        # Quoting is how a user says "this is literal text", and both
        # backends agree on quoted scalars, so the guard must not fire.
        for token in ("yes", "0755", "1:30", "2026-01-01", ".inf"):
            text = 'profiles:\n  a: {title: "%s"}\n' % (token,)
            builtin, pyyaml_ = self._both(text)
            self.assertEqual(builtin, pyyaml_, "disagreement on quoted %r" % (token,))
            self.assertEqual(builtin[0], "ok",
                             "quoted %r should be accepted, got %r" % (token, builtin))
            self.assertEqual(builtin[1]["profiles"]["a"]["title"], token)

    def test_malformed_input_is_refused_by_both(self):
        # The built-in parser used to fall through to the plain-text branch
        # here, silently accepting what PyYAML rejects outright.
        for text in ('profiles:\n  a: "unterminated\n',
                     "profiles:\n  a: {x: 1, y: 2\n",
                     "profiles:\n  a: [1, 2\n"):
            builtin, pyyaml_ = self._both(text)
            self.assertEqual(builtin[0], "rejected",
                             "built-in parser accepted malformed %r" % (text,))
            self.assertEqual(pyyaml_[0], "rejected",
                             "PyYAML path accepted malformed %r" % (text,))

    # Values whose FIRST character is one of YAML's structural indicators.
    # PyYAML resolves or refuses each of them; the built-in parser used to
    # hand back the text unchanged, so the same config meant two things.
    STRUCTURAL = [
        "&anchor 5", "&anchor", "*alias",       # anchors and aliases
        "!!str 7", "!tag",                      # tags
        "|", "|-", ">", ">2", "| grep", "> out",  # block scalar headers
        "%directive", "@reserved", "`backtick",   # reserved indicators
        ",leading-comma", "]stray-close", "}stray-close",
        "<<", "=",                              # reserved whole tokens
    ]

    # Flow collections the two backends split differently. `{x:0, y:0}` is
    # the one a person would really type: two keys with no values to PyYAML,
    # and x=0 y=0 to the built-in parser.
    AMBIGUOUS_FLOW = [
        "{x:0, y:0}", "{a}", "[a, b,]", "[b40: 2]", "[]trailing",
    ]

    # Flow collections that must keep working. A fontconfig font name really
    # does contain colons, which is why quoting exempts an item.
    VALID_FLOW = [
        "{x: 0, y: 0}", "{x: 0,y: 0}", "{a:}", "[a, b]", "[-fn, Monospace]",
        "[-]", '["Monospace:size=11"]', "[]", "{}",
    ]

    # The same characters INSIDE a value, where both backends agree and the
    # guard must stay silent. These are ordinary settings a real config has.
    STRUCTURAL_FALSE_POSITIVES = [
        "tail -f /var/log/x | grep error", "sleep 5 &", "user@host",
        "50%", "a*b", "a!b", "ssh host:port", "http://example/x",
        "-fn", "--wait", "2>&1",
    ]

    def test_structural_indicators_are_refused_by_both(self):
        for token in self.STRUCTURAL:
            text = "profiles:\n  a: %s\n" % (token,)
            builtin, pyyaml_ = self._both(text)
            self.assertEqual(
                builtin[0], "rejected",
                "built-in parser accepted structural %r as %r" % (token, builtin))
            self.assertEqual(
                pyyaml_[0], "rejected",
                "PyYAML path accepted structural %r as %r" % (token, pyyaml_))

    def test_ordinary_values_containing_indicators_are_accepted(self):
        for token in self.STRUCTURAL_FALSE_POSITIVES:
            text = "profiles:\n  a:\n    sessions:\n      w: {command: %s}\n" % (token,)
            builtin, pyyaml_ = self._both(text)
            self.assertEqual(builtin, pyyaml_,
                             "backends disagree on %r" % (token,))
            self.assertEqual(builtin[0], "ok",
                             "%r should be accepted, got %r" % (token, builtin))
            self.assertEqual(
                builtin[1]["profiles"]["a"]["sessions"]["w"]["command"], token)

    def test_structural_indicators_are_allowed_when_quoted(self):
        for token in ("&anchor", "*alias", "!tag", "|", "%directive"):
            text = 'profiles:\n  a: {title: "%s"}\n' % (token,)
            builtin, pyyaml_ = self._both(text)
            self.assertEqual(builtin, pyyaml_, "disagreement on quoted %r" % (token,))
            self.assertEqual(builtin[0], "ok",
                             "quoted %r should be accepted, got %r" % (token, builtin))
            self.assertEqual(builtin[1]["profiles"]["a"]["title"], token)

    def test_ambiguous_flow_collections_are_refused_by_both(self):
        for token in self.AMBIGUOUS_FLOW:
            text = "profiles:\n  a: %s\n" % (token,)
            builtin, pyyaml_ = self._both(text)
            self.assertEqual(
                builtin[0], "rejected",
                "built-in parser accepted %r as %r" % (token, builtin))
            self.assertEqual(
                pyyaml_[0], "rejected",
                "PyYAML path accepted %r as %r" % (token, pyyaml_))

    def test_ordinary_flow_collections_are_accepted(self):
        for token in self.VALID_FLOW:
            text = "profiles:\n  a: %s\n" % (token,)
            builtin, pyyaml_ = self._both(text)
            self.assertEqual(builtin, pyyaml_,
                             "backends disagree on %r" % (token,))
            self.assertEqual(builtin[0], "ok",
                             "%r should be accepted, got %r" % (token, builtin))

    def test_quotes_inside_a_plain_scalar_are_ordinary_text(self):
        """The built-in parser must not be STRICTER than PyYAML either.

        Scanning every token for balanced quotes refused `sed 's/a/b/'` and
        `say "hi"` -- valid YAML that PyYAML reads without complaint. Only
        a token that OPENS with a quote is a quoted scalar.
        """
        # Inside a flow mapping, where the value is one item among several.
        for token in ("sed 's/a/b/'", 'say "hi"', 'ok"', "ok'", "a\"b"):
            text = "profiles:\n  a:\n    sessions:\n      w: {command: %s}\n" % (token,)
            builtin, pyyaml_ = self._both(text)
            self.assertEqual(builtin, pyyaml_, "disagreement on %r" % (token,))
            self.assertEqual(builtin[0], "ok",
                             "%r should be accepted, got %r" % (token, builtin))
        # In block style, where a brace is not in a flow context either.
        # Both are tested because "{" IS an indicator inside a flow mapping,
        # so the same token is legitimately read two ways in two places.
        for token in ("3{x}", "a}b", "a[b", "a{b"):
            text = "profiles:\n  a:\n    title: %s\n" % (token,)
            builtin, pyyaml_ = self._both(text)
            self.assertEqual(builtin, pyyaml_, "disagreement on %r" % (token,))
            self.assertEqual(builtin[0], "ok",
                             "%r should be accepted, got %r" % (token, builtin))

    def test_a_quoted_value_may_contain_a_bracket(self):
        """A bracket inside a quoted flow-mapping value is ordinary text.

        The quote that opens a mapping's VALUE follows a colon, not a
        comma or a brace, so a scan that only opened a quoted scalar after
        "[", "{" and "," read the value as plain text and counted the
        bracket inside it as structure. `title: "v}w"` then closed the
        mapping early and the built-in parser refused a config PyYAML
        reads without complaint -- a config that loads on a machine with
        PyYAML installed and fails on one without it, which is the
        divergence this whole module exists to prevent.
        """
        for value in ('"v}w"', '"v]w"', '"{"', '"["', '"]"', '"a, b"'):
            text = ('profiles:\n  default:\n    sessions:\n'
                    '      s: {x: 0, y: 0, width: 1, height: 1, title: %s}\n'
                    % (value,))
            builtin, pyyaml_ = self._both(text)
            self.assertEqual(
                builtin, pyyaml_,
                "backends disagree on the quoted value %s: built-in gave %r, "
                "PyYAML gave %r" % (value, builtin, pyyaml_))
            self.assertEqual(builtin[0], "ok",
                             "%s should be accepted, got %r" % (value, builtin))

    def test_a_dash_is_ordinary_text_in_a_flow_mapping_too(self):
        """`{title: -}` is the one-character string "-" to both backends.

        A dash opens a block sequence only where a block sequence may
        begin, which is not inside a flow collection. The exemption was
        given to flow SEQUENCE items and not to flow MAPPING values, so
        the built-in parser refused `{title: -}` while PyYAML read it.
        """
        text = ('profiles:\n  default:\n    sessions:\n'
                '      s: {x: 0, y: 0, width: 1, height: 1, title: -}\n')
        builtin, pyyaml_ = self._both(text)
        self.assertEqual(builtin, pyyaml_,
                         "backends disagree on a dash as a flow-mapping "
                         "value: built-in gave %r, PyYAML gave %r"
                         % (builtin, pyyaml_))
        self.assertEqual(builtin[0], "ok")

    def test_a_quoted_comma_inside_a_flow_value_splits_the_same_way(self):
        """`{command: echo "hi, there"}` must mean one thing, not two.

        A quote opens a quoted scalar only where a scalar may begin. Mid
        scalar it is ordinary text, so YAML still splits the entry at the
        comma; the built-in splitter treated the quote as a delimiter,
        did not split, and read one command where PyYAML read a truncated
        command and a stray key. Silent on both sides, and it changed
        what the terminal would run.
        """
        text = ('profiles:\n  default:\n    sessions:\n'
                '      s: {x: 0, y: 0, width: 1, height: 1, '
                'command: echo "hi, there"}\n')
        builtin, pyyaml_ = self._both(text)
        self.assertEqual(builtin, pyyaml_,
                         "backends disagree on a quoted comma inside a flow "
                         "value: built-in gave %r, PyYAML gave %r"
                         % (builtin, pyyaml_))

    def test_escapes_in_a_double_quoted_value_are_refused(self):
        """Double quotes are the one quoted form YAML processes escapes in.

        PyYAML turns "a\\tb" into a real tab; the built-in loader knows
        only \\" and \\\\ and keeps the backslash. Neither reported
        anything, so a title or command meant two different strings on
        two machines. Refusing the escape is what makes the two agree,
        and single quotes are the spelling that carries a literal
        backslash.
        """
        for value in ('"a\\tb"', '"a\\nb"', '"\\u00e9"', '"a\\zb"'):
            text = "profiles:\n  a: %s\n" % (value,)
            builtin, pyyaml_ = self._both(text)
            self.assertEqual(builtin[0], "rejected",
                             "%s should be refused, got %r" % (value, builtin))
            self.assertEqual(pyyaml_[0], "rejected",
                             "%s should be refused, got %r" % (value, pyyaml_))
        for value in ('"a\\\\b"', '"a\\"b"', "'a\\tb'"):
            text = "profiles:\n  a: %s\n" % (value,)
            builtin, pyyaml_ = self._both(text)
            self.assertEqual(builtin, pyyaml_,
                             "backends disagree on %s: %r vs %r"
                             % (value, builtin, pyyaml_))
            self.assertEqual(builtin[0], "ok",
                             "%s should be accepted, got %r" % (value, builtin))

    def test_a_key_with_no_value_is_null_in_both(self):
        """`empty:` is null, which is what PyYAML makes of it.

        Reading it as an empty mapping instead made a profile written
        with no body a valid empty profile without PyYAML and a fatal
        "must be a mapping" with it -- one config file, usable on one
        machine and not the other.
        """
        text = "profiles:\n  default:\n    prefix: d-\n  empty:\n"
        builtin, pyyaml_ = self._both(text)
        self.assertEqual(builtin, pyyaml_,
                         "backends disagree on a key with no value: %r vs %r"
                         % (builtin, pyyaml_))
        self.assertIsNone(builtin[1]["profiles"]["empty"])

    def test_single_quotes_carry_an_ambiguous_value_unchanged(self):
        """Single quotes have no escapes, so they are the safe spelling.

        Whatever is between them is text to both backends, which is what
        the escape rule for double quotes tells the user to fall back to.
        """
        for token in ("yes", "0755", "1:30", "a\\tb", "it''s"):
            text = "profiles:\n  a: '%s'\n" % (token,)
            builtin, pyyaml_ = self._both(text)
            self.assertEqual(builtin, pyyaml_,
                             "disagreement on the single-quoted %r: %r vs %r"
                             % (token, builtin, pyyaml_))
            self.assertEqual(builtin[0], "ok",
                             "single-quoted %r should be accepted, got %r"
                             % (token, builtin))

    def test_an_empty_flow_mapping_entry_is_refused_by_both(self):
        """`{,}` and `{a: 1,,}` are a stray comma, and the two backends
        disagree about what is left: PyYAML refuses the input outright
        while the built-in parser dropped the empty entry and carried on.
        """
        for value in ("{,}", "{x: 1,,}"):
            text = "profiles:\n  a: %s\n" % (value,)
            builtin, pyyaml_ = self._both(text)
            self.assertEqual(builtin[0], "rejected",
                             "%s should be refused, got %r" % (value, builtin))
            self.assertEqual(pyyaml_[0], "rejected",
                             "%s should be refused, got %r" % (value, pyyaml_))

    def test_a_flow_key_opening_with_an_indicator_is_refused_by_both(self):
        """A key is as capable of carrying an indicator as a value is."""
        for value in ("{&k: 1}", "{*k: 1}", "{!k: 1}"):
            text = "profiles:\n  a: %s\n" % (value,)
            builtin, pyyaml_ = self._both(text)
            self.assertEqual(builtin[0], "rejected",
                             "%s should be refused, got %r" % (value, builtin))
            self.assertEqual(pyyaml_[0], "rejected",
                             "%s should be refused, got %r" % (value, pyyaml_))

    def test_an_ambiguous_key_is_refused_by_both(self):
        """A key is typed the way a value is, so it can diverge the same way.

        `yes:` is the key True to PyYAML and the key "yes" to the built-in
        parser, and the normalisation that follows turns those into the
        profile names "True" and "yes" -- one config file naming two
        different profiles depending on what is installed.

        A purely numeric key is deliberately NOT refused: PyYAML's 2 and
        the built-in parser's "2" both normalise to the name "2", so they
        already agree where it counts.
        """
        for key in ("yes", "no", "on", "off", "0755", "1:30", "1_000"):
            for template in ("profiles:\n  %s: {}\n",
                             "profiles:\n  a: {%s: 1}\n"):
                text = template % (key,)
                builtin, pyyaml_ = self._both(text)
                self.assertEqual(
                    builtin[0], "rejected",
                    "the key %r should be refused, got %r" % (key, builtin))
                self.assertEqual(
                    pyyaml_[0], "rejected",
                    "the key %r should be refused, got %r" % (key, pyyaml_))

    def test_a_quoted_key_keeps_its_text(self):
        """Quoting a key is how the user says it is a name, and it works."""
        for key in ('"2"', "'2'", '"true"', '"1:30"'):
            text = "profiles:\n  %s: {}\n" % (key,)
            builtin, pyyaml_ = self._both(text)
            self.assertEqual(builtin, pyyaml_,
                             "disagreement on the quoted key %s: %r vs %r"
                             % (key, builtin, pyyaml_))
            self.assertEqual(builtin[0], "ok",
                             "%s should be accepted, got %r" % (key, builtin))
            self.assertEqual(list(builtin[1]["profiles"]), [key[1:-1]])

    def test_an_opening_quote_must_still_close(self):
        """The half of that rule which has to keep firing."""
        for token in ('"unterminated', "'unterminated", '"x" trailing',
                      "{x: 1, y: 2", "[1, 2"):
            text = "profiles:\n  a: %s\n" % (token,)
            builtin, pyyaml_ = self._both(text)
            self.assertEqual(builtin[0], "rejected",
                             "built-in parser accepted %r" % (token,))
            self.assertEqual(pyyaml_[0], "rejected",
                             "PyYAML path accepted %r" % (token,))

    def test_tabs_are_refused_anywhere_in_a_line(self):
        """PyYAML refuses a tab inside a plain scalar; so must the guard."""
        for text in ("profiles:\n  a: 1\t2\n", "profiles:\n\ta: {}\n"):
            builtin, pyyaml_ = self._both(text)
            self.assertEqual(builtin[0], "rejected", "built-in accepted %r" % (text,))
            self.assertEqual(pyyaml_[0], "rejected", "PyYAML accepted %r" % (text,))

    def test_the_merge_key_is_refused_by_both(self):
        """The worst of the divergences: no error on either side.

        PyYAML folds the named mapping in, so the profile HAS the prefix;
        the built-in parser treats `<<` as an ordinary key, so it does not.
        Both readings load cleanly, which is what made this one silent.
        """
        for text in ("profiles:\n  a:\n    <<: {prefix: x-}\n    sessions: {}\n",
                     "profiles:\n  a: {<<: {prefix: x-}, sessions: {}}\n"):
            builtin, pyyaml_ = self._both(text)
            self.assertEqual(builtin[0], "rejected",
                             "built-in parser accepted a merge key: %r" % (builtin,))
            self.assertEqual(pyyaml_[0], "rejected",
                             "PyYAML path accepted a merge key: %r" % (pyyaml_,))

    def test_document_markers_are_refused_by_both(self):
        for text in ("---\nprofiles:\n  a: {sessions: {}}\n",
                     "profiles:\n  a: {sessions: {}}\n...\n"):
            builtin, pyyaml_ = self._both(text)
            self.assertEqual(builtin[0], "rejected",
                             "built-in parser accepted a document marker")
            self.assertEqual(pyyaml_[0], "rejected",
                             "PyYAML path accepted a document marker")

    def test_indentation_rules_apply_under_both_backends(self):
        """Odd indentation and tabs are a divergence like any other.

        The built-in parser has always refused them; PyYAML accepts both,
        so these two rules had to move in front of the backend choice
        rather than living inside one of the backends.
        """
        for text in ("profiles:\n   a:\n      sessions: {}\n",
                     "profiles:\n\ta:\n\t\tsessions: {}\n"):
            builtin, pyyaml_ = self._both(text)
            self.assertEqual(builtin[0], "rejected", "built-in accepted %r" % (text,))
            self.assertEqual(pyyaml_[0], "rejected", "PyYAML path accepted %r" % (text,))

    def test_quoted_key_containing_a_colon_agrees(self):
        # str.partition(":") used to cut this key in half, so the built-in
        # parser recovered '"bad' where PyYAML recovered 'bad:name'.
        text = 'profiles:\n  a:\n    sessions:\n      "bad:name": {x: 0}\n'
        builtin, pyyaml_ = self._both(text)
        self.assertEqual(builtin, pyyaml_)
        self.assertIn("bad:name",
                      builtin[1]["profiles"]["a"]["sessions"])

    def test_flow_mapping_values_are_scanned(self):
        # Geometry is normally written in flow style, so the guard has to
        # descend into it -- this is the form the bug would really take.
        text = "profiles:\n  a:\n    stack: {x: 0755, y: 0}\n"
        builtin, pyyaml_ = self._both(text)
        self.assertEqual(builtin[0], "rejected")
        self.assertEqual(pyyaml_[0], "rejected")

    def test_flow_sequence_values_are_scanned(self):
        text = "profiles:\n  a:\n    sessions:\n      w: {xterm_args: [-fn, 0755]}\n"
        builtin, pyyaml_ = self._both(text)
        self.assertEqual(builtin[0], "rejected")
        self.assertEqual(pyyaml_[0], "rejected")

    def test_comments_are_not_scanned(self):
        # A comment mentioning an ambiguous value must not trip the guard.
        text = "profiles:\n  a:\n    stack: {x: 0, y: 0}  # not 0755 or yes\n"
        builtin, pyyaml_ = self._both(text)
        self.assertEqual(builtin, pyyaml_)
        self.assertEqual(builtin[0], "ok")

    def test_xtm_own_output_survives_its_own_guard(self):
        """Whatever xtm writes, both backends must read back unchanged.

        Guards against the guard: a rule that rejected xtm's own dump format
        would make the tool unable to reload the config it just saved.
        """
        config = {
            "profiles": {
                "default": {
                    "prefix": "d-",
                    "stack": dict(xtm.DEFAULT_STACK),
                    "sessions": {
                        # Deliberately awkward values: words that are YAML 1.1
                        # booleans, numeric-looking text, and a leading dot.
                        "w": {"x": 0, "y": 0, "width": 900, "height": 700,
                              "title": "yes", "command": "off", "cwd": ".config",
                              "xterm_args": ["-fn", "0755"]},
                    },
                },
            },
        }
        text = xtm.dump_yaml(config)
        builtin, pyyaml_ = self._both(text)
        self.assertEqual(builtin[0], "ok",
                         "xtm's own output was refused: %r" % (builtin,))
        self.assertEqual(builtin, pyyaml_)
        self.assertEqual(builtin[1], config)

    def test_example_config_reads_identically(self):
        """The shipped example must mean the same thing on every machine."""
        text = (REPO_ROOT / "config.yaml.example").read_text()
        builtin, pyyaml_ = self._both(text)
        self.assertEqual(builtin[0], "ok", builtin)
        self.assertEqual(builtin, pyyaml_)

    def test_block_sequences_agree_in_both_indent_styles(self):
        """Block lists are the natural YAML spelling and must be readable.

        PyYAML writes items at the key's own indent; people writing a config
        by hand usually indent them one level deeper. Both are valid YAML and
        both appear in real configs, so both must parse.
        """
        styles = {
            "at the key's indent (what PyYAML writes)":
                "profiles:\n  desk:\n    match:\n      hostname:\n"
                "      - laptop\n      - laptop2\n    sessions: {}\n",
            "indented one level deeper (what people write)":
                "profiles:\n  desk:\n    match:\n      hostname:\n"
                "        - laptop\n        - laptop2\n    sessions: {}\n",
        }
        for label, text in styles.items():
            builtin, pyyaml_ = self._both(text)
            self.assertEqual(builtin, pyyaml_, "disagreement, %s" % (label,))
            self.assertEqual(
                builtin[1]["profiles"]["desk"]["match"]["hostname"],
                ["laptop", "laptop2"], label)

    def test_empty_blocks_agree(self):
        """A bare "sessions:" means an empty mapping under either backend.

        PyYAML reads it as None and the built-in parser as {}; xtm folds the
        two together, because a profile with an empty session block must not
        load on one machine and fail validation on another.
        """
        text = "profiles:\n  desk:\n    stack:\n    sessions:\n"
        builtin, pyyaml_ = self._both(text)
        self.assertEqual(builtin[0], "ok")
        self.assertEqual(pyyaml_[0], "ok")
        for backend, (_status, data) in (("built-in", builtin), ("PyYAML", pyyaml_)):
            xtm.validate_profile(data, "desk")
            self.assertEqual(data["profiles"]["desk"]["sessions"], {}, backend)
            self.assertEqual(data["profiles"]["desk"]["stack"],
                             dict(xtm.DEFAULT_STACK), backend)

    def test_a_config_written_with_pyyaml_loads_without_it(self):
        """The scenario the whole fallback parser exists to serve.

        A machine with PyYAML saves a profile carrying xterm_args. PyYAML
        writes the list as a block sequence. That file then has to load on a
        machine where PyYAML is not installed -- otherwise the config is not
        portable, which defeats the point of shipping a parser at all.
        """
        config = {"profiles": {"desk": {
            "stack": dict(xtm.DEFAULT_STACK),
            "sessions": {"w": {"x": 0, "y": 0, "width": 900, "height": 700,
                               "xterm_args": ["-fn", "fixed", "-fg", "white"]}},
        }}}
        xtm.HAVE_PYYAML = True
        written = xtm.dump_yaml(config)
        self.assertIn("- -fn", written,
                      "expected PyYAML's block-sequence output; the test no "
                      "longer reproduces the situation it is guarding")
        status, data = self._read(written, False)   # no PyYAML on this machine
        self.assertEqual(status, "ok",
                         "a PyYAML-written config was unreadable without "
                         "PyYAML: %r" % (data,))
        self.assertEqual(data, config)


class TestYamlBackendFuzz(unittest.TestCase):
    """Randomised search for values the two backends read differently.

    The hand-written corpus above encodes the divergences somebody thought
    to look for. This looks for the ones nobody did: it generates tokens
    from the alphabet where YAML's type resolution actually lives -- digits,
    signs, dots, colons, underscores, exponent and base letters -- and
    requires the two backends to agree on every one.

    It earns its place. Run against the first version of the ambiguity
    guard it found four separate holes that the curated corpus missed:
    a leading '+' on an octal or sexagesimal literal, non-ASCII digits,
    underscores in a leading-dot float, and a fractional sexagesimal.

    Seeds are fixed so a failure is reproducible; the failure message names
    the token, so a new hole is a one-line addition to the corpus above.
    """

    SEEDS = (7, 99, 20260819)
    # Three groups, and the reason for each is worth stating, because the
    # alphabet is what decides which divergences this test can even see.
    #
    #   0-9 + - . _ : e E x X b B o O a A f F      the numeric spellings the
    #       original ambiguity guard was written for
    #   & * ! % @ ` | > < = , ~                    YAML's structural
    #       indicators. Leaving these out is exactly why a second class of
    #       divergence -- anchors, aliases, tags, merge keys, block scalars,
    #       reserved tokens -- survived this test for a whole release: no
    #       generated token could express one
    #   [ ] { } " ' and a tab                      collection and quoting
    #       characters, which found the divergences pointing the OTHER way,
    #       where the built-in parser refused input PyYAML reads happily
    # The backslash is here because a double-quoted scalar is the one
    # quoted form YAML processes escapes in, so it is the one place a
    # quote does not mean the backends agree.
    ALPHABET = "0123456789+-._:eExXbBoOaAfF/ &*!%@`|><=,~[]{}\"'\t\\"

    # Every token is rendered into each of these, because a token's
    # meaning depends on where it sits: `"v}w"` is ordinary text as a
    # bare value and structure inside a flow collection, which is how a
    # regression that only affected flow mappings survived a fuzzer that
    # only tested bare values.
    POSITIONS = {
        "value": "profiles:\n  a: %s\n",
        "flow mapping value": "profiles:\n  a: {k: %s}\n",
        "flow sequence item": "profiles:\n  a: [%s]\n",
        "key": "profiles:\n  %s: {}\n",
        "flow mapping key": "profiles:\n  a: {%s: 1}\n",
    }
    TOKENS_PER_LENGTH = 400
    MAX_LENGTH = 9

    def setUp(self):
        if not xtm.HAVE_PYYAML:
            self.skipTest("PyYAML is not installed, so there is nothing to "
                          "compare the built-in parser against")
        self._saved = xtm.HAVE_PYYAML
        self.addCleanup(setattr, xtm, "HAVE_PYYAML", self._saved)

    @staticmethod
    def _as_names(value):
        """The same structure with every mapping key as its text.

        YAML types a key the way it types a value, so PyYAML hands back
        the integer 2 for `2:` where the built-in parser hands back "2".
        xtm looks every name up as text and `_normalize_config_keys`
        already forces profile and session names back to str after
        loading, so agreement "up to the text of a key" is the property
        that actually has to hold. Comparing raw would report a
        difference the tool cannot observe.
        """
        if isinstance(value, dict):
            return dict((str(k), TestYamlBackendFuzz._as_names(v))
                        for k, v in value.items())
        if isinstance(value, list):
            return [TestYamlBackendFuzz._as_names(v) for v in value]
        return value

    def _reading(self, text, use_pyyaml):
        """How one backend reads `text`, with every refusal reading alike.

        Which backend produced which error message does not matter; that
        both refused does, because refusing together is agreement.
        """
        xtm.HAVE_PYYAML = use_pyyaml
        try:
            return ("ok", self._as_names(xtm.load_yaml(text)))
        except xtm.XtmError:
            return ("refused", None)

    def test_no_token_is_read_differently_by_the_two_backends(self):
        import random
        for seed in self.SEEDS:
            rng = random.Random(seed)
            tokens = set()
            for length in range(1, self.MAX_LENGTH):
                for _ in range(self.TOKENS_PER_LENGTH):
                    tokens.add("".join(rng.choice(self.ALPHABET)
                                       for _ in range(length)))
            for token in tokens:
                token = token.strip()
                # A '#' would start a comment and a newline would end the
                # line; neither is a scalar any more.
                if not token or "#" in token or "\n" in token:
                    continue
                for where, template in self.POSITIONS.items():
                    # A token carrying a top-level comma stops being one
                    # value inside a flow mapping and becomes further
                    # ENTRIES, so what is under test is no longer the
                    # token in value position. Key-position agreement is
                    # its own question and is not what this test asks.
                    if (where == "flow mapping value"
                            and len(xtm._split_top_level(token)) > 1):
                        continue
                    text = template % (token,)
                    builtin = self._reading(text, False)
                    pyyaml_ = self._reading(text, True)
                    self.assertEqual(
                        builtin, pyyaml_,
                        "seed %d: %r in %s position is read differently "
                        "depending on whether PyYAML is installed -- built-in "
                        "gave %r, PyYAML gave %r"
                        % (seed, token, where, builtin, pyyaml_))
