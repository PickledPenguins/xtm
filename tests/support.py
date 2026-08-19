"""Shared fixtures for the xtm test suite.

Everything here exists so a test can say "these sessions are running, these
windows are on screen at these coordinates" and then assert on what xtm did
about it -- with no X server, window manager or terminal involved.

  * FAKE_TOOL_SOURCE is one Python program standing in for every external
    tool xtm calls. build_fake_tools() writes the wrapper scripts that
    dispatch to it by name. State lives in $XTM_FAKE_DIR as pipe-delimited
    text files, so a scenario can be set up or inspected with read_text().

  * IntegrationBase runs xtm as a real subprocess against a temporary config
    directory and a PATH holding ONLY those fakes -- never the real system
    PATH, so results cannot depend on what happens to be installed on the
    machine running the suite. See its setUp for why that matters.

  * PathIsolationMixin and RunPatchMixin are the in-process equivalents:
    they point xtm's module-level paths at a scratch directory, or replace
    xtm.run() with a scripted responder.

Window placement is verified against a genuine pty pair: the fake tmux
reports the pty's slave device as the session's client tty, xtm writes its
escape sequences there exactly as it would to a real xterm, and the test
reads the raw bytes back off the master side.

Run the suite with `make test`, or:

    python3 -m unittest discover -t . -s tests
"""

import json
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

import xtm  # noqa: E402  (the path must be set up first)

XTM_SCRIPT = REPO_ROOT / "xtm.py"


# --------------------------------------------------------------------------
# Fake external tools
# --------------------------------------------------------------------------
#
# One Python program stands in for every external tool xtm calls; the
# wrapper scripts placed on PATH dispatch to it by name. State lives in
# $XTM_FAKE_DIR as two pipe-delimited text files, so a test can set up a
# scenario or inspect the result with nothing more than read_text().
#
#   sessions.txt : name|attached|tty|pid
#   windows.txt  : id|title|instance|x|y|width|height
#   launched.log : one JSON argv list per xterm invocation
#   focus.log    : one window id per focus request

