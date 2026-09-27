"""The documentation must describe the tool that ships with it.

Every surface here is enumerated from the CODE at run time -- the argument
parser, the exit codes, the environment reads, the keys the validators
accept, the keys the JSON views actually emit -- and then looked for in the
document that owns it. Nothing is compared against a checked-in list, because
a list is one more thing to forget.

This is the test the 2026-09-01 review argued for. Six of its thirteen
findings were documentation drift: a testing section naming a file that no
longer existed, `$DISPLAY` described as an auto-selection input two versions
after it stopped being one, and six `--json` keys the tool emits that nothing
documented. Each of those becomes impossible to reintroduce here rather than
being found by the next reader.

Where a surface genuinely has no place in the documentation, it is listed in
an explicit exception below with the reason. There is no blanket ignore.
"""

import ast
import json
import re
import unittest

from tests.support import (
    REPO_ROOT,
    IntegrationBase,
    xtm,
)

DOCS = REPO_ROOT / "docs"
README = REPO_ROOT / "README.md"

CLI_DOC = DOCS / "cli.md"
CONFIG_DOC = DOCS / "configuration.md"

# The README is an index, not a manual. The cap is what keeps material moving
# into the document that owns the subject instead of back into the front page.
README_MAX_LINES = 250

# Parser options with nothing to say to a user beyond what --help prints.
UNDOCUMENTED_OPTIONS = {
    "-h", "--help",        # argparse's own, described in cli.md as a row already
}

# Environment variables read for reasons that are not xtm's interface.
UNDOCUMENTED_ENVIRONMENT = {
    "PATH",                # consulted to find external tools
    "HOME",               # path resolution
    "XDG_CONFIG_HOME",    # standard base directory, documented as the default path
    "XDG_STATE_HOME",     # likewise
    "LC_ALL", "LANG",     # forced to C for subprocesses
    "TMPDIR",             # stdlib temp-file location
    "COLUMNS",            # terminal width for help output
    "XTM_TEST_HOSTNAME",  # test seam, deliberately not a user interface
}


def read(path):
    return path.read_text()


def documented_in(text, needle):
    """Whether `needle` appears in `text` as a whole word or code span."""
    return re.search(r'(?<![A-Za-z0-9_-])%s(?![A-Za-z0-9_-])' % re.escape(needle),
                     text) is not None


class TestOptionsAreDocumented(unittest.TestCase):
    """Every option the parser accepts appears in the CLI reference."""

    def setUp(self):
        self.doc = read(CLI_DOC)
        self.parser = xtm.build_parser()

    def option_strings(self):
        for action in self.parser._actions:
            for option in action.option_strings:
                if option not in UNDOCUMENTED_OPTIONS:
                    yield option

    def test_there_are_options_to_check(self):
        # Guards the rest: if introspection broke, every other test in this
        # class would pass over an empty list.
        self.assertGreater(len(list(self.option_strings())), 30)

    def test_every_option_is_documented(self):
        missing = [o for o in self.option_strings()
                   if not documented_in(self.doc, o)]
        self.assertEqual([], missing,
                         "options the parser accepts and cli.md does not "
                         "mention: %s" % (missing,))

    def test_no_documented_option_is_absent_from_the_parser(self):
        known = set(self.option_strings()) | UNDOCUMENTED_OPTIONS
        # Only the option tables are scanned: prose elsewhere may name an
        # option in passing, and a row is what claims it exists.
        rows = [line for line in self.doc.split("\n")
                if line.startswith("| `-") or line.startswith("| — | `-")]
        claimed = set()
        for row in rows:
            claimed.update(re.findall(r'`(--?[A-Za-z][A-Za-z0-9-]*)`', row))
        unknown = sorted(c for c in claimed if c not in known)
        self.assertEqual([], unknown,
                         "cli.md documents options the parser does not "
                         "accept: %s" % (unknown,))

    def test_every_exit_code_is_documented(self):
        codes = [getattr(xtm, name) for name in dir(xtm)
                 if name.startswith("EXIT_")]
        self.assertGreaterEqual(len(codes), 4)
        table = self.doc[self.doc.index("## Exit status"):]
        for code in codes:
            self.assertIn("`%d`" % (code,), table,
                          "exit code %d is not in cli.md's exit status table"
                          % (code,))


