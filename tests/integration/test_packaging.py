"""The installer and the completion script are shipped, so they are tested.

Both are user-facing and neither was covered before. install.sh is the
primary way xtm gets onto a machine -- it is what the README documents
first, because pip is not an option on the hosts xtm targets -- and the
completion script is the kind of file that silently drifts out of sync with
the parser one release at a time.
"""

import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from tests.support import (
    REPO_ROOT,
    xtm,
)

INSTALL_SH = REPO_ROOT / "install.sh"
COMPLETION = REPO_ROOT / "completions" / "xtm.bash"


class InstallerBase(unittest.TestCase):
    """Run install.sh against a throwaway prefix."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="xtm-install-"))
        self.addCleanup(shutil.rmtree, str(self.tmp), True)
        self.prefix = self.tmp / "bin"
        self.completion_dir = self.tmp / "completions"

    def install(self, *args, **kwargs):
        argv = ["sh", str(INSTALL_SH), "--prefix", str(self.prefix)] + list(args)
        env = dict(os.environ)
        env.update(kwargs.pop("env", {}))
        return subprocess.run(argv, stdout=subprocess.PIPE,
                              stderr=subprocess.PIPE, universal_newlines=True,
                              env=env, timeout=120, **kwargs)


class TestInstaller(InstallerBase):

    def test_installs_a_working_executable(self):
        result = self.install()
        self.assertEqual(result.returncode, 0, result.stderr)
        target = self.prefix / "xtm"
        self.assertTrue(target.exists(), "install.sh did not create %s" % (target,))
        self.assertTrue(os.access(str(target), os.X_OK),
                        "the installed file is not executable")
        # The installer runs `xtm --version` itself as a final check, so a
        # broken install fails loudly rather than being discovered later.
        self.assertIn(xtm.__version__, result.stdout)

    def test_the_installed_copy_is_byte_identical(self):
        """A copy, not a rewrite: no build step means nothing can mangle it."""
        self.install()
        self.assertEqual((self.prefix / "xtm").read_bytes(),
                         (REPO_ROOT / "src" / "xtm.py").read_bytes())

    def test_the_installed_copy_actually_runs(self):
        self.install()
        result = subprocess.run([str(self.prefix / "xtm"), "--version"],
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                universal_newlines=True, timeout=60)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(xtm.__version__, result.stdout + result.stderr)

    def test_installing_twice_is_harmless(self):
        self.assertEqual(self.install().returncode, 0)
        second = self.install()
        self.assertEqual(second.returncode, 0, second.stderr)

    def test_completion_is_installed_on_request(self):
        result = self.install("--completion-dir", str(self.completion_dir))
        self.assertEqual(result.returncode, 0, result.stderr)
        installed = self.completion_dir / "xtm"
        self.assertTrue(installed.exists(),
                        "install.sh did not install the completion file")
        self.assertEqual(installed.read_bytes(), COMPLETION.read_bytes())

    def test_completion_is_not_installed_by_default(self):
        self.install()
        self.assertFalse((self.completion_dir / "xtm").exists())

    def test_uninstall_removes_what_was_installed(self):
        self.install("--completion-dir", str(self.completion_dir))
        result = self.install("--completion-dir", str(self.completion_dir),
                              "--uninstall")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse((self.prefix / "xtm").exists())
        self.assertFalse((self.completion_dir / "xtm").exists())

    def test_uninstall_with_nothing_installed_says_so(self):
        result = self.install("--uninstall")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Nothing to remove", result.stdout)

    def test_uninstall_leaves_configuration_alone(self):
        """Removing the program must not remove the user's layouts."""
        result = self.install("--uninstall")
        self.assertIn("left in place", result.stdout)

    def test_unknown_option_is_a_usage_error(self):
        result = self.install("--no-such-option")
        self.assertEqual(result.returncode, 2)
        self.assertIn("unknown option", result.stderr)

    def test_prefix_requires_an_argument(self):
        result = subprocess.run(["sh", str(INSTALL_SH), "--prefix"],
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                universal_newlines=True, timeout=60)
        self.assertEqual(result.returncode, 2)
        self.assertIn("needs a directory", result.stderr)

    def test_help_exits_zero(self):
        result = self.install("--help")
        self.assertEqual(result.returncode, 0)
        self.assertIn("Usage: install.sh", result.stdout)

    def test_a_prefix_not_on_path_warns_without_failing(self):
        """Installing somewhere deliberate should not be treated as an error."""
        result = self.install(env={"PATH": "/usr/bin:/bin"})
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("not on PATH", result.stdout)