FAKE_TOOL_SOURCE = r'''
import json
import os
import re
import sys
import time

STATE = os.environ["XTM_FAKE_DIR"]


def _path(name):
    return os.path.join(STATE, name)


def read_rows(name):
    try:
        with open(_path(name)) as f:
            return [line.rstrip("\n").split("|") for line in f if line.strip()]
    except IOError:
        return []


def write_rows(name, rows):
    with open(_path(name), "w") as f:
        for row in rows:
            f.write("|".join(str(c) for c in row) + "\n")


def append_line(name, text):
    with open(_path(name), "a") as f:
        f.write(text + "\n")


def fail(code=1):
    sys.exit(code)


def tool_tmux(argv):
    if not argv:
        fail()
    verb = argv[0]
    rows = read_rows("sessions.txt")
    if verb == "list-sessions":
        if not rows:
            sys.stderr.write("no server running\n")
            fail()
        for name, attached, _tty, _pid in rows:
            print("%s\t%s" % (name, attached))
        return
    if verb == "list-clients":
        target = argv[argv.index("-t") + 1] if "-t" in argv else None
        fmt = argv[argv.index("-F") + 1] if "-F" in argv else ""
        for name, attached, tty, pid in rows:
            if name != target or attached == "0":
                continue
            if "client_pid" in fmt:
                print("%s\t%s" % (tty, pid))
            else:
                print(tty)
        return
    if verb == "kill-session":
        target = argv[argv.index("-t") + 1] if "-t" in argv else None
        remaining = [r for r in rows if r[0] != target]
        if len(remaining) == len(rows):
            sys.stderr.write("session not found\n")
            fail()
        write_rows("sessions.txt", remaining)
        write_rows("windows.txt",
                   [w for w in read_rows("windows.txt")
                    if w[2] != "xtm-" + target])
        return
    if verb == "detach-client":
        # Detaching closes the window (the xterm's tmux command returns)
        # but leaves the session, its windows and its panes running, so
        # the row stays and only its attached flag is cleared.
        target = argv[argv.index("-s") + 1] if "-s" in argv else None
        found = False
        updated = []
        for r in rows:
            if r[0] == target:
                found = True
                updated.append([r[0], "0"] + list(r[2:]))
            else:
                updated.append(r)
        if not found:
            sys.stderr.write("session not found\n")
            fail()
        write_rows("sessions.txt", updated)
        write_rows("windows.txt",
                   [w for w in read_rows("windows.txt")
                    if w[2] != "xtm-" + target])
        return
    if verb == "new-session":
        return
    fail()


def detach_stdio():
    """Release the stdout/stderr pipes inherited from the test harness.

    The fake terminal deliberately outlives the xtm process that spawned
    it, exactly as a real xterm does. But it inherits xtm's pipes, and
    subprocess.run() in the harness waits for those to close, not for xtm
    to exit -- so every test that opens a window would block for the fake
    terminal's whole lifetime. Redirecting to /dev/null closes the last
    reference to the pipe and lets the harness return as soon as xtm is
    actually done.
    """
    devnull = os.open(os.devnull, os.O_WRONLY)
    os.dup2(devnull, 1)
    os.dup2(devnull, 2)
    os.close(devnull)


def tool_xterm(argv):
    append_line("launched.log", json.dumps(argv))
    title = argv[argv.index("-T") + 1] if "-T" in argv else ""
    instance = argv[argv.index("-name") + 1] if "-name" in argv else ""
    session = None
    if "-s" in argv:
        session = argv[argv.index("-s") + 1]
    if os.environ.get("XTM_FAKE_XTERM_FAIL"):
        sys.exit(3)
    if os.environ.get("XTM_FAKE_XTERM_NO_ATTACH"):
        # Stand in for a terminal that starts but never gets as far as
        # attaching, so the caller's wait loop can be interrupted.
        detach_stdio()
        time.sleep(float(os.environ.get("XTM_FAKE_XTERM_LIFETIME", "8")))
        return
    tty = os.environ.get("XTM_FAKE_TTY", "/dev/null")
    rows = read_rows("sessions.txt")
    if not any(r[0] == session for r in rows):
        rows.append([session, "1", tty, str(os.getpid())])
    else:
        rows = [[r[0], "1", tty, str(os.getpid())] if r[0] == session else r
                for r in rows]
    write_rows("sessions.txt", rows)
    geom = os.environ.get("XTM_FAKE_NEW_GEOM", "0,0,100,100").split(",")
    windows = read_rows("windows.txt")
    win_id = "0x%x" % (0x100 + len(windows),)
    windows.append([win_id, title, instance] + geom)
    write_rows("windows.txt", windows)
    # Stand in for a long-running terminal so that the caller's
    # wait-for-attach poll sees a live process, not an instant exit.
    detach_stdio()
    time.sleep(float(os.environ.get("XTM_FAKE_XTERM_LIFETIME", "8")))


def windows():
    return read_rows("windows.txt")


def tool_xdotool(argv):
    if not argv:
        fail()
    if argv[0] == "search":
        flag, pattern = argv[1], argv[2]
        field = 2 if flag == "--classname" else 1
        found = [w[0] for w in windows() if re.search(pattern, w[field])]
        if not found:
            fail()
        for win_id in found:
            print(win_id)
        return
    if argv[0] == "getwindowname":
        for w in windows():
            if w[0] == argv[1]:
                print(w[1])
                return
        fail()
    if argv[0] == "getwindowgeometry":
        win_id = argv[-1]
        for w in windows():
            if w[0] == win_id:
                print("WINDOW=%s" % win_id)
                print("X=%s" % w[3])
                print("Y=%s" % w[4])
                print("WIDTH=%s" % w[5])
                print("HEIGHT=%s" % w[6])
                return
        fail()
    if argv[0] == "windowactivate":
        append_line("focus.log", argv[1])
        return
    fail()


def tool_wmctrl(argv):
    if "-lx" in argv:
        for w in windows():
            print("%s  0 %s.XTerm  host %s" % (w[0], w[2], w[1]))
        return
    if "-lG" in argv:
        for w in windows():
            print("%s  0 %s %s %s %s host %s"
                  % (w[0], w[3], w[4], w[5], w[6], w[1]))
        return
    if "-a" in argv:
        append_line("focus.log", argv[-1])
        return
    fail()


def tool_xwininfo(argv):
    if "-root" in argv and "-tree" in argv:
        print('xwininfo: Window id: 0x1 (the root window) (has no name)')
        print("")
        print("  Root window id: 0x1 (the root window) (has no name)")
        for w in windows():
            print('     %s "%s": ("%s" "XTerm")  %sx%s+%s+%s  +%s+%s'
                  % (w[0], w[1], w[2], w[5], w[6], w[3], w[4], w[3], w[4]))
        return
    if "-id" in argv:
        win_id = argv[argv.index("-id") + 1]
        for w in windows():
            if w[0] == win_id:
                print("xwininfo: Window id: %s \"%s\"" % (w[0], w[1]))
                print("  Absolute upper-left X:  %s" % w[3])
                print("  Absolute upper-left Y:  %s" % w[4])
                print("  Width: %s" % w[5])
                print("  Height: %s" % w[6])
                return
        fail()
    fail()


def tool_xprop(argv):
    win_id = argv[argv.index("-id") + 1] if "-id" in argv else None
    prop = argv[-1]
    if prop == "WM_CLASS":
        for w in windows():
            if w[0] == win_id:
                print('WM_CLASS(STRING) = "%s", "XTerm"' % w[2])
                return
        fail()
    if prop == "_NET_FRAME_EXTENTS":
        extents = os.environ.get("XTM_FAKE_FRAME_EXTENTS")
        if not extents:
            sys.stderr.write("no such property\n")
            fail()
        print("_NET_FRAME_EXTENTS(CARDINAL) = %s" % extents)
        return
    fail()


TOOLS = {
    "tmux": tool_tmux,
    "xterm": tool_xterm,
    "xdotool": tool_xdotool,
    "wmctrl": tool_wmctrl,
    "xwininfo": tool_xwininfo,
    "xprop": tool_xprop,
}

if __name__ == "__main__":
    name = os.path.basename(sys.argv[1])
    # Every invocation is logged so tests can assert on how many times a
    # tool was called, not only on the end result.
    append_line("calls.log", json.dumps([name] + sys.argv[2:]))
    TOOLS[name](sys.argv[2:])
'''