class TestEnvironmentIsDocumented(unittest.TestCase):
    """Every environment variable xtm reads is documented, or excepted."""

    def test_every_variable_read_is_documented(self):
        source = read(REPO_ROOT / "src" / "xtm.py")
        names = set(re.findall(r'environ(?:\.get)?\(\s*["\']([A-Z_][A-Z0-9_]*)["\']',
                               source))
        names |= set(re.findall(r'getenv\(\s*["\']([A-Z_][A-Z0-9_]*)["\']', source))
        self.assertTrue(names, "no environment reads found; the pattern broke")
        doc = read(CLI_DOC)
        missing = sorted(n for n in names
                         if n not in UNDOCUMENTED_ENVIRONMENT
                         and not documented_in(doc, n))
        self.assertEqual([], missing,
                         "environment variables xtm reads that cli.md does "
                         "not document: %s" % (missing,))


class TestConfigKeysAreDocumented(unittest.TestCase):
    """Every key the validators accept appears in the configuration doc."""

    VALIDATORS = ("validate_session_entry", "validate_profile",
                  "validate_geometry", "validate_stack")

    def config_keys(self):
        """Keys the validator functions read, taken from the source tree."""
        tree = ast.parse(read(REPO_ROOT / "src" / "xtm.py"))
        keys = set()
        for node in ast.walk(tree):
            if not isinstance(node, ast.FunctionDef):
                continue
            if node.name not in self.VALIDATORS:
                continue
            for inner in ast.walk(node):
                if (isinstance(inner, ast.Call)
                        and isinstance(inner.func, ast.Attribute)
                        and inner.func.attr in ("get", "pop")
                        and inner.args
                        and isinstance(inner.args[0], ast.Constant)
                        and isinstance(inner.args[0].value, str)):
                    keys.add(inner.args[0].value)
                if (isinstance(inner, ast.Compare)
                        and isinstance(inner.left, ast.Constant)
                        and isinstance(inner.left.value, str)
                        and any(isinstance(op, (ast.In, ast.NotIn))
                                for op in inner.ops)):
                    keys.add(inner.left.value)
        return keys

    def test_there_are_keys_to_check(self):
        self.assertGreaterEqual(len(self.config_keys()), 8)

    def test_every_config_key_is_documented(self):
        doc = read(CONFIG_DOC)
        missing = sorted(k for k in self.config_keys()
                         if not documented_in(doc, k))
        self.assertEqual([], missing,
                         "config keys the validators accept that "
                         "configuration.md does not document: %s" % (missing,))


class TestJsonKeysAreDocumented(IntegrationBase):
    """Every key the JSON views emit appears in the CLI reference.

    Emitted rather than declared: the six keys the 2026-09-01 review found
    undocumented were all real output that no table mentioned, and only
    running the command finds that class.
    """

    def json_keys(self, payload, prefix=""):
        found = set()
        if isinstance(payload, dict):
            for key, value in payload.items():
                found.add(key)
                found |= self.json_keys(value, prefix)
        elif isinstance(payload, list):
            for item in payload:
                found |= self.json_keys(item, prefix)
        return found

    def test_every_emitted_json_key_is_documented(self):
        self.run_xtm("-s", "work1", "0,0,900,700")
        doc = read(CLI_DOC)
        emitted = set()
        for argv in (("-l", "-j"), ("-P", "-j"), ("-P", "-v", "-j"),
                     ("-C", "-j"), ("-V", "-j")):
            result = self.run_xtm(*argv)
            emitted |= self.json_keys(json.loads(result.stdout))
        self.assertGreaterEqual(len(emitted), 15)
        missing = sorted(k for k in emitted if not documented_in(doc, k))
        self.assertEqual([], missing,
                         "JSON keys the reporting commands emit that cli.md "
                         "does not document: %s" % (missing,))


