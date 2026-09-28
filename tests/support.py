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

# No test may reach the real desktop or the real tmux server. Tests run
# xtm in-process with the real PATH, and a unit test that stubs one tool
# but not the next would otherwise open a real xterm on the developer's
# screen, attached to their own tmux server -- which happened once, when
# placement started asking tmux for its clients through a new function.
# Without DISPLAY no window can open; with TMUX unset and TMUX_TMPDIR
# pointing at an empty directory, a real tmux finds no server at all.
# Integration tests set their own DISPLAY and PATH for the fakes.
os.environ.pop("DISPLAY", None)
os.environ.pop("TMUX", None)
os.environ["TMUX_TMPDIR"] = tempfile.mkdtemp(prefix="xtm-test-tmux-")

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "src"))
sys.path.insert(0, str(REPO_ROOT))

import xtm  # noqa: E402  (the path must be set up first)

XTM_SCRIPT = REPO_ROOT / "src" / "xtm.py"


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


def render_format(argv, values):
    """Expand a tmux -F format the way tmux does, including its damage.

    The fake used to print its own tab-separated line whatever format xtm
    asked for, so xtm's parsing was only ever tested against the fake's
    output -- and real tmux 3.6, run in the C locale xtm forces, rewrites
    every control character in format output to "_". A tab separator
    therefore passed every test and failed on every real machine. Rendering
    the format xtm actually sent, and sanitizing it under LC_ALL=C exactly
    as tmux does, keeps the fake honest.
    """
    fmt = argv[argv.index("-F") + 1] if "-F" in argv else ""
    line = re.sub(r"#\{(\w+)\}", lambda m: str(values.get(m.group(1), "")), fmt)
    if os.environ.get("LC_ALL") == "C":
        line = "".join("_" if ch < " " else ch for ch in line)
    return line


def tool_tmux(argv):
    if not argv:
        fail()
    verb = argv[0]
    # A tmux that works except for one verb, so a test can exercise the
    # "tmux said no" branch of a command without breaking everything else
    # the command needs tmux for.
    if verb in os.environ.get("XTM_FAKE_TMUX_REFUSE", "").split(","):
        sys.stderr.write("tmux: refusing %s (XTM_FAKE_TMUX_REFUSE)\n" % (verb,))
        fail()
    rows = read_rows("sessions.txt")
    # Clients beyond each session's own row: another terminal, an SSH login.
    # session|tty|pid, one per line.
    others = read_rows("clients.txt")

    def clients():
        """(session, tty, pid) for every attached client, xtm's first."""
        found = [(r[0], r[2], r[3]) for r in rows if r[1] != "0"]
        return found + [(c[0], c[1], c[2]) for c in others]

    if verb == "list-sessions":
        if not rows:
            sys.stderr.write("no server running\n")
            fail()
        for name, _attached, _tty, _pid in rows:
            count = sum(1 for c in clients() if c[0] == name)
            print(render_format(argv, {"session_name": name,
                                       "session_attached": count}))
        return
    if verb == "list-clients":
        target = argv[argv.index("-t") + 1] if "-t" in argv else None
        for name, tty, pid in clients():
            if target is not None and name != target:
                continue
            print(render_format(argv, {"session_name": name,
                                       "client_tty": tty, "client_pid": pid}))
        return
    if verb == "kill-session":
        target = argv[argv.index("-t") + 1] if "-t" in argv else None
        remaining = [r for r in rows if r[0] != target]
        if len(remaining) == len(rows):
            sys.stderr.write("session not found\n")
            fail()
        write_rows("sessions.txt", remaining)
        write_rows("clients.txt", [c for c in others if c[0] != target])
        write_rows("windows.txt",
                   [w for w in read_rows("windows.txt")
                    if w[2] != "xtm-" + target])
        return
    if verb == "detach-client":
        # Detaching closes the window (the xterm's tmux command returns)
        # but leaves the session, its windows and its panes running, so
        # the row stays and only its attached flag is cleared. `-s` throws
        # every client of the session off; `-t TTY` only that one.
        session = argv[argv.index("-s") + 1] if "-s" in argv else None
        tty = argv[argv.index("-t") + 1] if "-t" in argv else None
        # A real tty names one client. The fakes share /dev/null between
        # sessions, so `-t` takes the first match, as tmux's one would be.
        detached = set()
        updated = []
        for r in rows:
            by_tty = tty is not None and r[2] == tty and not detached
            if r[1] != "0" and (r[0] == session or by_tty):
                detached.add(r[0])
                updated.append([r[0], "0"] + list(r[2:]))
            else:
                updated.append(r)
        kept = [c for c in others
                if not (c[0] == session or (c[1] == tty and not detached))]
        if not detached and len(kept) == len(others):
            sys.stderr.write("can't find client\n")
            fail()
        write_rows("sessions.txt", updated)
        write_rows("clients.txt", kept)
        write_rows("windows.txt",
                   [w for w in read_rows("windows.txt")
                    if w[2] not in ["xtm-" + d for d in detached]])
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
    # A terminal that takes a moment to attach, so a test can check that
    # xtm waits for THIS client rather than taking whichever is there first.
    time.sleep(float(os.environ.get("XTM_FAKE_XTERM_ATTACH_DELAY", "0")))
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