class TestCompletionScript(unittest.TestCase):
    """The completion script must keep up with the parser."""

    def parser_long_options(self):
        """Every long option xtm accepts."""
        options = set()
        for action in xtm.build_parser()._actions:
            for spelling in action.option_strings:
                if spelling.startswith("--"):
                    options.add(spelling)
        return options

    def test_every_parser_option_is_offered_by_completion(self):
        text = COMPLETION.read_text()
        missing = sorted(opt for opt in self.parser_long_options()
                         if opt not in text)
        self.assertEqual(
            missing, [],
            "these options exist in xtm but are not offered by "
            "xtm-completion.bash: %s" % (", ".join(missing),))

    def test_completion_offers_no_option_xtm_does_not_have(self):
        """The other direction: a removed option must not linger in completion."""
        known = self.parser_long_options()
        # Words in the actions/options word lists, which are what completion
        # actually suggests. Other long words in the file are shell code.
        listed = set()
        for block in re.findall(r'local (?:actions|options)="([^"]*)"',
                                COMPLETION.read_text(), re.S):
            listed.update(w for w in block.split() if w.startswith("--"))
        self.assertTrue(listed, "could not find the completion word lists")
        stale = sorted(listed - known)
        self.assertEqual(
            stale, [],
            "xtm-completion.bash offers options xtm does not accept: %s"
            % (", ".join(stale),))

    def test_choice_options_list_their_real_choices(self):
        """--on-switch and --log-level suggest fixed sets; keep them true."""
        text = COMPLETION.read_text()
        for action in xtm.build_parser()._actions:
            if not action.choices or not action.option_strings:
                continue
            long_names = [s for s in action.option_strings if s.startswith("--")]
            if not long_names:
                continue
            for choice in action.choices:
                self.assertIn(
                    str(choice), text,
                    "%s accepts %r but completion never offers it"
                    % (long_names[0], choice))

    def test_completion_is_valid_shell(self):
        shell = shutil.which("bash") or shutil.which("sh")
        if not shell:
            self.skipTest("no shell available to syntax-check with")
        result = subprocess.run([shell, "-n", str(COMPLETION)],
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                universal_newlines=True, timeout=60)
        self.assertEqual(result.returncode, 0,
                         "xtm-completion.bash is not valid shell:\n%s"
                         % (result.stderr,))

    def test_installer_is_valid_shell(self):
        result = subprocess.run(["sh", "-n", str(INSTALL_SH)],
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                universal_newlines=True, timeout=60)
        self.assertEqual(result.returncode, 0,
                         "install.sh is not valid shell:\n%s" % (result.stderr,))


if __name__ == "__main__":
    unittest.main()


class TestConsoleEntryPoint(unittest.TestCase):
    """`pip install xtm` must behave like the copied script.

    install.sh copies xtm.py and the shebang runs the `if __name__ ==
    "__main__"` block; pyproject.toml's console script imports the module
    and calls one function instead. Anything guarded only by that block
    therefore does not run for a pip-installed xtm, which is how an
    ordinary bad config came to print a Python traceback there while
    printing one line here.
    """

    def entry_point_name(self):
        """The function pyproject.toml actually points the console script at."""
        text = (REPO_ROOT / "pyproject.toml").read_text()
        match = re.search(r'^xtm\s*=\s*"xtm:(\w+)"', text, re.MULTILINE)
        self.assertIsNotNone(match, "pyproject.toml declares no xtm console script")
        return match.group(1)

    def run_entry_point(self, *argv):
        """Call the console script's function the way a wheel's wrapper does."""
        program = (
            "import sys; sys.path.insert(0, %r); import xtm; "
            "sys.exit(xtm.%s(%r))" % (str(REPO_ROOT / "src"), self.entry_point_name(),
                                      list(argv))
        )
        return subprocess.run([sys.executable, "-c", program],
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                              universal_newlines=True, timeout=40)

    def test_the_entry_point_exists(self):
        self.assertTrue(hasattr(xtm, self.entry_point_name()),
                        "pyproject.toml names a function xtm.py does not define")

    def test_a_config_error_is_one_line_and_not_a_traceback(self):
        with tempfile.TemporaryDirectory(prefix="xtm-entry-") as tmp:
            bad = Path(tmp) / "bad.yaml"
            bad.write_text("profiles: [1, 2]\n")
            result = self.run_entry_point("--config", str(bad), "--state-dir",
                                          tmp, "--list")
        self.assertEqual(result.returncode, 1)
        self.assertNotIn("Traceback", result.stderr)
        self.assertNotIn("XtmError", result.stderr)
        self.assertIn("must be a mapping", result.stderr)

    def test_the_entry_point_returns_the_documented_exit_codes(self):
        with tempfile.TemporaryDirectory(prefix="xtm-entry-") as tmp:
            ok = self.run_entry_point("--config", str(Path(tmp) / "c.yaml"),
                                      "--state-dir", tmp, "--validate")
            self.assertEqual(ok.returncode, xtm.EXIT_OK, ok.stderr)

    def test_an_interrupt_becomes_exit_130(self):
        """KeyboardInterrupt is caught by the entry point, not by main()."""
        program = (
            "import sys; sys.path.insert(0, %r); import xtm; "
            "xtm.main = lambda argv=None: (_ for _ in ()).throw(KeyboardInterrupt()); "
            "sys.exit(xtm.%s([]))" % (str(REPO_ROOT / "src"), self.entry_point_name())
        )
        result = subprocess.run([sys.executable, "-c", program],
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                universal_newlines=True, timeout=40)
        self.assertEqual(result.returncode, xtm.EXIT_INTERRUPTED)
        self.assertNotIn("Traceback", result.stderr)