class TestDocumentSetIsCoherent(unittest.TestCase):
    """The index reaches every document, and every link resolves."""

    def test_readme_stays_an_index(self):
        lines = read(README).count("\n")
        self.assertLessEqual(
            lines, README_MAX_LINES,
            "README.md is %d lines, over the %d-line cap. New material "
            "belongs in the document that owns the subject."
            % (lines, README_MAX_LINES))

    def test_every_document_is_reachable_from_the_readme(self):
        linked = set(re.findall(r'\]\((docs/[a-z-]+\.md)\)', read(README)))
        present = set("docs/" + p.name for p in DOCS.iterdir()
                      if p.suffix == ".md")
        self.assertEqual(
            set(), present - linked,
            "documents that exist but the README index does not link: %s"
            % (sorted(present - linked),))
        self.assertEqual(
            set(), linked - present,
            "documents the README links that do not exist: %s"
            % (sorted(linked - present),))

    def test_no_internal_link_is_dead(self):
        dead = []
        for path in [README] + sorted(DOCS.iterdir()):
            if path.suffix != ".md":
                continue
            for target in re.findall(r'\]\(([^)]+)\)', read(path)):
                if target.startswith(("http://", "https://", "#")):
                    continue
                anchor = ""
                if "#" in target:
                    target, anchor = target.split("#", 1)
                    if not target:
                        continue
                resolved = (path.parent / target).resolve()
                if not resolved.exists():
                    dead.append("%s -> %s" % (path.name, target))
                elif anchor and resolved.suffix == ".md":
                    slugs = set()
                    for heading in re.findall(r'(?m)^#+\s+(.+)$',
                                              resolved.read_text()):
                        slug = re.sub(r'[^a-z0-9 -]', '', heading.lower())
                        slugs.add(slug.replace(" ", "-"))
                    if anchor not in slugs:
                        dead.append("%s -> %s#%s" % (path.name, target, anchor))
        self.assertEqual([], dead, "dead links: %s" % (dead,))


class TestRecordedNumbersMatchTheDocs(unittest.TestCase):
    """The coverage table says what the last real run measured.

    Those numbers are the one part of the documentation whose whole value is
    being current, and they had drifted three times -- silently, because no
    test reads prose. `tests/run-tests.sh --coverage` records what it
    measured; this compares the table against it.

    Skipped when nothing has been measured on this machine yet, because a
    coverage run takes minutes and the fast suite must not.
    """

    METRICS = REPO_ROOT / ".build" / "metrics.json"

    def setUp(self):
        if not self.METRICS.exists():
            self.skipTest("no .build/metrics.json yet; run the coverage step "
                          "(tests/run-tests.sh --coverage) to record one")
        self.measured = json.loads(self.METRICS.read_text())
        self.doc = read(REPO_ROOT / "docs" / "testing.md")

    def documented(self, label):
        # The whole cell, then the number in it: matching digits directly
        # against the table picked up the first digit of the row separator.
        match = re.search(r'\|\s*%s\s*\|\s*\**([\d.]+)\**'
                          % (re.escape(label),), self.doc)
        self.assertIsNotNone(match,
                             "docs/testing.md no longer states %r" % (label,))
        return match.group(1)

    def test_the_statement_count_is_current(self):
        self.assertEqual(str(self.measured["statements"]),
                         self.documented("Statements"),
                         "docs/testing.md quotes a statement count the last "
                         "coverage run does not agree with")

    def test_the_test_count_is_current(self):
        self.assertEqual(str(self.measured["tests"]), self.documented("Tests"),
                         "docs/testing.md quotes a test count the last run "
                         "does not agree with")

    def test_the_combined_figure_is_current(self):
        self.assertIn(self.measured["combined"].rstrip("%"), self.doc,
                      "docs/testing.md does not quote the combined coverage "
                      "figure the last run measured (%s)"
                      % (self.measured["combined"],))

    def test_the_commit_the_numbers_were_measured_at_is_named(self):
        self.assertRegex(
            self.doc, r"Measured at commit `[0-9a-f]{7,}`",
            "the coverage table does not say which commit it describes, "
            "which is what makes a stale number checkable")


class TestNumbersInProseAreCurrent(unittest.TestCase):
    """A count in prose is a claim, and claims are checked.

    Three counts had drifted by the time the 2026-09-01 review found them --
    a test total, a coverage figure and "four tmux commands" beside a list of
    five. Anything asserted here has to be updated with the change that moves
    it, which is the point.
    """

    def test_the_documented_tmux_command_count_matches_the_code(self):
        source = read(REPO_ROOT / "src" / "xtm.py")
        commands = set(re.findall(r'"tmux",\s*"([a-z-]+)"', source))
        self.assertTrue(commands, "no tmux invocations found; the pattern broke")
        doc = read(REPO_ROOT / "docs" / "guide.md")
        claimed = re.search(r'issues (\w+) tmux commands', doc)
        self.assertIsNotNone(claimed, "guide.md no longer states the count")
        words = {"three": 3, "four": 4, "five": 5, "six": 6, "seven": 7}
        self.assertEqual(
            len(commands), words[claimed.group(1)],
            "guide.md says %s tmux commands; the code issues %d: %s"
            % (claimed.group(1), len(commands), sorted(commands)))
        for command in commands:
            self.assertIn("`%s`" % (command,), doc,
                          "guide.md does not name the tmux command %r"
                          % (command,))


if __name__ == "__main__":
    unittest.main()