def tool_xrandr(argv):
    # XTM_FAKE_MONITORS is "name:WxH+X+Y,..."; unset, xrandr fails and xtm
    # checks nothing, which keeps every test that does not care unaffected.
    monitors = os.environ.get("XTM_FAKE_MONITORS")
    if "--listmonitors" not in argv or not monitors:
        fail()
    entries = [m.split(":", 1) for m in monitors.split(",")]
    print("Monitors: %d" % (len(entries),))
    for index, (name, spec) in enumerate(entries):
        m = re.match(r"(\d+)x(\d+)([+-]\d+)([+-]\d+)", spec)
        print(" %d: +%s %s/300x%s/200%s%s  %s"
              % (index, name, m.group(1), m.group(2), m.group(3), m.group(4), name))


TOOLS = {
    "xrandr": tool_xrandr,
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
            ["tmux", "xterm", "xdotool", "wmctrl", "xwininfo", "xprop",
             "xrandr"])):
        script = bin_dir / name
        script.write_text("#!/bin/sh\nexec %s %s \"$0\" \"$@\"\n"
                          % (sys.executable, impl))
        script.chmod(0o755)
    return bin_dir


def socket_hostname():
    """This machine's short hostname, sanitised the way xtm sanitises it."""
    return xtm.short_hostname()


class PathIsolationMixin(unittest.TestCase):
    """Point xtm's config and state at a scratch directory for one test.

    Enters an xtm.scoped_context() for the duration, so the whole RunContext
    is restored afterwards. This used to save and restore five named fields
    by hand, which meant every field added to the context was one someone
    had to remember to add here too -- and forgetting left state leaking
    into whatever test ran next.
    """

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="xtm-test-"))
        self.addCleanup(shutil.rmtree, str(self.tmp), True)
        self._context = xtm.scoped_context()
        self._context.__enter__()
        self.addCleanup(self._context.__exit__, None, None, None)
        xtm.resolve_paths(str(self.tmp / "config.yaml"), str(self.tmp / "state"))


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

    def add_session(self, name, tty="/dev/null", attached="1", pid=1,
                    owned=True):
        """A running session. Attached, it is shown in an xtm window unless
        `owned` is False: its client is then a real process whose command
        line carries `-name xtm-<name>`, exactly what xtm looks for in /proc
        to tell its own windows from anyone else's."""
        if attached != "0" and owned:
            pid = self.xtm_window_process(name)
        rows = self.rows("sessions.txt")
        rows.append([name, attached, tty, pid])
        self.write_rows("sessions.txt", rows)

    def add_client(self, session, tty="/dev/pts/99", pid=1):
        """A second client on a session that is not an xtm window: another
        terminal, or an SSH login."""
        rows = self.rows("clients.txt")
        rows.append([session, tty, pid])
        self.write_rows("clients.txt", rows)

    def xtm_window_process(self, session):
        """A live stand-in for the xterm xtm opens for `session`."""
        proc = subprocess.Popen(
            [sys.executable, "-c", "import time; time.sleep(120)",
             "-name", "xtm-" + session],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.addCleanup(proc.wait)
        self.addCleanup(proc.kill)
        return proc.pid

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