def build_fake_tools(bin_dir, only=None):
    """Create wrapper scripts for the fake tools in `bin_dir`.

    `only` limits which tools exist, which is how the tests exercise the
    "no window-query tool installed" and "tmux is missing" paths.
    """
    bin_dir = Path(bin_dir)
    bin_dir.mkdir(parents=True, exist_ok=True)
    impl = bin_dir / "_fake_tools.py"
    impl.write_text(FAKE_TOOL_SOURCE)
    for name in (only if only is not None else sorted(
            ["tmux", "xterm", "xdotool", "wmctrl", "xwininfo", "xprop"])):
        script = bin_dir / name
        script.write_text("#!/bin/sh\nexec %s %s \"$0\" \"$@\"\n"
                          % (sys.executable, impl))
        script.chmod(0o755)
    return bin_dir


def socket_hostname():
    """This machine's short hostname, sanitised the way xtm sanitises it."""
    return xtm.short_hostname()


class PathIsolationMixin(unittest.TestCase):
    """Point xtm's config and state at a scratch directory for one test."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="xtm-test-"))
        self.addCleanup(shutil.rmtree, str(self.tmp), True)
        self._saved = (xtm.CONFIG_DIR, xtm.CONFIG_FILE, xtm.STATE_DIR,
                       xtm.STATE_FILE, xtm.DRY_RUN)
        self.addCleanup(self._restore)
        xtm.resolve_paths(str(self.tmp / "config.yaml"), str(self.tmp / "state"))

    def _restore(self):
        (xtm.CONFIG_DIR, xtm.CONFIG_FILE, xtm.STATE_DIR, xtm.STATE_FILE,
         xtm.DRY_RUN) = self._saved


class IntegrationBase(unittest.TestCase):
    """Run xtm as a subprocess against fake external tools."""

    TOOLS = None  # None means "all tools available"

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="xtm-int-"))
        self.addCleanup(shutil.rmtree, str(self.tmp), True)
        self.state = self.tmp / "fake"
        self.state.mkdir()
        self.bin = build_fake_tools(self.tmp / "bin", self.TOOLS)
        self.config_path = self.tmp / "config.yaml"
        self.env = dict(os.environ)
        self.env.update({
            # ONLY the fake tools, never the real PATH. Appending the real
            # PATH would let a genuinely installed tmux/xdotool/wmctrl/
            # xwininfo/xprop answer instead of the fake, so a class that
            # deliberately omits a tool to exercise a degraded path (see
            # TestDegradedEnvironments and TestNoTmux) would silently find
            # the real one. That makes results depend on what happens to be
            # installed on the machine running the suite, which is the one
            # property a test suite must never have.
            #
            # Consequence for helper scripts written by tests: nothing is on
            # PATH, so a /bin/sh helper may use shell builtins (printf, exit)
            # or absolute paths, but never an external command such as cat.
            # See make_editor().
            "PATH": str(self.bin),
            "XTM_FAKE_DIR": str(self.state),
            "XTM_CONFIG_DIR": str(self.tmp / "cfg"),
            "XTM_STATE_DIR": str(self.tmp / "state"),
            "DISPLAY": ":99",
            "XTM_FAKE_XTERM_LIFETIME": "6",
        })
        self.env.pop("XTM_FAKE_FRAME_EXTENTS", None)
        self.addCleanup(self.kill_fake_terminals)

    def kill_fake_terminals(self):
        """Stop any sleeping stand-in terminals left over from a test."""
        for row in self.rows("sessions.txt"):
            try:
                os.kill(int(row[3]), signal.SIGKILL)
            except (ValueError, IndexError, ProcessLookupError, PermissionError):
                pass

    def run_xtm(self, *argv, **kwargs):
        env = dict(self.env)
        env.update(kwargs.pop("env", {}))
        return subprocess.run(
            [sys.executable, str(XTM_SCRIPT)] + list(argv),
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            universal_newlines=True, env=env, timeout=40, **kwargs)

    def fs_snapshot(self, *roots):
        """Map every file under `roots` to its contents.

        Used to assert what a command did to the filesystem as a whole
        rather than checking one expected effect at a time. Directories are
        recorded too, so a command that creates an empty directory is caught
        as readily as one that writes a file.
        """
        snapshot = {}
        for root in roots:
            root = Path(root)
            if not root.exists():
                continue
            for path in sorted(root.rglob("*")):
                if path.is_dir():
                    snapshot[str(path)] = "<dir>"
                else:
                    try:
                        snapshot[str(path)] = path.read_bytes()
                    except OSError as e:
                        snapshot[str(path)] = "<unreadable: %s>" % (e,)
        return snapshot

    def make_editor(self, name, content):
        """Write a stand-in $EDITOR that replaces its argument with `content`.

        Built entirely from printf, which is a POSIX shell builtin, because
        the integration tests run with a hermetic PATH holding only the fake
        tools (see setUp). An editor that shelled out to cat or sed would
        need a PATH reaching the real system, which is exactly what these
        tests must not have.
        """
        # Order matters: backslashes first so the escapes added afterwards
        # are not themselves doubled, and the newline translation last
        # because it deliberately introduces backslashes printf must see.
        escaped = (content.replace("\\", "\\\\")
                          .replace("%", "%%")
                          .replace("'", "'\\''")
                          .replace("\n", "\\n"))
        path = self.tmp / name
        path.write_text("#!/bin/sh\nprintf '%s' > \"$1\"\n" % (escaped,))
        path.chmod(0o755)
        return path

    def rows(self, name):
        path = self.state / name
        if not path.exists():
            return []
        return [line.split("|") for line in path.read_text().splitlines()
                if line.strip()]

    def write_rows(self, name, rows):
        (self.state / name).write_text(
            "".join("|".join(str(c) for c in row) + "\n" for row in rows))

    def config_text(self):
        return (self.tmp / "cfg" / "config.yaml").read_text()

    def write_config(self, text):
        (self.tmp / "cfg").mkdir(parents=True, exist_ok=True)
        (self.tmp / "cfg" / "config.yaml").write_text(textwrap.dedent(text))

    def add_session(self, name, tty="/dev/null", attached="1", pid=1):
        rows = self.rows("sessions.txt")
        rows.append([name, attached, tty, pid])
        self.write_rows("sessions.txt", rows)

    def calls(self, tool=None):
        """Invocations of the fake tools, optionally filtered to one tool."""
        path = self.state / "calls.log"
        if not path.exists():
            return []
        entries = [json.loads(line) for line in path.read_text().splitlines()
                   if line.strip()]
        if tool is None:
            return entries
        return [e for e in entries if e[0] == tool]

    def clear_calls(self):
        """Discard the invocation log, so a later assertion counts only the
        calls made after this point."""
        path = self.state / "calls.log"
        if path.exists():
            path.unlink()

    def add_window(self, session, x, y, width, height, win_id=None):
        rows = self.rows("windows.txt")
        win_id = win_id or ("0x%x" % (0x200 + len(rows)))
        rows.append([win_id, "xtm:" + session, "xtm-" + session,
                     x, y, width, height])
        self.write_rows("windows.txt", rows)
        return win_id


class RunPatchMixin(unittest.TestCase):
    """Replace xtm.run with a scripted responder keyed on the program name."""

    def patch_run(self, responder):
        original = xtm.run
        xtm.run = lambda cmd, timeout=None, required=True: responder(list(cmd))
        self.addCleanup(lambda: setattr(xtm, "run", original))


class FakeResult(object):
    """Stand-in for subprocess.CompletedProcess in monkeypatched tests."""

    def __init__(self, returncode=0, stdout="", stderr=""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr
