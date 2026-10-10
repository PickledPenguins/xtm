#!/usr/bin/env python3
"""
xtm - xterm + tmux workspace manager

Manages a set of xterm windows, each attached to a named tmux session,
positioned on screen according to a per-machine "profile" stored in a
small YAML config file.

Day-to-day use (opening, resetting and closing sessions) needs xterm, tmux,
python3 and PyYAML, which reads the config. Window placement is
performed by writing xterm's own window-control escape sequences directly
to the pty that tmux reports as the session's client tty.

Reading CURRENT window positions back off the screen (--update-profile,
--new-profile, --focus, --verify) does require something that can ask the
X server "where is this window". xtm uses whichever of xdotool / wmctrl /
xwininfo is already installed, and says so clearly when none are found;
the config file can always be edited by hand instead.

Config file:  ~/.config/xtm/config.yaml       (override: XTM_CONFIG_DIR, --config)
State file:   ~/.local/state/xtm/current_profile.<host>
                                             (override: XTM_STATE_DIR, --state-dir)

Compatibility: this targets Python 3.6+ with PyYAML as its one third-party
package, so it runs on long-lived enterprise systems as well as current
ones. That rules out PEP 604 "X | Y" unions (3.10+),
PEP 585 built-in generics such as list[str] (3.9+), and
`from __future__ import annotations` (3.7+, and no help on 3.6 anyway
since newer syntax fails at compile time, before any runtime version
check could run). All type hints therefore use the classic `typing`
spellings. For the same reason subprocess calls use `stdout=PIPE,
stderr=PIPE, universal_newlines=True` rather than the 3.7-only
`capture_output=` / `text=` keywords, and `typing.NoReturn` (3.6.2+) is
not used at all.
"""

import argparse
import contextlib
import fnmatch
import json
import logging
import math
import os
import platform
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

__version__ = "0.5"
# Versioning scheme: 0.<N>, N incrementing by 1 each release (0.9 -> 0.10,
# never rolling over to 1.0). The commit log is the record of what changed.

# --------------------------------------------------------------------------
# Type aliases. Config data is loaded dynamically and validated at runtime,
# so it is typed loosely here; validate_geometry() and validate_profile()
# enforce the real shape, which matters more than static shape-checking for
# hand-editable YAML input.
# --------------------------------------------------------------------------

ConfigDict = Dict[str, Any]
ProfileDict = Dict[str, Any]
GeometryDict = Dict[str, Any]

# --------------------------------------------------------------------------
# Paths / constants
# --------------------------------------------------------------------------

DEFAULT_CONFIG_DIR = Path.home() / ".config" / "xtm"
DEFAULT_STATE_DIR = Path.home() / ".local" / "state" / "xtm"


class RunContext(object):
    """Everything one invocation needs to know that is not a direct argument.

    These were seven loose module globals. Gathering them into one object
    does not make them less global -- every helper still reaches for
    CONTEXT rather than being handed it -- but it fixes the part that
    actually caused trouble:

      * There is one thing to save and restore, not seven. Tests previously
        hand-rolled that, and PathIsolationMixin restored five of them while
        TestFrameCompensation separately restored a sixth, so a partial
        restore was one edit away at any time.
      * Derived values cannot drift. config_dir and state_file are computed
        from their sources rather than assigned alongside them, so they
        cannot disagree with the paths they are derived from.
      * Overriding for a scope is a supported operation (see
        scoped_context) instead of an ad hoc save/assign/restore dance.

    The alternative -- threading a context parameter through all 126
    functions -- was considered and rejected: it would put an argument that
    is never read at the call site into signatures like
    _write_text_safe(path, content, description), making every one of them
    worse to read in exchange for a purity that this program, a
    single-invocation CLI, gets nothing from.
    """

    def __init__(self) -> None:
        """Start from the defaults a bare invocation would use."""
        self.config_file = DEFAULT_CONFIG_DIR / "config.yaml"
        self.state_dir = DEFAULT_STATE_DIR
        # Pretend mode: no window is opened, moved or closed and no file is
        # written. Checked at every side-effecting call rather than at one
        # gate, so a new side effect has to opt in deliberately.
        self.dry_run = False
        # Whether this invocation is one that writes the config at all.
        # Reporting commands set it False, and the reserved profile is then
        # injected in memory rather than saved: reading someone's config is
        # not a reason to rewrite it, and rewriting expands their flow style
        # and drops their comments.
        self.may_write_config = True
        # The machine this run acts as. None means "this one". Set by
        # --hostname, so a config can be checked as the machine it was
        # written for: a profile bound with `match:` is hidden everywhere
        # else, and auto-selection -- the thing the binding is for -- could
        # otherwise only be exercised by being on that host.
        self.hostname = None  # type: Optional[str]
        # Read each window's position back after placing it and report the
        # difference from what was asked for.
        self.verify = False
        # Correct captured positions for window-manager decorations.
        self.frame_compensation = True
        # Move a window that would land off every monitor back onto one,
        # rather than only warning about it.
        self.clamp = False

    @property
    def config_dir(self) -> Path:
        """Directory holding the config file."""
        return self.config_file.parent

    @property
    def state_file(self) -> Path:
        """This machine\'s current-profile state file.

        Per-machine by construction: a shared or NFS-mounted home directory
        would otherwise let two machines fight over one "current profile",
        which defeats the entire point of per-machine profiles.
        """
        return self.state_dir / ("current_profile." + short_hostname())


CONTEXT = RunContext()


@contextlib.contextmanager
def scoped_context(**overrides: Any) -> Any:
    """Temporarily replace the active RunContext, restoring it afterwards.

    The supported way to run something under different settings without
    leaking them into whatever runs next:

        with scoped_context(dry_run=True):
            ...

    Restores the whole context, so no field can be missed on the way back.
    """
    global CONTEXT
    previous = CONTEXT
    replacement = RunContext()
    replacement.__dict__.update(previous.__dict__)
    for key, value in overrides.items():
        if not hasattr(replacement, key):
            raise AttributeError("RunContext has no field %r" % (key,))
        setattr(replacement, key, value)
    CONTEXT = replacement
    try:
        yield replacement
    finally:
        CONTEXT = previous

TITLE_PREFIX = "xtm:"
DEFAULT_TITLE_TEMPLATE = "xtm:{session}"
# How a profile disposes of its own windows when the user switches away.
SWITCH_MODES = ("leave", "detach", "kill")
DEFAULT_SWITCH_MODE = "detach"
# Window instance name (WM_CLASS) prefix. Unlike the title, a running
# program inside the terminal cannot change this, so it survives
# `set-titles on` in .tmux.conf and shells that rewrite the title.
INSTANCE_PREFIX = "xtm-"

# Session/profile names are restricted to this charset so they can never
# contain ':' (which would break TITLE_PREFIX parsing and tmux's own target
# syntax) or characters that need quoting as a YAML key.
# Uses \Z rather than $ deliberately: in Python regex $ also matches just
# before a trailing newline, so a name like "work1\n" would otherwise
# incorrectly pass validation.
SESSION_NAME_RE = re.compile(r"^[A-Za-z0-9_.\-]+\Z")

DEFAULT_PROFILE_NAME = "default"
# The "default" profile is reserved: it is created with the config, cannot be
# deleted or renamed, and is always global (never machine-specific), so it is
# guaranteed to exist as the last resort of profile resolution. That
# guarantee is what stops the effective default silently changing when the
# config is edited or reordered.
DEFAULT_STACK = {
    "x": 40,
    "y": 40,
    "width": 900,
    "height": 650,
    "offset_x": 40,
    "offset_y": 40,
}  # type: GeometryDict

DEFAULT_CONFIG = {
    "profiles": {
        DEFAULT_PROFILE_NAME: {
            "stack": dict(DEFAULT_STACK),
            "sessions": {},
        }
    }
}  # type: ConfigDict

WAIT_FOR_ATTACH_TIMEOUT_SECONDS = 6.0
WAIT_FOR_ATTACH_POLL_INTERVAL_SECONDS = 0.15
DEFAULT_SUBPROCESS_TIMEOUT_SECONDS = 10.0

GEOMETRY_TOOLS = ("xdotool", "wmctrl", "xwininfo")

LOGGER_NAME = "xtm"
logger = logging.getLogger(LOGGER_NAME)

# Exit codes. Documented in docs/cli.md#exit-status; keep the two in sync.
EXIT_OK = 0
EXIT_ERROR = 1
EXIT_USAGE = 2
EXIT_INTERRUPTED = 130


# --------------------------------------------------------------------------
# Logging
# --------------------------------------------------------------------------

def setup_logging(console_level: int, log_file: Optional[str],
                  log_file_level: int = logging.DEBUG) -> None:
    """Configure the module logger's console and optional file handlers.

    Console logging is ON by default at INFO, writing to stderr, so that
    progress and decisions are visible during a normal run. Data output
    (--list, --json, profile names) is written to stdout with print()
    instead, which keeps it pipeable and unpolluted no matter how noisy
    the log stream is.

    File logging is OFF by default; --log-file turns it on and captures
    full DEBUG detail regardless of the console level, so a run can be
    diagnosed after the fact without having to reproduce it under --debug.
    """
    logger.setLevel(logging.DEBUG)  # handlers below do their own filtering
    for handler in list(logger.handlers):
        logger.removeHandler(handler)
        handler.close()
    logger.propagate = False  # never double-log through the root logger

    console = logging.StreamHandler(sys.stderr)
    console.setLevel(console_level)
    if console_level <= logging.DEBUG:
        console.setFormatter(logging.Formatter(
            "%(asctime)s [%(levelname)-7s] %(message)s", datefmt="%H:%M:%S"))
    else:
        console.setFormatter(_ConsoleFormatter())
    logger.addHandler(console)

    if log_file:
        expanded = os.path.expanduser(log_file)
        try:
            os.makedirs(os.path.dirname(expanded) or ".", exist_ok=True)
            file_handler = logging.FileHandler(expanded)
        except OSError as e:
            logger.warning("Could not open log file %r: %s", expanded, e)
        else:
            file_handler.setLevel(log_file_level)
            file_handler.setFormatter(logging.Formatter(
                "%(asctime)s [%(levelname)-7s] %(name)s: %(message)s"))
            logger.addHandler(file_handler)
            logger.debug("Logging to file %s at level %s",
                         expanded, logging.getLevelName(log_file_level))


class _ConsoleFormatter(logging.Formatter):
    """Keep ordinary progress lines clean while still labelling problems.

    INFO is the normal running commentary and reads better unprefixed;
    WARNING and above are prefixed so they stand out in a terminal that
    is also showing the command's real output.
    """

    def format(self, record: logging.LogRecord) -> str:
        """Render one console record, prefixing only warnings and errors."""
        message = record.getMessage()
        if record.levelno <= logging.INFO:
            return message
        return "xtm: %s: %s" % (record.levelname.lower(), message)


def console_level_from_args(args: argparse.Namespace) -> int:
    """Resolve the console log level from the mutually reinforcing
    verbosity flags, most explicit first."""
    if getattr(args, "log_level", None):
        return getattr(logging, args.log_level.upper())
    if args.debug:
        return logging.DEBUG
    if args.quiet:
        return logging.WARNING
    return logging.INFO


# --------------------------------------------------------------------------
# Errors
# --------------------------------------------------------------------------

class XtmError(Exception):
    """User-facing error. Caught in main() and reported as a single line
    without a Python traceback; the traceback is still available under
    --debug."""


def die(msg: str) -> Any:
    """Raise an XtmError. Named `die` because that is what it does from
    the caller's point of view: every call site treats it as terminal.

    Deliberately annotated `-> Any` rather than `-> NoReturn`, because
    typing.NoReturn only exists on Python 3.6.2+ and this tool supports
    3.6.0.
    """
    raise XtmError(msg)


# --------------------------------------------------------------------------
# Name validation
# --------------------------------------------------------------------------

def is_valid_session_name(name: Optional[str]) -> bool:
    """Non-raising charset check, used when filtering rather than
    rejecting (for example, skipping a stray window with an odd title)."""
    return bool(name) and bool(SESSION_NAME_RE.match(name))


def validate_session_name(name: str) -> str:
    """Raising form of is_valid_session_name, for input that must be
    correct to proceed (such as a session name typed by the user)."""
    if not is_valid_session_name(name):
        die("Invalid session name %r. Only letters, digits, '.', '_' and "
            "'-' are allowed." % (name,))
    return name


def validate_profile_name(name: str) -> str:
    """Same charset rules as session names. This keeps profile names plain
    YAML keys and safe to store as text in the current-profile state
    file."""
    if not is_valid_session_name(name):
        die("Invalid profile name %r. Only letters, digits, '.', '_' and "
            "'-' are allowed." % (name,))
    return name


# --------------------------------------------------------------------------
# Subprocess helpers
# --------------------------------------------------------------------------

def _truncate(value: Optional[str], limit: int = 300) -> str:
    """Shorten long subprocess output so debug logs stay readable."""
    if not value:
        return ""
    value = value.strip()
    if len(value) > limit:
        return value[:limit] + "...<%d more chars>" % (len(value) - limit,)
    return value


def _subprocess_env() -> Dict[str, str]:
    """Environment for xtm's own information-gathering subprocess calls
    (tmux queries, wmctrl/xdotool/xwininfo/xprop): force the C locale so
    their output stays in a consistent, parseable form regardless of the
    user's configured locale.

    Deliberately NOT used for the interactive xterm/tmux session spawned
    by spawn_xterm(), which must inherit the user's normal environment so
    that real work (UTF-8 rendering, for instance) is unaffected.
    """
    env = dict(os.environ)
    env["LC_ALL"] = "C"
    env["LANG"] = "C"
    return env


def run(cmd: Sequence[str], timeout: float = DEFAULT_SUBPROCESS_TIMEOUT_SECONDS,
        required: bool = True) -> Optional[subprocess.CompletedProcess]:
    """Run one of xtm's internal information-gathering commands.

    Adds text-mode output, a timeout so a wedged external tool cannot hang
    xtm forever, a forced C locale for consistent parsing, a clean error
    instead of a traceback when a program is missing or not executable,
    and debug logging of every command and its result.

    With required=False a missing program yields None instead of an error,
    which is how optional helpers such as xprop are probed.

    Note the explicit stdout/stderr/universal_newlines arguments: the
    tidier capture_output= and text= keywords are Python 3.7+, and this
    tool supports 3.6.
    """
    cmd = list(cmd)
    logger.debug("run: %s", " ".join(cmd))
    try:
        result = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            universal_newlines=True,
            env=_subprocess_env(),
            timeout=timeout,
        )
    except FileNotFoundError:
        if not required:
            logger.debug("run: %r not found on PATH (optional)", cmd[0])
            return None
        return die("Required program %r was not found on PATH." % (cmd[0],))
    except subprocess.TimeoutExpired:
        return die("Command %r timed out after %ss." % (" ".join(cmd), timeout))
    except OSError as e:
        # Broader than FileNotFoundError: catches a binary that exists on
        # PATH but is not executable, and other exec-time failures, rather
        # than leaking a raw traceback.
        if not required:
            logger.debug("run: could not run %r: %s (optional)", cmd[0], e)
            return None
        return die("Could not run %r: %s" % (cmd[0], e))
    logger.debug("run: -> rc=%s stdout=%s stderr=%s", result.returncode,
                 _truncate(result.stdout), _truncate(result.stderr))
    return result


def which_or_none(name: str) -> Optional[str]:
    """Locate a program on PATH, returning None rather than raising."""
    return shutil.which(name)


# --------------------------------------------------------------------------
# Safe file I/O (turns permission/disk errors into clean XtmErrors)
# --------------------------------------------------------------------------

def _read_text_safe(path: Path, context: str) -> str:
    """Read a text file, reporting failures as a user-facing error."""
    try:
        return path.read_text()
    except OSError as e:
        return die("Could not read %s (%s): %s" % (context, path, e))


def _write_text_safe(path: Path, content: str, context: str) -> None:
    """Write `content` to `path` atomically and durably.

    The temp file is created in the SAME directory as the target, because
    os.replace() is only atomic within a filesystem. Contents are flushed
    and fsync'd before the swap so that a crash or power loss cannot leave
    a present-but-empty file, and the parent directory is fsync'd
    afterwards so the rename itself is durable. The target therefore ends
    up either fully untouched or fully replaced, never truncated.

    Files are created with mkstemp's 0600 permissions, which is
    appropriate for per-user configuration and state.
    """
    if CONTEXT.dry_run:
        logger.info("[dry-run] would write %s (%s), %d bytes",
                    path, context, len(content))
        return
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp_name = tempfile.mkstemp(
            dir=str(path.parent), prefix="." + path.name + ".", suffix=".tmp")
        tmp_path = Path(tmp_name)
        try:
            with os.fdopen(fd, "w") as f:
                f.write(content)
                f.flush()
                os.fsync(f.fileno())
            os.replace(str(tmp_path), str(path))
            _fsync_directory(path.parent)
        except BaseException:
            try:
                tmp_path.unlink()
            except OSError:
                pass  # best-effort cleanup; the original error is what matters
            raise
    except OSError as e:
        die("Could not write %s (%s): %s" % (context, path, e))
    logger.debug("Wrote %s (%s), %d bytes", path, context, len(content))


def _fsync_directory(directory: Path) -> None:
    """Flush a directory entry so a completed rename survives a crash.

    Best-effort: some filesystems refuse O_RDONLY fsync on directories,
    and failing to harden the rename is not worth aborting a write that
    has already succeeded.
    """
    try:
        dir_fd = os.open(str(directory), os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(dir_fd)
    except OSError:
        pass
    finally:
        os.close(dir_fd)


# --------------------------------------------------------------------------
# Config file locking
# --------------------------------------------------------------------------

class config_lock(object):
    """Serialize read-modify-write cycles on the config file.

    Atomic writes prevent a corrupted file, but they do not prevent a lost
    update: two concurrent --update-profile runs would each load the same
    starting config and the second write would discard the first one's
    changes. An advisory lock on a sibling .lock file closes that window.

    A separate lock file is used rather than locking config.yaml itself,
    because the atomic write replaces the config inode and would otherwise
    detach the lock from the file being protected. Locking is skipped
    entirely when fcntl is unavailable or the lock file cannot be created,
    since an unlocked run is still far better than a failed one.
    """

    def __init__(self, path: Optional[Path] = None) -> None:
        """Prepare a lock beside `path`, defaulting to the active config."""
        self.path = (path or CONTEXT.config_file).with_suffix(
            (path or CONTEXT.config_file).suffix + ".lock")
        self._handle = None  # type: Any

    def __enter__(self) -> "config_lock":
        """Take the exclusive lock, degrading to no locking on failure."""
        if CONTEXT.dry_run:
            # A dry run promises to touch nothing, and this was the one write
            # path that ignored it: creating the lock file (and its parent
            # directory) left `xtm -N` with a filesystem side effect. There is
            # nothing to serialize anyway, since no write will follow.
            logger.debug("[dry-run] not creating the config lock %s", self.path)
            return self
        try:
            import fcntl
        except ImportError:  # pragma: no cover - non-POSIX platforms only
            logger.debug("fcntl unavailable; proceeding without a config lock")
            return self
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self._handle = open(str(self.path), "w")
            # Try without blocking first, purely so that waiting can be
            # reported. A run that simply stops with no output looks like a
            # hang; saying what it is waiting for turns that into a fact the
            # user can act on.
            try:
                fcntl.flock(self._handle.fileno(),
                            fcntl.LOCK_EX | fcntl.LOCK_NB)
            except (IOError, OSError):
                logger.info("Waiting for another xtm to finish writing %s ...",
                            CONTEXT.config_file)
                fcntl.flock(self._handle.fileno(), fcntl.LOCK_EX)
            logger.debug("Acquired config lock %s", self.path)
        except OSError as e:
            logger.debug("Could not acquire config lock %s: %s", self.path, e)
            self._close()
        return self

    def __exit__(self, *exc_info: Any) -> bool:
        """Release the lock and let any exception propagate."""
        self._close()
        return False

    def _close(self) -> None:
        """Close the lock file handle if one is open."""
        if self._handle is not None:
            try:
                self._handle.close()
            except OSError:
                pass
            self._handle = None


# --------------------------------------------------------------------------
# YAML: reading and writing the config
# --------------------------------------------------------------------------
#
# The config is YAML, read and written by PyYAML's safe loader and dumper. Two
# things are adjusted, both about names:
#
#   * A KEY is a name, taken as the text written. YAML 1.1 would read the slot
#     `on:` as the boolean True, `0755:` as the number 493 and `~:` as None,
#     and xtm looks every profile and slot up by its name -- so each of those
#     would silently become a different name, or none. Keys are therefore
#     never typed: whatever is written is the name, and the name rules then
#     accept or refuse it.
#   * A key written twice is refused. YAML keeps the last and drops the rest
#     without a word, so a slot pasted in twice would lose its first position.
#
# Values keep YAML's own meaning: `yes` is a boolean and `0755` is octal, as
# every other YAML reader of the same file would say. The validators name the
# field and suggest quoting when text was expected and YAML read something else.

try:
    import yaml  # type: ignore
except ImportError:  # pragma: no cover - reported when the config is read
    yaml = None

YAML_INSTALL_HINT = (
    "xtm reads its config with PyYAML, which is not installed for %s. "
    "Install it with the system package manager (the package is usually "
    "python3-yaml or python3-pyyaml) or with `python3 -m pip install --user "
    "PyYAML`." % (sys.executable,))


def _require_yaml() -> Any:
    """The yaml module, or a clean error saying how to install it."""
    if yaml is None:
        die(YAML_INSTALL_HINT)
    return yaml


if yaml is not None:

    # The libyaml-backed classes when PyYAML was built with them: about ten
    # times faster on a large config, and the parsing rules are the same.
    _SafeLoaderBase = getattr(yaml, "CSafeLoader", yaml.SafeLoader)
    _SafeDumperBase = getattr(yaml, "CSafeDumper", yaml.SafeDumper)

    class _ConfigLoader(_SafeLoaderBase):  # type: ignore
        """PyYAML's safe loader, with keys read as names."""

        def construct_mapping(self, node: Any, deep: bool = False) -> Any:
            """Build a mapping whose keys are the text written, never typed.

            Duplicates are looked for among the keys written in this mapping
            only, before flatten_mapping() folds a merge key (`<<: *base`) in
            as the safe loader would. A key written beside a merge overrides
            the merged one on purpose, and must not count as written twice.
            """
            if not isinstance(node, yaml.MappingNode):
                raise yaml.constructor.ConstructorError(
                    None, None, "expected a mapping, found %s" % (node.id,),
                    node.start_mark)
            written = set()
            for key_node, _value_node in node.value:
                if not isinstance(key_node, yaml.ScalarNode):
                    raise yaml.constructor.ConstructorError(
                        "while reading a mapping", node.start_mark,
                        "a key must be a name, not a list or a mapping",
                        key_node.start_mark)
                if key_node.tag == "tag:yaml.org,2002:merge":
                    continue
                if key_node.value in written:
                    raise yaml.constructor.ConstructorError(
                        "while reading a mapping", node.start_mark,
                        "the key %r appears twice; YAML would silently keep "
                        "only the last one" % (key_node.value,),
                        key_node.start_mark)
                written.add(key_node.value)
            self.flatten_mapping(node)
            mapping = {}  # type: Dict[str, Any]
            for key_node, value_node in node.value:
                # Merged keys come first, so a key written here wins.
                mapping[key_node.value] = self.construct_object(value_node,
                                                                deep=deep)
            return mapping

    class _ConfigDumper(_SafeDumperBase):  # type: ignore
        """PyYAML's safe dumper, keeping key order and writing no aliases."""

        def ignore_aliases(self, data: Any) -> bool:
            """Write every value out in full.

            An anchor and alias pair in a hand-edited config is fine, but xtm
            never writes one: a shared object in memory is an accident of how
            a profile was copied, not something the user asked for.
            """
            return True

    def _represent_in_order(dumper: Any, data: Dict[str, Any]) -> Any:
        """Represent a mapping in insertion order.

        A list of pairs rather than the dict itself, because every PyYAML
        release sorts a dict's keys when it has the chance; sort_keys=False
        only exists from 5.1. Order matters here: slots are placed in the
        order they are written.
        """
        return dumper.represent_mapping("tag:yaml.org,2002:map",
                                        list(data.items()))

    _ConfigDumper.add_representer(dict, _represent_in_order)


def dump_yaml(data: Dict[str, Any]) -> str:
    """Serialize the config as block-style YAML, in the order it is held."""
    return _require_yaml().dump(data, Dumper=_ConfigDumper,
                                default_flow_style=False, allow_unicode=True)


def load_yaml(text: str) -> Any:
    """Parse config text. Keys are names; see _ConfigLoader."""
    module = _require_yaml()
    try:
        return module.load(text, Loader=_ConfigLoader)
    except module.YAMLError as e:
        # PyYAML's own rendering names "<unicode string>" and spreads over
        # several lines; the line, column and problem are what the user needs,
        # in the same one-line shape as every other config error.
        mark = getattr(e, "problem_mark", None)
        problem = getattr(e, "problem", None)
        if mark is None or not problem:
            return die("%s is not valid YAML: %s" % (CONTEXT.config_file, e))
        context = getattr(e, "context", None)
        return die("%s line %d, column %d: %s%s"
                   % (CONTEXT.config_file, mark.line + 1, mark.column + 1,
                      problem, " (%s)" % (context,) if context else ""))


# --------------------------------------------------------------------------
# Config load / save / validation
# --------------------------------------------------------------------------

def resolve_paths(config_path: Optional[str], state_dir: Optional[str]) -> None:
    """Fix the config and state locations for this run.

    Precedence is: explicit command-line path, then environment variable,
    then the XDG-style default. Both locations are overridable so that a
    machine-specific or throwaway configuration can be used without
    disturbing the real one.

    Only the two source paths are set. config_dir and state_file are derived
    from them by RunContext, so they cannot fall out of step with the paths
    they come from.
    """
    if config_path:
        CONTEXT.config_file = Path(os.path.expanduser(config_path))
    else:
        env_dir = os.environ.get("XTM_CONFIG_DIR")
        base = Path(os.path.expanduser(env_dir)) if env_dir else DEFAULT_CONFIG_DIR
        CONTEXT.config_file = base / "config.yaml"

    if state_dir:
        CONTEXT.state_dir = Path(os.path.expanduser(state_dir))
    else:
        env_state = os.environ.get("XTM_STATE_DIR")
        CONTEXT.state_dir = (Path(os.path.expanduser(env_state)) if env_state
                             else DEFAULT_STATE_DIR)


def short_hostname() -> str:
    """Host portion of the machine name, sanitised to the same charset as
    profile names so it is always safe inside a filename.

    Honors --hostname, so every part of a run -- matching, visibility and
    the per-machine state file -- agrees about which machine it is acting
    as. Overriding only the matching would produce a run that selected one
    machine's profile and remembered it as another's.
    """
    name = CONTEXT.hostname or platform.node().split(".")[0] or "unknown"
    return re.sub(r"[^A-Za-z0-9_.\-]", "_", name)





def load_config(create_if_missing: bool = True) -> ConfigDict:
    """Read, sanity-check and return the whole config file, creating a
    default one on first run."""
    if not CONTEXT.config_file.exists():
        if not create_if_missing:
            die("No config file at %s." % (CONTEXT.config_file,))
        logger.info("No config file at %s; creating a default one.", CONTEXT.config_file)
        _write_text_safe(CONTEXT.config_file, dump_yaml(DEFAULT_CONFIG), "config file")
        if CONTEXT.dry_run:
            # A deep copy, not dict(): a shallow one shares the nested
            # 'profiles' mapping with the module-level constant, and the
            # caller goes on to inject defaults and run a command against
            # it. A single in-process dry run would otherwise leave
            # DEFAULT_CONFIG permanently altered for everything after it,
            # which matters because this module is imported as well as run.
            return _deep_copy(DEFAULT_CONFIG)
    raw_text = _read_text_safe(CONTEXT.config_file, "config file")
    data = load_yaml(raw_text)
    if not isinstance(data, dict) or "profiles" not in data:
        die("%s does not look like an xtm config (no 'profiles' key)." % (CONTEXT.config_file,))
    # A bare "profiles:" with nothing under it is YAML's null, and means
    # "no profiles yet".
    if data["profiles"] is None:
        data["profiles"] = {}
    if not isinstance(data["profiles"], dict):
        die("%s: 'profiles' must be a mapping of profile name -> settings."
            % (CONTEXT.config_file,))
    for name in data["profiles"]:
        # Validated on load as well as on input: a hand-edited name reaches
        # tmux, window titles and the state file's contents from here.
        validate_profile_name(name)
    if DEFAULT_PROFILE_NAME not in data["profiles"]:
        # The reserved profile is the last resort of resolution, so it has
        # to exist for this run. Whether it is also written back depends on
        # whether this command writes the config anyway: a reporting command
        # that rewrote the file would expand the user's flow style and drop
        # their comments, on a run they asked only to read.
        data["profiles"][DEFAULT_PROFILE_NAME] = {
            "stack": dict(DEFAULT_STACK),
            "sessions": {},
        }
        if CONTEXT.may_write_config:
            logger.info("Adding the reserved %r profile to %s.",
                        DEFAULT_PROFILE_NAME, CONTEXT.config_file)
            save_config(data)
        else:
            logger.debug("Supplying the reserved %r profile for this run; "
                         "%s is not being written by this command.",
                         DEFAULT_PROFILE_NAME, CONTEXT.config_file)
    logger.debug("Loaded config from %s with profiles: %s",
                 CONTEXT.config_file, ", ".join(sorted(data["profiles"])) or "(none)")
    return data


def save_config(config: ConfigDict) -> None:
    """Persist the whole config file."""
    _write_text_safe(CONTEXT.config_file, dump_yaml(config), "config file")


def _check_number(entry: GeometryDict, key: str, context: str) -> None:
    """Validate one numeric geometry field.

    bool is rejected explicitly because it is a subclass of int in
    Python, and non-finite values are rejected because inf/nan parse
    happily as floats but blow up later with an opaque OverflowError or
    ValueError when converted to int for an escape sequence.
    """
    if key not in entry:
        die("%s: missing required field %r." % (context, key))
    value = entry[key]
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        die("%s: field %r must be a number, got %r." % (context, key, value))
    if not math.isfinite(float(value)):
        die("%s: field %r must be a finite number, got %r." % (context, key, value))


def validate_geometry(entry: GeometryDict, context: str,
                      require_offsets: bool = False) -> None:
    """Ensure a session or stack geometry mapping carries the numeric
    fields needed to build escape sequences, with sane values.

    Width and height must be positive; x and y may be negative, which is
    valid for monitors positioned left of or above the primary origin in
    a multi-monitor layout.
    """
    required = ["x", "y", "width", "height"]
    if require_offsets:
        required = required + ["offset_x", "offset_y"]
    for key in required:
        _check_number(entry, key, context)
    if entry["width"] <= 0 or entry["height"] <= 0:
        die("%s: 'width' and 'height' must be positive (got width=%s, height=%s)."
            % (context, entry["width"], entry["height"]))


def require_text(value: Any, context: str, field: str) -> None:
    """Refuse a field that must be text but was read as something else.

    YAML types an unquoted value: `yes`, `no`, `on` and `off` are booleans,
    `2026-01-01` is a date and `0755` is a number. The message says so,
    because "must be a string, got True" for `title: yes` reads as nonsense
    to someone who wrote the word.
    """
    if isinstance(value, str):
        return
    if isinstance(value, (dict, list)):
        die("%s: '%s' must be text, not a %s." % (
            context, field, "mapping" if isinstance(value, dict) else "list"))
    kind = {"bool": "boolean", "int": "number", "float": "number",
            "NoneType": "null"}.get(type(value).__name__, type(value).__name__)
    die("%s: '%s' must be text, but YAML read %r as a %s. Quote the value to "
        "keep it as written." % (context, field, value, kind))


def validate_session_entry(entry: GeometryDict, context: str) -> None:
    """Validate a slot's geometry plus its optional launch settings."""
    validate_geometry(entry, context)
    session = entry.get("session")
    if session is not None:
        require_text(session, context, "session")
        validate_session_name(session)
    title = entry.get("title")
    if title is not None:
        require_text(title, context, "title")
        validate_title_template(title, context)
    command = entry.get("command")
    if command is not None:
        require_text(command, context, "command")
    cwd = entry.get("cwd")
    if cwd is not None:
        require_text(cwd, context, "cwd")
    setup = entry.get("setup")
    if setup is not None:
        require_text(setup, context, "setup")
    xterm_args = entry.get("xterm_args")
    if xterm_args is not None:
        if not isinstance(xterm_args, (list, tuple)):
            die("%s: 'xterm_args' must be a list, got %r." % (context, xterm_args))
        for arg in xterm_args:
            if not isinstance(arg, (str, int, float)) or isinstance(arg, bool):
                die("%s: every entry in 'xterm_args' must be a string, got %r."
                    % (context, arg))


def validate_title_template(template: str, context: str) -> None:
    """Reject a title template that would fail at window-creation time.

    Checked when the config is read rather than when a window is opened,
    so a typo is reported by --validate instead of surfacing much later
    as a half-opened layout.
    """
    try:
        template.format(slot="s", session="s", profile="p")
    except (KeyError, IndexError, ValueError) as e:
        die("%s: invalid 'title' template %r: %s. Available fields are "
            "{slot}, {session} and {profile}." % (context, template, e))


def validate_prefix(value: Any, context: str) -> None:
    """Validate a profile's session-name prefix.

    The prefix is concatenated with a slot name to form a tmux session
    name, so the result has to satisfy the same charset rules as a name
    written out in full.
    """
    require_text(value, context, "prefix")
    if value and not is_valid_session_name(value + "x"):
        die("%s: 'prefix' %r would produce invalid session names. Only "
            "letters, digits, '.', '_' and '-' are allowed."
            % (context, value))


def validate_switch_mode(value: Any, context: str) -> None:
    """Validate a profile's on_switch setting."""
    if value not in SWITCH_MODES:
        die("%s: 'on_switch' must be one of %s, got %r."
            % (context, ", ".join(SWITCH_MODES), value))


def validate_match(entry: Any, context: str) -> None:
    """Validate a profile's machine-binding block.

    A profile carrying a 'match' block is machine-specific: it is only
    visible and selectable on a machine whose hostname the block matches.
    A profile without one is global and available everywhere.

    'hostname' may be a single glob or a list of them, so that one
    profile can serve several machines with the same screen layout.
    """
    if not isinstance(entry, dict):
        die("%s: 'match' must be a mapping." % (context,))
    if not entry:
        die("%s: 'match' is empty. Remove it to make the profile global, "
            "or give it a 'hostname'." % (context,))
    for key, value in entry.items():
        if key != "hostname":
            die("%s: unknown 'match' key %r (expected 'hostname')."
                % (context, key))
        for pattern in _match_patterns(value):
            if not isinstance(pattern, str):
                die("%s: 'match.hostname' must be a string or a list of "
                    "strings, got %r." % (context, pattern))
            if not pattern:
                die("%s: 'match.hostname' patterns must not be empty."
                    % (context,))
    if not _match_patterns(entry.get("hostname")):
        die("%s: 'match' needs a non-empty 'hostname'." % (context,))


def _match_patterns(value: Any) -> List[Any]:
    """Normalize a 'hostname' value to a list of patterns.

    Accepting both a bare string and a list keeps the common
    single-machine case uncluttered while allowing one profile to cover
    several machines.
    """
    if value is None:
        return []
    if isinstance(value, (list, tuple)):
        return list(value)
    return [value]


def profile_is_visible(profile: Any, host: Optional[str] = None) -> bool:
    """Whether a profile belongs on this machine.

    Global profiles (no 'match' block) are visible everywhere. A
    machine-specific one is visible only where its hostname patterns fit,
    so that a laptop's layouts do not clutter the workstation.
    """
    if not isinstance(profile, dict):
        return True   # malformed profiles stay visible so they can be fixed
    criteria = profile.get("match")
    if not isinstance(criteria, dict) or not criteria:
        return True
    # A malformed block is treated as visible rather than as "belongs to
    # some other machine". Hiding it would strand the profile behind a
    # misleading message: a typo such as `platform:` instead of
    # `hostname:` would make the profile vanish, and the user would be
    # told to use --all rather than that the key is wrong.
    if any(key != "hostname" for key in criteria):
        return True
    patterns = _match_patterns(criteria.get("hostname"))
    if not patterns or not all(isinstance(p, str) and p for p in patterns):
        return True
    if host is None:
        host = short_hostname()
    for pattern in patterns:
        if fnmatch.fnmatch(host, pattern):
            return True
    return False


def visible_profile_names(config: ConfigDict, include_all: bool = False
                          ) -> List[str]:
    """Names of the profiles usable on this machine, in sorted order."""
    profiles = config.get("profiles", {})
    if include_all:
        return sorted(profiles)
    host = short_hostname()
    return sorted(name for name, profile in profiles.items()
                  if profile_is_visible(profile, host))


def validate_profile(config: ConfigDict, name: str) -> ProfileDict:
    """Fill in missing defaults for one profile and validate it fully.

    This mutates `config` in place to inject 'stack'/'sessions' defaults,
    so a profile missing a section self-heals into a complete one. That is
    intentional and harmless -- it only reaches disk if the caller goes on
    to save the config -- but is worth knowing when tracing unexpected
    config changes after a run.

    Only the requested profile is deep-validated, so a problem in a
    profile that is not in use cannot block an unrelated command. Use
    --validate to check every profile at once.
    """
    profiles = config.setdefault("profiles", {})
    if name not in profiles:
        die("Profile %r does not exist in %s. Known profiles: %s"
            % (name, CONTEXT.config_file, ", ".join(sorted(profiles)) or "(none)"))
    profile = profiles[name]
    if not isinstance(profile, dict):
        die("Profile %r in %s must be a mapping, got %s."
            % (name, CONTEXT.config_file, type(profile).__name__))
    profile.setdefault("stack", dict(DEFAULT_STACK))
    profile.setdefault("sessions", {})
    # An empty "stack:" block is YAML's null, and gets the defaults: a stack
    # with no fields is unusable, and a profile missing the section already
    # self-heals into a complete one. setdefault cannot do this, because the
    # key is present -- it just has no value.
    if not profile["stack"]:
        profile["stack"] = dict(DEFAULT_STACK)
    # "sessions:" differs: an empty mapping is a legitimate value meaning
    # "no slots configured", so only None is folded.
    if profile["sessions"] is None:
        profile["sessions"] = {}
    if not isinstance(profile["stack"], dict):
        die("Profile %r: 'stack' must be a mapping." % (name,))
    if not isinstance(profile["sessions"], dict):
        die("Profile %r: 'sessions' must be a mapping." % (name,))

    validate_geometry(profile["stack"], "profile %r stack" % (name,),
                      require_offsets=True)
    if "prefix" in profile:
        validate_prefix(profile["prefix"], "profile %r" % (name,))
    if "on_switch" in profile:
        validate_switch_mode(profile["on_switch"], "profile %r" % (name,))
    if "title" in profile:
        require_text(profile["title"], "profile %r" % (name,), "title")
        validate_title_template(profile["title"], "profile %r" % (name,))
    if "match" in profile:
        if name == DEFAULT_PROFILE_NAME:
            die("Profile %r is reserved and must stay global, so it cannot "
                "have a 'match' block. Remove it, or use a differently named "
                "profile for machine-specific layouts." % (name,))
        validate_match(profile["match"], "profile %r" % (name,))
    for sess_name, sess_cfg in profile["sessions"].items():
        validate_session_name(sess_name)
        if not isinstance(sess_cfg, dict):
            die("Profile %r slot %r must be a mapping, got %s."
                % (name, sess_name, type(sess_cfg).__name__))
        validate_session_entry(sess_cfg, "profile %r slot %r" % (name, sess_name))

    # Two slots resolving to one session would fight over the same window
    # on every reset, so it is rejected rather than left to surprise the
    # user later.
    seen = {}  # type: Dict[str, str]
    for slot, session in profile_slots(profile):
        validate_session_name(session)
        if session in seen:
            die("Profile %r: slots %r and %r both resolve to session %r. "
                "Give one of them a different 'session'."
                % (name, seen[session], slot, session))
        seen[session] = slot
    return profile


@contextlib.contextmanager
def edit_profile(config: ConfigDict, profile_name: str, what: str) -> Any:
    """Apply an edit to one profile, or leave the profile untouched.

    Validation runs when the config is LOADED, so a command that edits a
    profile and saves it can write a file that every later command
    refuses. `xtm --set` could do exactly that: adding a slot whose
    prefixed name collides with another slot's explicit 'session:'
    reported success, and afterwards every command on that profile --
    including --validate and --delete-session -- failed with "both
    resolve to session", leaving hand-editing as the only way back.

    Wrapping the edit re-runs the same validation the next load would,
    and restores the profile as it was if the edit does not survive it,
    so the failure arrives before the write instead of after it.
    """
    profiles = config.setdefault("profiles", {})
    original = _deep_copy(profiles.get(profile_name))

    def restore():
        if original is None:
            profiles.pop(profile_name, None)
        else:
            profiles[profile_name] = original

    # The body and the validation are caught separately, because only one of
    # them means "the edit would leave this profile invalid". Catching both
    # under that message would report an unrelated failure inside a command
    # as a validation error, and blame the profile for it.
    try:
        yield
    except BaseException:
        restore()
        raise
    try:
        validate_profile(config, profile_name)
    except XtmError as e:
        restore()
        die("%s would leave profile %r invalid, so nothing was changed. %s"
            % (what, profile_name, e))
    except BaseException:
        # Anything else out of the validator is a bug rather than a refusal,
        # and the profile must still not be left carrying an edit that was
        # never accepted. Restore, then let it propagate unwrapped.
        restore()
        raise


def resolve_session_name(profile: ProfileDict, slot: str) -> str:
    """Tmux session name for one slot of a profile.

    A slot is a position on screen; the session is what gets attached to
    it. An explicit 'session' on the slot is used verbatim and is never
    prefixed, which is what lets two profiles name the same session and
    genuinely share it. Otherwise the profile's 'prefix' is prepended to
    the slot name, so a profile owns its sessions by default and two
    profiles cannot collide by accident.
    """
    slot_cfg = profile.get("sessions", {}).get(slot)
    if isinstance(slot_cfg, dict) and slot_cfg.get("session"):
        return str(slot_cfg["session"])
    return "%s%s" % (profile.get("prefix", ""), slot)


def resolve_window_title(profile: ProfileDict, profile_name: str,
                         slot: str, session: str) -> str:
    """Window title for one slot.

    Order of preference: the slot's own 'title', then the profile's
    'title' template, then the built-in default. Templates may use
    {slot}, {session} and {profile}.

    The title is cosmetic: windows are identified by their instance name
    (WM_CLASS), which is not configurable, so a custom title cannot break
    window discovery.
    """
    slot_cfg = profile.get("sessions", {}).get(slot)
    template = None
    if isinstance(slot_cfg, dict) and slot_cfg.get("title"):
        template = str(slot_cfg["title"])
    elif profile.get("title"):
        template = str(profile["title"])
    if template is None:
        template = DEFAULT_TITLE_TEMPLATE
    try:
        return template.format(slot=slot, session=session, profile=profile_name)
    except (KeyError, IndexError, ValueError) as e:
        # Validation catches this up front; reaching it here means a
        # template arrived by some other route, and a usable window with
        # a plain title beats no window at all.
        logger.warning("Invalid title template %r (%s); using the default.",
                       template, e)
        return DEFAULT_TITLE_TEMPLATE.format(session=session)


def profile_slots(profile: ProfileDict) -> List[Tuple[str, str]]:
    """(slot, session) for every slot in the profile, in config order."""
    return [(slot, resolve_session_name(profile, slot))
            for slot in profile.get("sessions", {})]


def profile_sessions(profile: ProfileDict) -> List[str]:
    """Session names this profile owns or shares, in config order."""
    return [session for _slot, session in profile_slots(profile)]


def slot_for_session(profile: ProfileDict, session: str) -> Optional[str]:
    """The slot a session occupies in this profile, if any.

    Fine for a single lookup. When looking up many sessions against the same
    profile, build a session_slot_index() once instead: this walks every slot
    per call, so a loop over it is quadratic in the number of slots.
    """
    return session_slot_index(profile).get(session)


def session_slot_index(profile: ProfileDict) -> Dict[str, str]:
    """{session name: slot name} for every slot in the profile.

    Built once and reused by callers that resolve a whole screenful of
    windows back to their slots. Earlier slots win on a duplicate, matching
    the first-match behavior a linear scan had; validate_profile() rejects
    duplicates outright, so this only matters for a profile that has not
    been validated yet.
    """
    index = {}  # type: Dict[str, str]
    for slot, session in profile_slots(profile):
        index.setdefault(session, slot)
    return index


def switch_mode(profile: ProfileDict) -> str:
    """What happens to a profile's windows when the user switches away."""
    return str(profile.get("on_switch", DEFAULT_SWITCH_MODE))


def session_geometry(sess_cfg: GeometryDict) -> GeometryDict:
    """Extract just the placement fields from a session entry, dropping
    launch settings such as command/cwd/xterm_args."""
    return {
        "x": sess_cfg["x"],
        "y": sess_cfg["y"],
        "width": sess_cfg["width"],
        "height": sess_cfg["height"],
    }


def parse_geometry_spec(spec: str) -> GeometryDict:
    """Parse the --set geometry argument, "x,y,width,height".

    A comma form is used rather than the classic X "WxH+X+Y" string
    because in X geometry a leading '-' means "measured from the opposite
    edge", which collides with genuinely negative coordinates on a
    multi-monitor desktop where a secondary screen sits left of the
    origin.
    """
    parts = [p.strip() for p in spec.split(",")]
    if len(parts) != 4:
        die("Geometry must be 'x,y,width,height' (for example '0,0,900,700'), "
            "got %r." % (spec,))
    values = []
    for label, raw in zip(("x", "y", "width", "height"), parts):
        try:
            values.append(int(raw))
        except ValueError:
            return die("Geometry field %r must be a whole number, got %r."
                       % (label, raw))
    geometry = dict(zip(("x", "y", "width", "height"), values))
    validate_geometry(geometry, "geometry %r" % (spec,))
    return geometry


# --------------------------------------------------------------------------
# Persisted "current profile" state and profile resolution
# --------------------------------------------------------------------------
#
# A child process cannot set an environment variable in its parent shell,
# so a live $XTM_PROFILE in an interactive shell is not achievable from
# here. This state file is the practical equivalent: it persists across
# separate xtm invocations exactly as an environment variable would across
# a shell session. docs/guide.md documents an optional shell wrapper for
# those who also want a real environment variable.

def read_current_profile_state() -> Optional[str]:
    """Return the persisted current profile for this machine, if any."""
    if not CONTEXT.state_file.exists():
        return None
    raw = _read_text_safe(CONTEXT.state_file, "current-profile state file")
    # Only the first line is meaningful; anything odd appended after it is
    # ignored rather than returned as a name that could never match a real
    # profile.
    first_line = raw.splitlines()[0].strip() if raw.strip() else ""
    if not first_line:
        return None
    logger.debug("Current profile %r read from %s", first_line, CONTEXT.state_file)
    return first_line


def write_current_profile_state(name: str) -> None:
    """Persist `name` as this machine's current profile."""
    _write_text_safe(CONTEXT.state_file, name + "\n", "current-profile state file")
    logger.debug("Persisted current profile as %r in %s", name, CONTEXT.state_file)


def match_profile(config: ConfigDict) -> Optional[str]:
    """Pick a machine-specific profile whose 'match' block fits this host.

    Patterns are shell globs tested against the short hostname. Profiles
    are considered in sorted name order so the choice is deterministic
    when more than one matches; a global profile (no 'match' block) is
    never auto-selected, since it gives no signal that it belongs to this
    machine in particular.
    """
    host = short_hostname()
    for name in sorted(config.get("profiles", {})):
        profile = config["profiles"][name]
        if not isinstance(profile, dict):
            continue
        criteria = profile.get("match")
        if not isinstance(criteria, dict) or not criteria:
            continue   # a global profile is never auto-selected
        if not profile_is_visible(profile, host):
            continue
        logger.debug("Profile %r matched host=%r", name, host)
        return name
    return None


def resolve_profile_name(config: ConfigDict, explicit: Optional[str],
                         auto: bool = False, include_all: bool = False,
                         switch_override: Optional[str] = None,
                         switch: bool = True) -> str:
    """Decide which profile this invocation uses.

    Order of precedence:
      1. An explicitly named profile, from the positional argument or
         --profile. With `switch` (every command but the reporting ones)
         it is also persisted as the new current profile, and switching
         away from a different profile disposes of that profile's windows
         according to its 'on_switch' setting.
      2. The persisted current profile for this machine, unless --auto
         was given.
      3. A machine-specific profile whose 'match' block fits this host.
      4. The reserved "default" profile.

    Step 4 is what makes a bare `xtm` work on any machine: "default"
    always exists, so resolution can never come up empty.

    Only profiles visible on this machine can be selected, so a layout
    belonging to another host cannot be reached by accident. `include_all`
    lifts that restriction for commands run with --all.
    """
    profiles = config.get("profiles", {})
    visible = visible_profile_names(config, include_all)
    if explicit:
        if explicit not in visible:
            die(_unknown_profile_message(config, explicit, include_all))
        if not switch:
            logger.debug("Using profile %r (named explicitly, this run only)",
                         explicit)
            return explicit
        previous = read_current_profile_state()
        if previous and previous != explicit:
            apply_switch_action(config, previous, explicit, switch_override)
        write_current_profile_state(explicit)
        logger.debug("Using profile %r (named explicitly)", explicit)
        return explicit

    if not auto:
        current = read_current_profile_state()
        if current:
            if current not in profiles:
                die("Current profile %r (from %s) no longer exists in %s. "
                    "Choose a valid one with 'xtm NAME'."
                    % (current, CONTEXT.state_file, CONTEXT.config_file))
            if current not in visible:
                # Reached by moving a saved profile to another machine, or
                # by editing its 'match' block. Falling through silently
                # would be worse than saying what happened.
                die("Current profile %r belongs to another machine and cannot "
                    "be used on %s. Choose one with 'xtm NAME' (see "
                    "--list-profiles)." % (current, short_hostname()))
            logger.debug("Using profile %r (persisted current profile)", current)
            return current

    matched = match_profile(config)
    if matched:
        logger.info("Auto-selected profile %r for host %s",
                    matched, short_hostname())
        return matched

    if DEFAULT_PROFILE_NAME in profiles:
        logger.debug("Using profile %r (reserved default)", DEFAULT_PROFILE_NAME)
        return DEFAULT_PROFILE_NAME
    return die("No current profile is set and the reserved %r profile is "
               "missing from %s. Name one with 'xtm NAME' (see "
               "--list-profiles)." % (DEFAULT_PROFILE_NAME, CONTEXT.config_file))


def apply_switch_action(config: ConfigDict, old_profile_name: str,
                        new_profile_name: str, mode_override: Optional[str]
                        ) -> None:
    """Dispose of the previous profile's windows when switching profiles.

    Sessions that the new profile also lays claim to are deliberately
    left alone: they are about to be repositioned into their new slot,
    and closing them only to reopen them would throw away the running
    window for no reason. That is what makes an explicitly shared
    'session' persist across a switch while profile-owned sessions come
    and go with the profile.
    """
    profiles = config.get("profiles", {})
    old_profile = profiles.get(old_profile_name)
    new_profile = profiles.get(new_profile_name)
    if not isinstance(old_profile, dict):
        return
    mode = mode_override or switch_mode(old_profile)
    if mode == "leave":
        logger.debug("Leaving profile %s windows alone on switch.",
                     old_profile_name)
        return
    keep = set(profile_sessions(new_profile)) if isinstance(new_profile, dict) else set()
    # Only sessions with an xtm window on screen: a session attached only
    # in somebody else's terminal is not one of this profile's windows.
    windows = xtm_windows()
    targets = [s for s in profile_sessions(old_profile)
               if s in windows and s not in keep]
    if not targets:
        logger.debug("No windows from profile %s need %sing.",
                     old_profile_name, mode)
        return
    done, failed = [], []
    for session in targets:
        ok = (tmux_detach_session(session, windows) if mode == "detach"
              else tmux_kill_session(session))
        (done if ok else failed).append(session)
    if done:
        logger.info("%s %d window(s) from profile %s: %s",
                    "Detached" if mode == "detach" else "Closed",
                    len(done), old_profile_name, ", ".join(done))
    if failed:
        # Not fatal: the switch itself succeeded, and leaving a window
        # open is a cosmetic problem rather than a broken state.
        logger.warning("Could not %s: %s", mode, ", ".join(failed))


def _unknown_profile_message(config: ConfigDict, name: str,
                             include_all: bool) -> str:
    """Explain why a named profile could not be used.

    A bare name is now the ordinary way to switch profiles, so the two
    likeliest mistakes are naming a session by accident and naming a
    profile that belongs to another machine. Both get a specific hint
    rather than a bare list of valid names.
    """
    profiles = config.get("profiles", {})
    visible = visible_profile_names(config, include_all)
    known = ", ".join(visible) or "(none)"
    if name in profiles:
        return ("Profile %r belongs to another machine and is not available "
                "on %s. Use --all to work with it anyway."
                % (name, short_hostname()))
    sessions = set()
    for profile in profiles.values():
        if isinstance(profile, dict) and isinstance(profile.get("sessions"), dict):
            sessions.update(profile["sessions"])
    if name in sessions or name in tmux_list_sessions():
        return ("There is no profile named %r, but there is a session with "
                "that name. Did you mean 'xtm --open %s' or "
                "'xtm --focus %s'? Known profiles: %s"
                % (name, name, name, known))
    return "Profile %r does not exist. Known profiles: %s" % (name, known)


# --------------------------------------------------------------------------
# tmux helpers
# --------------------------------------------------------------------------

# The separator between fields in the formats xtm asks tmux for. A colon,
# because tmux never allows one in a session name (it is the target
# separator, and tmux rewrites it to "_") and a tty path has none. A tab
# looked safer and was not: xtm runs tmux in the C locale, and tmux 3.6
# rewrites every control character in format output to "_" there, so
# "desk\t1" came back as "desk_1" and no session ever parsed as running.
_FIELD_SEPARATOR = ":"
_SESSION_FORMAT = "#{session_name}" + _FIELD_SEPARATOR + "#{session_attached}"
_CLIENT_FORMAT = "#{client_tty}" + _FIELD_SEPARATOR + "#{client_pid}"


def tmux_list_sessions() -> Dict[str, bool]:
    """Return {session_name: is_attached} for every tmux session.

    An empty dict means no tmux server is running yet; tmux exits
    non-zero in that case, which is expected rather than an error worth
    surfacing.
    """
    res = run(["tmux", "list-sessions", "-F", _SESSION_FORMAT], required=False)
    if res is None:
        # tmux itself is missing. Reporting commands should still work and
        # show the configured layout, so this degrades to "nothing running"
        # with a warning rather than aborting the whole run.
        logger.warning("tmux was not found on PATH; treating all sessions as "
                       "not running.")
        return {}
    if res.returncode != 0:
        logger.debug("No tmux sessions (no server running, or tmux returned an error)")
        return {}
    sessions = {}  # type: Dict[str, bool]
    for line in res.stdout.splitlines():
        if not line.strip():
            continue
        name, _, attached = line.partition(_FIELD_SEPARATOR)
        # #{session_attached} is a client COUNT, not strictly 0/1, so any
        # non-zero, non-empty value means at least one client is attached.
        sessions[name] = attached.strip() not in ("0", "")
    return sessions


def tmux_kill_session(name: str) -> bool:
    """Kill a tmux session. Returns False when it did not exist."""
    if CONTEXT.dry_run:
        logger.info("[dry-run] would kill tmux session %r", name)
        return True
    res = run(["tmux", "kill-session", "-t", name])
    if res is None or res.returncode != 0:
        logger.debug("kill-session %r failed: %s", name,
                     _truncate(res.stderr if res else ""))
        return False
    return True


def tmux_detach_client(tty: str) -> bool:
    """Detach one client, by its tty, closing the window it runs in.

    The window closes as a consequence rather than being killed: the
    xterm's `tmux new-session -A` command returns when its client detaches,
    so the terminal exits on its own. The session, its windows, its panes
    and everything running in them stay alive -- and so does any OTHER
    client attached to the same session, which `detach-client -s` would
    have thrown off too: an SSH login to the same session is not xtm's to
    close.
    """
    if CONTEXT.dry_run:
        logger.info("[dry-run] would detach the tmux client on %s", tty)
        return True
    res = run(["tmux", "detach-client", "-t", tty])
    if res is None or res.returncode != 0:
        logger.debug("detach-client %r failed: %s", tty,
                     _truncate(res.stderr if res else ""))
        return False
    return True


def tmux_detach_session(name: str, windows: Optional[Dict[str, List[str]]] = None
                        ) -> bool:
    """Close every xtm window on a session, leaving the session running.

    Only xtm's own clients are detached. Returns False when there was
    nothing to detach or a detach failed.
    """
    ttys = (xtm_windows() if windows is None else windows).get(name, [])
    if not ttys:
        return False
    return all([tmux_detach_client(tty) for tty in ttys])


# Every attached client, of every session, in one call.
_ALL_CLIENTS_FORMAT = ("#{session_name}" + _FIELD_SEPARATOR + "#{client_tty}"
                       + _FIELD_SEPARATOR + "#{client_pid}")


def tmux_clients() -> Dict[str, List[Tuple[str, Optional[int]]]]:
    """{session: [(client tty, client pid), ...]} for every attached client.

    An empty dict when no server is running or tmux is missing: both mean
    nothing is attached anywhere.
    """
    res = run(["tmux", "list-clients", "-F", _ALL_CLIENTS_FORMAT], required=False)
    if res is None or res.returncode != 0:
        return {}
    clients = {}  # type: Dict[str, List[Tuple[str, Optional[int]]]]
    for line in res.stdout.splitlines():
        if not line.strip():
            continue
        session, _, rest = line.partition(_FIELD_SEPARATOR)
        tty, _, pid_text = rest.partition(_FIELD_SEPARATOR)
        try:
            pid = int(pid_text)  # type: Optional[int]
        except ValueError:
            pid = None
        clients.setdefault(session, []).append((tty, pid))
    return clients


def _read_proc_ppid(pid: int) -> Optional[int]:
    """Parent PID of `pid`, read from /proc/<pid>/stat.

    Linux-specific, which is fine because xtm targets Linux hosts. Returns
    None when it cannot be read (process already gone, no /proc).
    """
    try:
        stat_text = Path("/proc/%d/stat" % (pid,)).read_text()
    except OSError:
        return None
    # The format is "pid (comm) state ppid ...", and comm can itself
    # contain spaces or parentheses, so split on the LAST ')' rather than
    # splitting the whole line on whitespace.
    after_comm = stat_text.rsplit(")", 1)[-1].split()
    if len(after_comm) < 2:
        return None
    try:
        return int(after_comm[1])
    except ValueError:
        return None


def _read_proc_cmdline(pid: int) -> List[str]:
    """The argument vector of a process, or [] when it cannot be read."""
    try:
        raw = Path("/proc/%d/cmdline" % (pid,)).read_bytes()
    except OSError:
        return []
    return [part.decode("utf-8", "replace") for part in raw.split(b"\0") if part]


def _is_descendant_of(pid: int, ancestor_pid: int, max_depth: int = 8) -> bool:
    """Whether `ancestor_pid` appears in `pid`'s parent chain within
    `max_depth` hops. Used to confirm that a tmux client really is
    running inside the xterm xtm just spawned."""
    current = pid
    for _ in range(max_depth):
        if current == ancestor_pid:
            return True
        parent = _read_proc_ppid(current)
        if parent is None or parent == current:
            return False
        current = parent
    return False


def _runs_in_xtm_window(pid: Optional[int], session: str, max_depth: int = 8
                        ) -> bool:
    """Whether a tmux client is inside the xterm xtm opens for `session`.

    xtm starts that xterm as `xterm ... -name xtm-<session> ... -e tmux
    new-session -A -s <session>`, so the client's parent -- or, through a
    wrapper, a nearby ancestor -- carries `-name xtm-<session>` in its
    command line. Nothing else does: a Konsole, an SSH login or a hand-
    opened xterm attached to the same session is somebody else's window,
    and xtm neither moves it, nor detaches it, nor counts it as the
    session's window.
    """
    if pid is None:
        return False
    instance = window_instance(session)
    current = pid
    for _ in range(max_depth):
        argv = _read_proc_cmdline(current)
        for index, arg in enumerate(argv[:-1]):
            if arg == "-name" and argv[index + 1] == instance:
                return True
        parent = _read_proc_ppid(current)
        if parent is None or parent == current or parent <= 1:
            return False
        current = parent
    return False


def _can_see_processes() -> bool:
    """Whether /proc is there to tell xtm's clients from anyone else's."""
    return os.path.isdir("/proc/self")


def xtm_windows(clients: Optional[Dict[str, List[Tuple[str, Optional[int]]]]] = None
                ) -> Dict[str, List[str]]:
    """{session: [tty, ...]} of the clients running in xtm's own windows.

    Without /proc there is no way to tell, and every attached client is
    taken to be xtm's -- the behavior before clients were told apart.
    """
    clients = tmux_clients() if clients is None else clients
    own_all = not _can_see_processes()
    windows = {}  # type: Dict[str, List[str]]
    for session, entries in clients.items():
        ttys = [tty for tty, pid in entries
                if own_all or _runs_in_xtm_window(pid, session)]
        if ttys:
            windows[session] = ttys
    return windows


def wait_for_own_client(session: str, proc: Optional[subprocess.Popen],
                        timeout: float = WAIT_FOR_ATTACH_TIMEOUT_SECONDS
                        ) -> Optional[str]:
    """Poll until the xterm just spawned has attached; return its tty.

    Only a client inside that xterm counts. Waiting for "any client" was
    satisfied at once by one already attached elsewhere -- an SSH login to
    the same session -- and xtm then moved that terminal instead of its own.

    When the spawned process exits first, that is reported with its real
    exit status, instead of making the user wait out the full timeout.
    """
    logger.debug("Waiting up to %.1fs for a client to attach to %r", timeout, session)
    deadline = time.time() + timeout
    while time.time() < deadline:
        for tty, pid in tmux_clients().get(session, []):
            if proc is None or (pid is not None and _is_descendant_of(pid, proc.pid)):
                logger.debug("%r attached on %s", session, tty)
                return tty
        if proc is not None and proc.poll() is not None:
            die("xterm exited immediately (status %s) without attaching to tmux "
                "session %r. Run with --debug, and check DISPLAY, the xterm "
                "settings in ~/.Xresources, and any 'xterm_args' in the profile."
                % (proc.returncode, session))
        time.sleep(WAIT_FOR_ATTACH_POLL_INTERVAL_SECONDS)
    logger.debug("Timed out waiting for %r to attach.", session)
    return None


# --------------------------------------------------------------------------
# Window placement via xterm's own window-control escape sequences
# --------------------------------------------------------------------------
#
#   CSI 3 ; x ; y t          -> move the window to pixel position (x, y)
#   CSI 4 ; height ; width t -> resize the window to the given pixel size
#
# These require the xterm to have been started with allowWindowOps enabled,
# which xtm always sets explicitly on every xterm it spawns. It is disabled
# by default in most distribution configurations for good reason: an
# untrusted program printing escape sequences to a terminal should not be
# able to move or resize windows.

_CSI_MOVE_WINDOW_TEMPLATE = "\x1b[3;{x};{y}t"
_CSI_RESIZE_WINDOW_PX_TEMPLATE = "\x1b[4;{height};{width}t"


def send_window_op(tty_path: str, seq: str) -> None:
    """Write one window-control escape sequence to a client's pty."""
    if CONTEXT.dry_run:
        logger.info("[dry-run] would send %r to %s", seq, tty_path)
        return
    logger.debug("Sending %r to %s", seq, tty_path)
    try:
        with open(tty_path, "w") as f:
            f.write(seq)
    except OSError as e:
        die("Could not write to %s: %s" % (tty_path, e))


def place_window(session: str, geometry: GeometryDict, tty: str) -> None:
    """Move and resize one session's window, through its client's tty.

    The tty is always xtm's own client: the caller found it among the
    session's clients by the xterm it runs in (see xtm_windows), so another
    terminal attached to the same session is never written to.

    Resize happens before move because on some window managers a resize
    can shift the window's anchor point, so moving last guarantees the
    final position is the requested one either way.
    """
    x, y = int(geometry["x"]), int(geometry["y"])
    width, height = int(geometry["width"]), int(geometry["height"])
    send_window_op(tty, _CSI_RESIZE_WINDOW_PX_TEMPLATE.format(height=height,
                                                             width=width))
    send_window_op(tty, _CSI_MOVE_WINDOW_TEMPLATE.format(x=x, y=y))
    if CONTEXT.verify:
        verify_placement(session, geometry)


def verify_placement(session: str, requested: GeometryDict,
                     settle_seconds: float = 0.25) -> None:
    """Re-read a window's geometry after placement and report the delta.

    Placement is fire-and-forget: the escape sequence is a request to the
    window manager, which is free to honor it approximately (size
    increments, panel struts, snapping). --verify turns that silent
    approximation into a visible one, which is the difference between a
    layout that "did not work" and one that worked as well as the window
    manager allows.
    """
    tool = detect_geometry_tool()
    if not tool:
        logger.warning("--verify needs one of %s to read window positions back; "
                       "skipping verification.", ", ".join(GEOMETRY_TOOLS))
        return
    time.sleep(settle_seconds)  # give the window manager time to act
    actual = get_window_geometry(session, tool, compensate=True)
    if not actual:
        logger.warning("--verify: could not read back the geometry of %r.", session)
        return
    deltas = dict((k, int(actual[k]) - int(requested[k]))
                  for k in ("x", "y", "width", "height"))
    if any(deltas.values()):
        logger.warning("%s: placed at x=%s y=%s %sx%s, requested x=%s y=%s %sx%s "
                       "(delta x=%+d y=%+d w=%+d h=%+d)",
                       session, actual["x"], actual["y"], actual["width"],
                       actual["height"], requested["x"], requested["y"],
                       requested["width"], requested["height"],
                       deltas["x"], deltas["y"], deltas["width"], deltas["height"])
    else:
        logger.info("%s: verified at x=%s y=%s %sx%s",
                    session, actual["x"], actual["y"],
                    actual["width"], actual["height"])


# --------------------------------------------------------------------------
# Window identification and geometry read-back (needs an external tool)
# --------------------------------------------------------------------------

def detect_geometry_tool() -> Optional[str]:
    """First available window-query tool, or None if none are installed."""
    for tool in GEOMETRY_TOOLS:
        if which_or_none(tool):
            logger.debug("Using %r for window queries", tool)
            return tool
    return None


def require_geometry_tool() -> str:
    """Like detect_geometry_tool, but fail with actionable advice."""
    tool = detect_geometry_tool()
    if not tool:
        die("This command has to read back current window positions, which "
            "requires one of: %s. None were found on PATH. Either ask an "
            "administrator whether one is available under a different name, "
            "or edit %s by hand instead."
            % (", ".join(GEOMETRY_TOOLS), CONTEXT.config_file))
    return tool


def require_acting_geometry_tool() -> str:
    """The first tool that can RAISE a window, not merely query one.

    xwininfo satisfies require_geometry_tool() but cannot focus anything,
    so asking for the capability up front is what separates "this tool
    cannot do that" from "no such window". Checking it afterwards, as
    --focus used to, reported a missing window whenever the window was
    absent, hiding the fact that the installed tool could never have
    raised it either.
    """
    for tool in GEOMETRY_TOOLS:
        if tool != "xwininfo" and which_or_none(tool):
            return tool
    if which_or_none("xwininfo"):
        die("Focusing a window needs xdotool or wmctrl. Only xwininfo was "
            "found on PATH, and it can query windows but not raise them.")
    return die("Focusing a window needs xdotool or wmctrl, and neither was "
               "found on PATH.")


def window_title(session: str) -> str:
    """The xterm title xtm gives a session's window."""
    return TITLE_PREFIX + session


def window_instance(session: str) -> str:
    """The WM_CLASS instance name xtm gives a session's window.

    Preferred over the title for identification, because a program
    running inside the terminal (a shell prompt, or tmux with
    `set-titles on`) can rewrite the title at any moment but cannot
    touch WM_CLASS.
    """
    return INSTANCE_PREFIX + session


# Matches a child-window line from `xwininfo -root -tree`, for example:
#   0x1400007 "xtm:work1": ("xtm-work1" "XTerm")  800x600+100+200  +100+200
# Captures the window id, the quoted title, and the quoted instance name.
_XWININFO_TREE_CHILD_RE = re.compile(
    r'^\s*(0x[0-9a-fA-F]+)\s+"([^"]*)"(?::\s+\("([^"]*)")?')


def _session_from_identifiers(title: str, instance: str) -> Optional[str]:
    """Recover a session name from a window's title and instance name.

    The instance name is authoritative when present; the title is the
    fallback for windows opened by xtm 0.2 or earlier, which did not set
    an instance name. Names that fail the charset check are rejected
    rather than propagated: that can only happen for an unrelated window
    coincidentally named like an xtm one, and quietly ignoring it beats
    writing an unusable session name into the config.
    """
    for candidate, prefix in ((instance, INSTANCE_PREFIX), (title, TITLE_PREFIX)):
        if candidate and candidate.startswith(prefix):
            session = candidate[len(prefix):]
            if is_valid_session_name(session):
                return session
            logger.debug("Ignoring window with an invalid session-like name %r",
                         candidate)
    return None


def find_all_xtm_sessions(tool: str) -> List[str]:
    """Session names of every xtm-managed window currently on screen."""
    found = []  # type: List[str]
    if tool == "xdotool":
        # One search per identifier, merged: --classname finds windows
        # from 0.3 onward, --name also finds ones opened by older
        # versions that only set a title.
        seen_ids = []  # type: List[str]
        for flag, pattern in (("--classname", "^" + re.escape(INSTANCE_PREFIX)),
                              ("--name", "^" + re.escape(TITLE_PREFIX))):
            res = run(["xdotool", "search", flag, pattern])
            if res is None or res.returncode != 0:
                continue
            for win_id in res.stdout.split():
                if win_id not in seen_ids:
                    seen_ids.append(win_id)
        for win_id in seen_ids:
            title = _xdotool_window_name(win_id)
            instance = _xdotool_window_classname(win_id)
            session = _session_from_identifiers(title, instance)
            if session and session not in found:
                found.append(session)
    elif tool == "wmctrl":
        # -lx adds the WM_CLASS column: "id desktop instance.Class host title".
        res = run(["wmctrl", "-lx"])
        if res is not None and res.returncode == 0:
            for line in res.stdout.splitlines():
                parts = line.split(None, 4)
                if len(parts) < 5:
                    continue
                instance = parts[2].split(".")[0]
                session = _session_from_identifiers(parts[4].strip(), instance)
                if session and session not in found:
                    found.append(session)
    elif tool == "xwininfo":
        for _win_id, title, instance in _xwininfo_tree_entries():
            session = _session_from_identifiers(title, instance)
            if session and session not in found:
                found.append(session)
    logger.debug("Found %d xtm window(s) via %s: %s",
                 len(found), tool, ", ".join(found) or "(none)")
    return found


def _xdotool_window_name(win_id: str) -> str:
    """Window title for an xdotool window id, or an empty string."""
    res = run(["xdotool", "getwindowname", win_id])
    if res is None or res.returncode != 0:
        return ""
    return res.stdout.strip()


def _xdotool_window_classname(win_id: str) -> str:
    """WM_CLASS instance name for an xdotool window id.

    xdotool has no direct "print the classname" verb, so this reads the
    property with xprop when available. An empty result simply means the
    title has to be used instead.
    """
    if not which_or_none("xprop"):
        return ""
    res = run(["xprop", "-id", win_id, "WM_CLASS"], required=False)
    if res is None or res.returncode != 0:
        return ""
    m = re.search(r'WM_CLASS\(STRING\)\s*=\s*"([^"]*)"', res.stdout)
    return m.group(1) if m else ""


def _xwininfo_tree_entries() -> List[Tuple[str, str, str]]:
    """(window id, title, instance) for every child window of the root.

    Deliberately does not try to read geometry out of the tree dump:
    under a reparenting window manager (effectively all modern ones) the
    coordinates `-tree` prints for a child are sometimes relative to an
    intermediate frame rather than to the screen, and that ambiguity is
    not reliably resolvable from the tree output across xwininfo versions
    and window managers. Geometry is read per-window with `xwininfo -id`
    instead, which reports unambiguous absolute screen coordinates.
    """
    res = run(["xwininfo", "-root", "-tree"])
    if res is None or res.returncode != 0:
        return []
    entries = []
    for line in res.stdout.splitlines():
        m = _XWININFO_TREE_CHILD_RE.match(line)
        if m:
            entries.append((m.group(1), m.group(2), m.group(3) or ""))
    return entries


def find_window_id(session: str, tool: str) -> Optional[str]:
    """X window id for a session's window, or None if it is not on screen."""
    instance = window_instance(session)
    title = window_title(session)
    if tool == "xdotool":
        for flag, pattern in (("--classname", "^" + re.escape(instance) + "$"),
                              ("--name", "^" + re.escape(title) + "$")):
            res = run(["xdotool", "search", flag, pattern])
            if res is not None and res.returncode == 0 and res.stdout.split():
                return res.stdout.split()[0]
        return None
    if tool == "wmctrl":
        res = run(["wmctrl", "-lx"])
        if res is None or res.returncode != 0:
            return None
        for line in res.stdout.splitlines():
            parts = line.split(None, 4)
            if len(parts) < 5:
                continue
            if parts[2].split(".")[0] == instance or parts[4].strip() == title:
                return parts[0]
        return None
    if tool == "xwininfo":
        for win_id, win_title, win_instance in _xwininfo_tree_entries():
            if win_instance == instance or win_title == title:
                return win_id
        return None
    return None


def get_frame_extents(win_id: str) -> Optional[Tuple[int, int, int, int]]:
    """Window manager decoration thickness as (left, right, top, bottom).

    Read from the _NET_FRAME_EXTENTS property with xprop. Returns None
    when xprop is missing or the window manager does not publish the
    property, in which case the caller proceeds without compensation.
    """
    if not which_or_none("xprop"):
        return None
    res = run(["xprop", "-id", win_id, "_NET_FRAME_EXTENTS"], required=False)
    if res is None or res.returncode != 0:
        return None
    m = re.search(r"_NET_FRAME_EXTENTS\(CARDINAL\)\s*=\s*"
                  r"(\d+),\s*(\d+),\s*(\d+),\s*(\d+)", res.stdout)
    if not m:
        return None
    extents = (int(m.group(1)), int(m.group(2)), int(m.group(3)), int(m.group(4)))
    logger.debug("Frame extents for window %s: left=%d right=%d top=%d bottom=%d",
                 win_id, *extents)
    return extents


def get_window_geometry(session: str, tool: str,
                        compensate: bool = True) -> Optional[GeometryDict]:
    """Current pixel geometry of a session's window, or None.

    Frame compensation is the reason this is not a plain read. The move
    escape sequence is a request the window manager applies to the window
    FRAME (the default NorthWest gravity), while every query tool reports
    the position of the CLIENT area inside that frame. Storing the raw
    client position and replaying it as a frame position therefore shifts
    every window down and right by the title bar and border thickness on
    each capture/restore cycle -- a drift that compounds every time a
    profile is updated. Subtracting the frame extents here stores the
    value that placement will actually reproduce.

    Sizes need no such correction: the resize sequence sets the client
    area, which is also what the query tools report.
    """
    geometry = _read_raw_geometry(session, tool)
    if geometry is None:
        return None
    if compensate and CONTEXT.frame_compensation:
        win_id = find_window_id(session, tool)
        extents = get_frame_extents(win_id) if win_id else None
        if extents:
            left, _right, top, _bottom = extents
            geometry["x"] = int(geometry["x"]) - left
            geometry["y"] = int(geometry["y"]) - top
            logger.debug("Applied frame compensation to %r: -%d horizontally, "
                         "-%d vertically", session, left, top)
        else:
            logger.debug("No frame extents available for %r; storing the raw "
                         "client position.", session)
    return geometry


def _read_raw_geometry(session: str, tool: str) -> Optional[GeometryDict]:
    """Uncompensated client-area geometry straight from the query tool."""
    win_id = find_window_id(session, tool)
    if not win_id:
        return None
    if tool == "xdotool":
        geo = run(["xdotool", "getwindowgeometry", "--shell", win_id])
        if geo is None or geo.returncode != 0:
            return None
        vals = dict(line.split("=", 1) for line in geo.stdout.splitlines()
                    if "=" in line)
        try:
            return {"x": int(vals["X"]), "y": int(vals["Y"]),
                    "width": int(vals["WIDTH"]), "height": int(vals["HEIGHT"])}
        except (KeyError, ValueError):
            logger.debug("Could not parse xdotool geometry for %r", session)
            return None
    if tool == "wmctrl":
        # -lG columns: id desktop x y width height host title
        res = run(["wmctrl", "-lG"])
        if res is None or res.returncode != 0:
            return None
        for line in res.stdout.splitlines():
            parts = line.split(None, 7)
            if len(parts) >= 6 and parts[0] == win_id:
                try:
                    return {"x": int(parts[2]), "y": int(parts[3]),
                            "width": int(parts[4]), "height": int(parts[5])}
                except ValueError:
                    return None
        return None
    if tool == "xwininfo":
        res = run(["xwininfo", "-id", win_id])
        if res is None or res.returncode != 0:
            return None
        geometry = {}  # type: GeometryDict
        for key, pattern in (("x", r"Absolute upper-left X:\s+(-?\d+)"),
                             ("y", r"Absolute upper-left Y:\s+(-?\d+)"),
                             ("width", r"Width:\s+(\d+)"),
                             ("height", r"Height:\s+(\d+)")):
            m = re.search(pattern, res.stdout)
            if not m:
                return None
            geometry[key] = int(m.group(1))
        return geometry
    return None


def focus_window(session: str, tool: str) -> bool:
    """Raise and focus a session's window. Returns False if it failed."""
    win_id = find_window_id(session, tool)
    if not win_id:
        return False
    if CONTEXT.dry_run:
        logger.info("[dry-run] would focus window %s for session %r", win_id, session)
        return True
    if tool == "xdotool":
        res = run(["xdotool", "windowactivate", win_id])
        return res is not None and res.returncode == 0
    if tool == "wmctrl":
        res = run(["wmctrl", "-i", "-a", win_id])
        return res is not None and res.returncode == 0
    die("Focusing a window needs xdotool or wmctrl; only xwininfo was found, "
        "and it can query windows but not act on them.")
    return False


# --------------------------------------------------------------------------
# The screen: which monitors are connected, and whether a window fits
# --------------------------------------------------------------------------
#
# A profile is absolute pixels, so one written for a desk with two monitors
# puts half its windows off screen on the same laptop undocked -- silently,
# since the window manager accepts the position. Checking each window
# against the monitors actually connected turns that into a warning naming
# the window and the screen, and --clamp into a window you can see.

# One monitor in `xrandr --listmonitors`:
#    0: +*eDP-1 1440/290x900/180+0+0  eDP-1
_XRANDR_MONITOR_RE = re.compile(
    r"^\s*\d+:\s+\S+\s+(\d+)/\d+x(\d+)/\d+([+-]\d+)([+-]\d+)\s+(\S+)", re.M)

ON_SCREEN = "on screen"
PARTLY_OFF_SCREEN = "partly off screen"
OFF_SCREEN = "off screen"


def screen_monitors() -> Optional[List[GeometryDict]]:
    """The connected monitors as geometries with a `name`, or None.

    `xrandr --listmonitors` first, since it knows each monitor and the gaps
    between them; the root window's size from `xwininfo -root` otherwise,
    which is one rectangle covering them all. None when neither answers --
    no display, or neither tool -- and then nothing is checked at all.
    """
    if not os.environ.get("DISPLAY"):
        return None
    res = run(["xrandr", "--listmonitors"], required=False)
    if res is not None and res.returncode == 0:
        found = [{"name": m.group(5), "x": int(m.group(3)), "y": int(m.group(4)),
                  "width": int(m.group(1)), "height": int(m.group(2))}
                 for m in _XRANDR_MONITOR_RE.finditer(res.stdout)]
        if found:
            return found
    res = run(["xwininfo", "-root"], required=False)
    if res is not None and res.returncode == 0:
        width = re.search(r"Width:\s+(\d+)", res.stdout)
        height = re.search(r"Height:\s+(\d+)", res.stdout)
        if width and height:
            return [{"name": "screen", "x": 0, "y": 0,
                     "width": int(width.group(1)), "height": int(height.group(1))}]
    logger.debug("Could not read the monitor layout; windows are not checked "
                 "against it.")
    return None


def _overlap(a: GeometryDict, b: GeometryDict) -> int:
    """Area, in pixels, shared by two rectangles."""
    w = min(a["x"] + a["width"], b["x"] + b["width"]) - max(a["x"], b["x"])
    h = min(a["y"] + a["height"], b["y"] + b["height"]) - max(a["y"], b["y"])
    return max(w, 0) * max(h, 0)


def screen_fit(geometry: GeometryDict, monitors: List[GeometryDict]) -> str:
    """Whether a window at `geometry` can be seen and grabbed.

    OFF_SCREEN when it touches no monitor. PARTLY_OFF_SCREEN when it touches
    one but its top-left corner is on none: the title bar is then out of
    reach, so the window cannot be dragged back by hand either.
    """
    if not any(_overlap(geometry, m) for m in monitors):
        return OFF_SCREEN
    corner = {"x": geometry["x"], "y": geometry["y"], "width": 1, "height": 1}
    if not any(_overlap(corner, m) for m in monitors):
        return PARTLY_OFF_SCREEN
    return ON_SCREEN


def clamp_to_screen(geometry: GeometryDict, monitors: List[GeometryDict]
                    ) -> GeometryDict:
    """`geometry` moved onto the monitor it overlaps most, or the first.

    Shrunk to that monitor when it is larger than it; otherwise the size is
    kept and only the position moves.
    """
    target = max(monitors, key=lambda m: _overlap(geometry, m))
    if not _overlap(geometry, target):
        target = monitors[0]
    width = min(int(geometry["width"]), target["width"])
    height = min(int(geometry["height"]), target["height"])
    x = min(max(int(geometry["x"]), target["x"]),
            target["x"] + target["width"] - width)
    y = min(max(int(geometry["y"]), target["y"]),
            target["y"] + target["height"] - height)
    return {"x": x, "y": y, "width": width, "height": height}


def _describe_monitors(monitors: List[GeometryDict]) -> str:
    return ", ".join("%s %sx%s%+d%+d" % (m["name"], m["width"], m["height"],
                                         m["x"], m["y"]) for m in monitors)


def fit_to_screen(session: str, geometry: GeometryDict,
                  monitors: Optional[List[GeometryDict]]) -> GeometryDict:
    """The geometry to place `session` at: as given, or clamped.

    A window that would land off screen is reported either way; with
    --clamp it is also moved onto a monitor. Unknown monitors check nothing.
    """
    if not monitors:
        return geometry
    fit = screen_fit(geometry, monitors)
    if fit == ON_SCREEN:
        return geometry
    where = "x=%s y=%s %sx%s" % (geometry["x"], geometry["y"],
                                 geometry["width"], geometry["height"])
    if CONTEXT.clamp:
        clamped = clamp_to_screen(geometry, monitors)
        logger.warning("%s: %s is %s on this screen (%s); placing it at "
                       "x=%s y=%s %sx%s instead.", session, where, fit,
                       _describe_monitors(monitors), clamped["x"], clamped["y"],
                       clamped["width"], clamped["height"])
        return clamped
    logger.warning("%s: %s is %s on this screen (%s). Use --clamp to bring "
                   "it onto a monitor, or switch to a profile for this screen "
                   "with 'xtm NAME'.", session, where, fit,
                   _describe_monitors(monitors))
    return geometry


# --------------------------------------------------------------------------
# Stacking (default placement for sessions with no configured position)
# --------------------------------------------------------------------------

def stack_slot(stack_cfg: GeometryDict, index: int) -> GeometryDict:
    """Diagonally offset position and size for the Nth (0-based) window
    that has no configured position. All stacked windows share one size.

    There is no wraparound: with enough simultaneous stray sessions the
    diagonal offsets push a window off screen. Placement warns about that
    when the monitors are known, and --clamp moves it back; lower
    offset_x/offset_y, or give the sessions positions, to avoid it.
    """
    return {
        "x": stack_cfg["x"] + index * stack_cfg["offset_x"],
        "y": stack_cfg["y"] + index * stack_cfg["offset_y"],
        "width": stack_cfg["width"],
        "height": stack_cfg["height"],
    }


# --------------------------------------------------------------------------
# Opening sessions
# --------------------------------------------------------------------------

def tmux_version() -> Optional[Tuple[int, int]]:
    """tmux's (major, minor) version, or None when it cannot be told."""
    res = run(["tmux", "-V"], required=False)
    if res is None or res.returncode != 0:
        return None
    match = re.search(r"(\d+)\.(\d+)", res.stdout)
    return (int(match.group(1)), int(match.group(2))) if match else None


# `remain-on-exit failed`, which keeps a pane only when its program exits
# non-zero, arrived in tmux 3.2.
HOLD_FAILED_PANES_SINCE = (3, 2)


def build_xterm_command(session: str, sess_cfg: Optional[GeometryDict],
                        title: Optional[str] = None,
                        hold_failures: bool = False) -> List[str]:
    """Assemble the xterm argument vector for one session.

    Every window gets allowWindowOps (required for placement to work at
    all), a title, and an instance name for robust identification. Any
    per-session xterm_args are inserted before -e, so a profile can set
    fonts or colors -- color-coding a production cluster differently
    from a development one, for example.

    The title is cosmetic and may be overridden by the profile; the
    instance name is the identity and is always derived from the session
    name, so a custom title can never break window discovery.

    The tmux side uses `new-session -A`, which attaches to the session
    when it already exists and creates it otherwise. Note that -c and the
    startup command apply only when the session is actually created;
    tmux ignores them when attaching to an existing session.

    With `hold_failures`, the tmux command also sets `remain-on-exit
    failed` on the pane. A slot's command replaces the pane's shell, so
    when it failed -- `ssh headnode` before the VPN is up, a typo -- the
    pane, the session and the window all vanished, and in the morning a
    window was simply missing. Held, the dead pane stays on screen with
    the exit status, and `tmux respawn-pane` runs the command again. A
    command that succeeds still closes its pane, as before.
    """
    sess_cfg = sess_cfg or {}
    argv = [
        "xterm",
        "-xrm", "XTerm*allowWindowOps: true",
        "-name", window_instance(session),
        "-T", title if title is not None else window_title(session),
    ]
    for arg in sess_cfg.get("xterm_args") or []:
        argv.append(str(arg))

    tmux_argv = ["tmux", "new-session", "-A", "-s", session]
    cwd = sess_cfg.get("cwd")
    if cwd:
        tmux_argv += ["-c", os.path.expanduser(str(cwd))]
    command = sess_cfg.get("command")
    if command:
        # Passed as a single argument: tmux hands it to the shell, so
        # pipelines and && work as written in the config.
        tmux_argv.append(str(command))
        if hold_failures:
            tmux_argv += [";", "set-option", "-p", "remain-on-exit", "failed"]

    return argv + ["-e"] + tmux_argv


def spawn_xterm(session: str, sess_cfg: Optional[GeometryDict] = None,
                title: Optional[str] = None) -> Optional[subprocess.Popen]:
    """Launch a new xterm attached to `session` and return the process.

    Does not wait for the tmux client to attach; see wait_for_own_client,
    which uses the process to tell this xterm's client from any other.

    Uses Popen directly rather than run(): this starts the user's real
    interactive terminal, so unlike xtm's information-gathering calls it
    must inherit the user's environment and locale untouched.
    """
    if not os.environ.get("DISPLAY"):
        die("$DISPLAY is not set, so xterm has nowhere to open a window. Make "
            "sure this shell has a working X display (an X-forwarded or local "
            "session rather than a plain non-X SSH login).")
    hold = bool((sess_cfg or {}).get("command"))
    if hold:
        version = tmux_version()
        hold = version is not None and version >= HOLD_FAILED_PANES_SINCE
    argv = build_xterm_command(session, sess_cfg, title, hold_failures=hold)
    logger.debug("Spawning: %s", " ".join(argv))
    if CONTEXT.dry_run:
        logger.info("[dry-run] would launch: %s", " ".join(argv))
        return None
    try:
        proc = subprocess.Popen(argv, start_new_session=True)
    except FileNotFoundError:
        return die("Required program 'xterm' was not found on PATH.")
    except OSError as e:
        return die("Could not launch xterm: %s" % (e,))
    logger.debug("xterm for session %r started with pid=%d", session, proc.pid)
    return proc


SETUP_TIMEOUT_SECONDS = 60.0


def run_setup(session: str, sess_cfg: GeometryDict, slot: str,
              profile_name: Optional[str]) -> None:
    """Run a slot's `setup` once, just after xtm created its session.

    `setup` is a shell command -- a script path, or a line of tmux commands
    -- for what `command` cannot do: extra tmux windows, splits, a layout.
    It runs with XTM_SESSION, XTM_SLOT and XTM_PROFILE set, in the slot's
    cwd, with the user's own environment. Only on creation: a session that
    was already running has whatever its setup built, and running it again
    would build it twice.

    A failure is a warning, not an error: the window is already open and
    placed, and the session is usable without whatever setup would have
    added.
    """
    setup = sess_cfg.get("setup")
    if not setup:
        return
    if CONTEXT.dry_run:
        logger.info("[dry-run] would run setup for %s: %s", session, setup)
        return
    cwd = os.path.expanduser(str(sess_cfg.get("cwd") or "~"))
    if not os.path.isdir(cwd):
        cwd = os.path.expanduser("~")
    env = dict(os.environ)
    env.update({"XTM_SESSION": session, "XTM_SLOT": slot,
                "XTM_PROFILE": profile_name or ""})
    logger.debug("Running setup for %s in %s: %s", session, cwd, setup)
    # stderr goes to a file, not a pipe: a process setup starts in the
    # background inherits it, and a pipe would keep xtm waiting for EOF
    # until that process exited rather than until setup itself did.
    with tempfile.TemporaryFile("w+") as errors:
        try:
            result = subprocess.run(["/bin/sh", "-c", str(setup)], cwd=cwd,
                                    env=env, stdout=subprocess.DEVNULL,
                                    stderr=errors, timeout=SETUP_TIMEOUT_SECONDS)
        except subprocess.TimeoutExpired:
            logger.warning("%s: setup did not finish within %ds and was stopped.",
                           session, SETUP_TIMEOUT_SECONDS)
            return
        except OSError as e:
            logger.warning("%s: could not run setup: %s", session, e)
            return
        if result.returncode != 0:
            errors.seek(0)
            logger.warning("%s: setup exited %d: %s", session, result.returncode,
                           _truncate(errors.read()) or "(no output)")
            return
    logger.info("Ran setup for %s.", session)


def open_session(session: str, geometry: GeometryDict,
                 sess_cfg: Optional[GeometryDict] = None,
                 title: Optional[str] = None, slot: Optional[str] = None,
                 profile_name: Optional[str] = None) -> None:
    """Open one session in a new xterm and place it.

    Errors if the session already has an xtm window. A session attached
    only elsewhere -- an SSH login, another terminal -- still gets one:
    `tmux new-session -A` attaches a second client beside the first. When
    the session did not exist and this created it, the slot's `setup` runs.
    """
    validate_session_name(session)
    if xtm_windows().get(session):
        die("tmux session %r already has an xtm window." % (session,))
    creating = session not in tmux_list_sessions()
    proc = spawn_xterm(session, sess_cfg, title)
    if CONTEXT.dry_run:
        logger.info("[dry-run] would place %r at x=%s y=%s %sx%s", session,
                    geometry["x"], geometry["y"], geometry["width"],
                    geometry["height"])
        if creating and sess_cfg:
            run_setup(session, sess_cfg, slot or session, profile_name)
        return
    tty = wait_for_own_client(session, proc)
    if not tty:
        die("Timed out waiting for tmux session %r to attach. Check that xterm "
            "is installed and DISPLAY is set correctly." % (session,))
    place_window(session, geometry, tty)
    logger.info("Opened %s at x=%s y=%s %sx%s", session, geometry["x"],
                geometry["y"], geometry["width"], geometry["height"])
    if creating and sess_cfg:
        run_setup(session, sess_cfg, slot or session, profile_name)


def reposition_or_open(session: str, geometry: GeometryDict,
                       sess_cfg: Optional[GeometryDict] = None,
                       window_tty: Optional[str] = None,
                       title: Optional[str] = None, slot: Optional[str] = None,
                       profile_name: Optional[str] = None) -> None:
    """Move a session's xtm window, or open one if it has none.

    `window_tty` is the tty of the session's xtm client, from an
    xtm_windows() snapshot the caller already took for the whole profile.
    None means the session has no xtm window -- whether it is not running,
    detached, or attached only in someone else's terminal -- so one is
    opened.
    """
    validate_session_name(session)
    if window_tty:
        if CONTEXT.dry_run:
            logger.info("[dry-run] would reposition %s to x=%s y=%s %sx%s",
                        session, geometry["x"], geometry["y"],
                        geometry["width"], geometry["height"])
            return
        place_window(session, geometry, window_tty)
        logger.info("Repositioned %s to x=%s y=%s %sx%s", session, geometry["x"],
                    geometry["y"], geometry["width"], geometry["height"])
    else:
        open_session(session, geometry, sess_cfg, title, slot, profile_name)


def count_unnamed_open(profile: ProfileDict,
                       windows: Optional[Dict[str, List[str]]] = None) -> int:
    """How many xtm windows show a session this profile does not name.

    Used to choose the next stack slot for --open on an unconfigured
    session. This is deliberately a different ordering strategy from
    --reset-all, which re-stacks every stray in alphabetical order for a
    fully deterministic layout; --open simply appends one more window to
    however many are already out there. Both are self-consistent, they
    just serve different purposes.
    """
    named = set(profile_sessions(profile))
    windows = xtm_windows() if windows is None else windows
    return sum(1 for name in windows if name not in named)


# --------------------------------------------------------------------------
# Commands: window actions
# --------------------------------------------------------------------------

def cmd_open(args: argparse.Namespace, config: ConfigDict, profile_name: str) -> int:
    """Open one session, at its configured position or the next stack slot."""
    profile = validate_profile(config, profile_name)
    name = validate_session_name(args.open)
    # The argument may name a slot in the profile or a session directly,
    # so that both `xtm --open work1` (the slot) and `xtm --open
    # desk-work1` (what tmux calls it) do the obvious thing.
    slot_cfg = profile["sessions"].get(name)
    if slot_cfg is not None:
        slot_name = name
    else:
        slot_name = slot_for_session(profile, name)
        slot_cfg = profile["sessions"].get(slot_name) if slot_name else None
    if slot_cfg is not None:
        session = resolve_session_name(profile, slot_name)
        geometry = session_geometry(slot_cfg)
        title = resolve_window_title(profile, profile_name, slot_name, session)
    else:
        session = name
        index = count_unnamed_open(profile)
        geometry = stack_slot(profile["stack"], index)
        title = None
        logger.info("%s has no configured position in profile %s; using stacked "
                    "slot #%d.", session, profile_name, index)
    open_session(session, fit_to_screen(session, geometry, screen_monitors()),
                 slot_cfg, title, slot_name, profile_name)
    return EXIT_OK


def cmd_reset(args: argparse.Namespace, config: ConfigDict, profile_name: str) -> int:
    """Reposition, or open, every named session in the profile."""
    profile = validate_profile(config, profile_name)
    if not profile["sessions"]:
        logger.info("Profile %s has no slots configured.", profile_name)
        return EXIT_OK
    windows = xtm_windows()
    monitors = screen_monitors()
    failures = []
    for slot, session in profile_slots(profile):
        slot_cfg = profile["sessions"][slot]
        try:
            reposition_or_open(
                session,
                fit_to_screen(session, session_geometry(slot_cfg), monitors),
                slot_cfg,
                window_tty=(windows.get(session) or [None])[0],
                title=resolve_window_title(profile, profile_name, slot, session),
                slot=slot, profile_name=profile_name)
        except XtmError as e:
            logger.warning("%s: %s", session, e)
            failures.append(session)
    if failures:
        logger.error("Failed to place %d session(s): %s",
                     len(failures), ", ".join(failures))
        return EXIT_ERROR
    return EXIT_OK


def cmd_reset_all(args: argparse.Namespace, config: ConfigDict,
                  profile_name: str) -> int:
    """Reset named sessions, then stack every stray xtm window as well.

    A stray is an xtm window showing a session the profile does not name.
    Terminals xtm did not open are never strays, even when they are
    attached to a tmux session: they are not xtm's to move.
    """
    profile = validate_profile(config, profile_name)
    status = cmd_reset(args, config, profile_name)

    named = set(profile_sessions(profile))
    windows = xtm_windows()
    strays = sorted(name for name in windows if name not in named)
    monitors = screen_monitors() if strays else None
    failures = []
    for index, session in enumerate(strays):
        geometry = fit_to_screen(session, stack_slot(profile["stack"], index),
                                 monitors)
        try:
            place_window(session, geometry, windows[session][0])
            logger.info("Stacked stray session %s at slot #%d (x=%s y=%s)",
                        session, index, geometry["x"], geometry["y"])
        except XtmError as e:
            logger.warning("%s: %s", session, e)
            failures.append(session)
    if not strays:
        logger.info("No stray (unnamed) sessions found.")
    if failures:
        logger.error("Failed to stack %d stray session(s): %s",
                     len(failures), ", ".join(failures))
        return EXIT_ERROR
    return status


def _resolve_target_session(profile: ProfileDict, name: str) -> str:
    """Accept either a slot name or a session name from the command line.

    A user thinks in terms of the names written in their profile, but
    tmux knows the prefixed name. Accepting both means neither has to be
    memorised.
    """
    if name in profile.get("sessions", {}):
        return resolve_session_name(profile, name)
    return name


def cmd_close(args: argparse.Namespace, config: ConfigDict, profile_name: str) -> int:
    """Kill one tmux session, which closes its window with it."""
    profile = validate_profile(config, profile_name)
    session = _resolve_target_session(profile,
                                      validate_session_name(args.close))
    live = tmux_list_sessions()
    if session not in live:
        logger.error("No tmux session named %r is running.", session)
        return EXIT_ERROR
    if not tmux_kill_session(session):
        logger.error("Could not kill tmux session %r.", session)
        return EXIT_ERROR
    logger.info("Closed %s", session)
    return EXIT_OK


def cmd_detach(args: argparse.Namespace, config: ConfigDict,
               profile_name: str) -> int:
    """Close one session's window while leaving the session running.

    The distinction from --close matters: detaching tidies the screen and
    keeps every tab, pane and running job alive, while closing destroys
    them. Detaching is the reversible one.
    """
    profile = validate_profile(config, profile_name)
    session = _resolve_target_session(profile,
                                      validate_session_name(args.detach))
    live = tmux_list_sessions()
    if session not in live:
        logger.error("No tmux session named %r is running.", session)
        return EXIT_ERROR
    windows = xtm_windows()
    if not windows.get(session):
        logger.info("Session %s has no xtm window%s.", session,
                    " (it is attached elsewhere, which xtm leaves alone)"
                    if live[session] else "")
        return EXIT_OK
    if not tmux_detach_session(session, windows):
        logger.error("Could not detach tmux session %r.", session)
        return EXIT_ERROR
    logger.info("Detached %s; the session is still running.", session)
    return EXIT_OK


def cmd_close_all(args: argparse.Namespace, config: ConfigDict,
                  profile_name: str) -> int:
    """Kill every running session named in the profile.

    Scoped to the profile on purpose: strays are left alone because they
    are, by definition, sessions xtm was never told to manage, and some
    of them may be long-running work started elsewhere.
    """
    profile = validate_profile(config, profile_name)
    live = tmux_list_sessions()
    if args.all:
        # Every session any visible profile lays claim to, so that
        # sessions left behind by an earlier profile can be cleaned up
        # without switching back to it first.
        wanted = set()
        for name in visible_profile_names(config, include_all=False):
            candidate = config["profiles"][name]
            if isinstance(candidate, dict):
                wanted.update(profile_sessions(candidate))
        scope = "every profile"
    else:
        wanted = set(profile_sessions(profile))
        scope = "profile %s" % (profile_name,)
    windows = xtm_windows() if args.detach_mode else {}
    # Detaching closes xtm's windows, so only sessions that have one are
    # targets; closing kills the session, so any running one is.
    targets = sorted(s for s in wanted
                     if (s in windows if args.detach_mode else s in live))
    verb = "Detach" if args.detach_mode else "Close"
    if not targets:
        logger.info("No sessions from %s are running.", scope)
        return EXIT_OK
    if not confirm("%s %d session(s) from %s (%s)?"
                   % (verb, len(targets), scope, ", ".join(targets)), args.yes):
        logger.info("Canceled.")
        return EXIT_OK
    if args.detach_mode:
        failures = [s for s in targets if not tmux_detach_session(s, windows)]
    else:
        failures = [s for s in targets if not tmux_kill_session(s)]
    for session in targets:
        if session not in failures:
            logger.info("%s %s", "Detached" if args.detach_mode else "Closed",
                        session)
    if failures:
        logger.error("Could not %s: %s", verb.lower(), ", ".join(failures))
        return EXIT_ERROR
    return EXIT_OK


def cmd_focus(args: argparse.Namespace, config: ConfigDict, profile_name: str) -> int:
    """Raise and focus one session's window.

    Takes a slot name or a session name, as --open, --close, --detach and
    --delete-session all do. It used to take only the session name, which
    made it the one exception, and made the "did you mean 'xtm --focus
    NAME'" hint in _unknown_profile_message wrong for any profile with a
    prefix: the hint names a SLOT, and the window is named after the
    session the slot resolves to.
    """
    profile = validate_profile(config, profile_name)
    session = _resolve_target_session(profile,
                                      validate_session_name(args.focus))
    tool = require_acting_geometry_tool()
    if not focus_window(session, tool):
        logger.error("Could not find or focus a window for session %r.", session)
        return EXIT_ERROR
    logger.info("Focused %s", session)
    return EXIT_OK


def confirm(question: str, assume_yes: bool) -> bool:
    """Ask for confirmation before a destructive action.

    Non-interactive runs (a cron job, a pipeline) must not block on a
    prompt that nobody can answer, so a missing tty is treated the same
    as --yes; anything scripted has already opted in by invoking a
    destructive command with no terminal attached.
    """
    if assume_yes or CONTEXT.dry_run:
        return True
    if not sys.stdin.isatty():
        logger.debug("stdin is not a terminal; proceeding without confirmation.")
        return True
    try:
        answer = input("%s [y/N] " % (question,))
    except EOFError:
        return False
    return answer.strip().lower() in ("y", "yes")


# --------------------------------------------------------------------------
# Commands: profile capture and editing
# --------------------------------------------------------------------------

def capture_layout(tool: str) -> Dict[str, GeometryDict]:
    """Read back the geometry of every xtm-managed window on screen."""
    layout = {}  # type: Dict[str, GeometryDict]
    for session in find_all_xtm_sessions(tool):
        geometry = get_window_geometry(session, tool)
        if geometry is None:
            logger.warning("Skipping %s: could not read its geometry.", session)
            continue
        layout[session] = geometry
    return layout


def cmd_update_profile(args: argparse.Namespace, config: ConfigDict,
                       profile_name: str) -> int:
    """Save the current on-screen layout into the current profile."""
    profile = validate_profile(config, profile_name)
    tool = require_geometry_tool()
    layout = capture_layout(tool)
    if not layout:
        logger.info("No xtm-managed windows are currently open; nothing to update.")
        return EXIT_OK
    updated = added = skipped = 0
    # Built once, not per window: this loop runs over every window on screen,
    # and resolving each one with slot_for_session() would rescan every slot
    # in the profile each time.
    slot_index = session_slot_index(profile)
    # Guarded for the same reason --set is: --capture-new adds slots, and a
    # captured window whose name collides with an existing slot's resolved
    # session would otherwise be written to a config that no later command
    # can load.
    with edit_profile(config, profile_name, "Capturing the current layout"):
        for session, geometry in layout.items():
            slot = slot_index.get(session)
            existing = profile["sessions"].get(slot) if slot else None
            if isinstance(existing, dict):
                # Preserve command/cwd/xterm_args/session/title: only
                # placement is captured from the screen, and silently
                # dropping the rest would be a destructive surprise.
                existing.update(geometry)
                updated += 1
            elif args.capture_new:
                # An unrecognized window is recorded under its own name with
                # an explicit 'session', so the entry means exactly the
                # session that was captured regardless of any prefix. A slot
                # of that name may already exist for another session (slot
                # work1 is session desk-work1 under prefix desk-); it is kept,
                # and the capture takes the first free name after it.
                key = session
                suffix = 1
                while key in profile["sessions"]:
                    suffix += 1
                    key = "%s-%d" % (session, suffix)
                if key != session:
                    logger.warning("Slot %s already names session %s; captured "
                                   "session %s recorded as slot %s.", session,
                                   resolve_session_name(profile, session),
                                   session, key)
                entry = dict(geometry)
                if session != "%s%s" % (profile.get("prefix", ""), key):
                    entry["session"] = session
                profile["sessions"][key] = entry
                added += 1
            else:
                # Windows belonging to another profile are left out by
                # default: capturing them would quietly graft one profile's
                # sessions onto another.
                skipped += 1
                logger.debug("Skipping %s: not part of profile %s.",
                             session, profile_name)
                continue
            logger.debug("Captured %s at x=%s y=%s %sx%s", session,
                         geometry["x"], geometry["y"], geometry["width"],
                         geometry["height"])
    save_config(config)
    logger.info("Profile %s updated: %d slot(s) updated, %d added. Saved to %s",
                profile_name, updated, added, CONTEXT.config_file)
    if skipped:
        logger.info("%d open window(s) are not part of this profile and were "
                    "left out; use --capture-new to add them.", skipped)
    return EXIT_OK


def cmd_new_profile(args: argparse.Namespace, config: ConfigDict,
                    profile_name: str) -> int:
    """Create a new profile from the current on-screen layout."""
    new_name = validate_profile_name(args.new_profile)
    if new_name in config.get("profiles", {}):
        die("Profile %r already exists." % (new_name,))
    tool = require_geometry_tool()
    layout = capture_layout(tool)
    if not layout:
        logger.warning("No xtm-managed windows are open, so profile %r will be "
                       "created with no sessions.", new_name)

    # Base the new profile's stack defaults on the profile in use, so a
    # derived profile starts from a familiar default window size.
    base_stack = dict(DEFAULT_STACK)
    if profile_name in config.get("profiles", {}):
        existing = config["profiles"][profile_name]
        if isinstance(existing, dict) and isinstance(existing.get("stack"), dict):
            base_stack = dict(existing["stack"])

    # New profiles own their sessions by default, so the prefix is written
    # out explicitly rather than being implied. Captured windows keep an
    # explicit 'session', because those sessions already exist under the
    # names they were captured with and must not be renamed by the prefix.
    prefix = "%s-" % (new_name,)
    slots = {}
    for session, geometry in layout.items():
        entry = dict(geometry)
        entry["session"] = session
        slots[session] = entry

    config["profiles"][new_name] = {
        "prefix": prefix,
        "stack": base_stack,
        "sessions": slots,
    }
    save_config(config)
    logger.info("Created profile %s with %d slot(s) captured from the current "
                "layout, and prefix %r for new slots. Saved to %s",
                new_name, len(slots), prefix, CONTEXT.config_file)
    return EXIT_OK


def cmd_set(args: argparse.Namespace, config: ConfigDict, profile_name: str) -> int:
    """Add or update one slot's position in the profile."""
    profile = validate_profile(config, profile_name)
    slot = validate_session_name(args.set[0])
    geometry = parse_geometry_spec(args.set[1])
    existing = profile["sessions"].get(slot)
    with edit_profile(config, profile_name, "Setting slot %r" % (slot,)):
        if isinstance(existing, dict):
            existing.update(geometry)
            action = "Updated"
        else:
            profile["sessions"][slot] = geometry
            action = "Added"
    save_config(config)
    session = resolve_session_name(profile, slot)
    logger.info("%s slot %s (session %s) in profile %s: x=%s y=%s %sx%s",
                action, slot, session, profile_name, geometry["x"],
                geometry["y"], geometry["width"], geometry["height"])
    return EXIT_OK


def cmd_delete_session(args: argparse.Namespace, config: ConfigDict,
                       profile_name: str) -> int:
    """Remove one session's entry from the profile.

    This edits configuration only. A running session of the same name
    keeps running; use --close to kill it.
    """
    profile = validate_profile(config, profile_name)
    name = args.delete_session
    # Accept a slot name or the session name it resolves to, matching
    # every other command that takes one.
    slot = name if name in profile["sessions"] else slot_for_session(profile, name)
    if not slot or slot not in profile["sessions"]:
        logger.error("Slot %r is not configured in profile %s.",
                     name, profile_name)
        return EXIT_ERROR
    session = resolve_session_name(profile, slot)
    del profile["sessions"][slot]
    save_config(config)
    logger.info("Removed slot %s from profile %s. Session %s keeps running if "
                "it is up; use --close to end it.", slot, profile_name, session)
    return EXIT_OK


def _refuse_reserved(name: str, what: str) -> None:
    """Block an operation that would remove or replace "default".

    Resolution falls back to this profile, and the CLI treats it as the
    baseline layout, so it has to be present under exactly that name.
    Its contents remain fully editable.
    """
    if name == DEFAULT_PROFILE_NAME:
        die("Profile %r is reserved and cannot be %s. Its contents can still "
            "be edited freely." % (name, what))


def cmd_delete_profile(args: argparse.Namespace, config: ConfigDict,
                       profile_name: str) -> int:
    """Delete a whole profile from the config."""
    name = validate_profile_name(args.delete_profile)
    _refuse_reserved(name, "deleted")
    profiles = config.get("profiles", {})
    if name not in profiles:
        logger.error("Profile %r does not exist.", name)
        return EXIT_ERROR
    if not confirm("Delete profile %r and its %d configured session(s)?"
                   % (name, len(profiles[name].get("sessions", {})
                              if isinstance(profiles[name], dict) else {})),
                   args.yes):
        logger.info("Canceled.")
        return EXIT_OK
    del profiles[name]
    save_config(config)
    if read_current_profile_state() == name:
        # Leaving the state file pointing at a deleted profile would make
        # every later command fail until --profile is passed again.
        logger.warning("Profile %s was the current profile; clearing that "
                       "selection.", name)
        clear_current_profile_state()
    logger.info("Deleted profile %s from %s", name, CONTEXT.config_file)
    return EXIT_OK


def follow_rename_in_state_files(old: str, new: str) -> None:
    """Point every machine's state file that names `old` at `new`.

    Every machine, not only this one: a home directory shared between
    machines is what per-machine state files exist for, and a machine left
    naming the old profile would stop on its next run. Deletion stays
    per-machine (cmd_delete_profile): there, another machine stopping to
    ask which profile to use is better than a silent fallback. No lock
    covers the state files; a machine switching at the same moment can
    only write a name of its own choosing.
    """
    for path in sorted(CONTEXT.state_dir.glob("current_profile.*")):
        raw = _read_text_safe(path, "current-profile state file")
        if raw.strip() and raw.splitlines()[0].strip() == old:
            _write_text_safe(path, new + "\n", "current-profile state file")
            logger.debug("Renamed the current profile in %s", path)


def clear_current_profile_state() -> None:
    """Forget the persisted current profile for this machine."""
    if CONTEXT.dry_run:
        logger.info("[dry-run] would clear %s", CONTEXT.state_file)
        return
    try:
        if CONTEXT.state_file.exists():
            CONTEXT.state_file.unlink()
    except OSError as e:
        logger.warning("Could not clear %s: %s", CONTEXT.state_file, e)


def cmd_copy_profile(args: argparse.Namespace, config: ConfigDict,
                     profile_name: str) -> int:
    """Duplicate an existing profile under a new name."""
    source, target = args.copy_profile
    validate_profile_name(source)
    validate_profile_name(target)
    _refuse_reserved(target, "replaced by a copy")
    profiles = config.get("profiles", {})
    if source not in profiles:
        logger.error("Profile %r does not exist.", source)
        return EXIT_ERROR
    if target in profiles:
        logger.error("Profile %r already exists.", target)
        return EXIT_ERROR
    profiles[target] = _deep_copy(profiles[source])
    # A copied match block would make two profiles claim the same machine,
    # so auto-selection is left to be configured deliberately on the copy.
    if isinstance(profiles[target], dict):
        profiles[target].pop("match", None)
    save_config(config)
    logger.info("Copied profile %s to %s", source, target)
    return EXIT_OK


def cmd_rename_profile(args: argparse.Namespace, config: ConfigDict,
                       profile_name: str) -> int:
    """Rename a profile, keeping it selected if it was current."""
    old, new = args.rename_profile
    validate_profile_name(old)
    validate_profile_name(new)
    _refuse_reserved(old, "renamed")
    _refuse_reserved(new, "replaced by a rename")
    profiles = config.get("profiles", {})
    if old not in profiles:
        logger.error("Profile %r does not exist.", old)
        return EXIT_ERROR
    if new in profiles:
        logger.error("Profile %r already exists.", new)
        return EXIT_ERROR
    # Rebuild in order so the renamed profile keeps its position in the
    # file rather than jumping to the end.
    config["profiles"] = dict(
        (new if name == old else name, value) for name, value in profiles.items())
    save_config(config)
    follow_rename_in_state_files(old, new)
    logger.info("Renamed profile %s to %s", old, new)
    return EXIT_OK


def cmd_make_global(args: argparse.Namespace, config: ConfigDict,
                    profile_name: str) -> int:
    """Remove a profile's machine binding, making it usable everywhere.

    Deliberately one-way: there is no command to bind a global profile
    back to a machine. Re-binding would silently hide a profile that
    other machines may already be relying on, so it is left as an
    explicit edit of the config file.
    """
    name = validate_profile_name(args.make_global)
    profiles = config.get("profiles", {})
    if name not in profiles:
        logger.error("Profile %r does not exist.", name)
        return EXIT_ERROR
    profile = profiles[name]
    if not isinstance(profile, dict) or "match" not in profile:
        logger.info("Profile %s is already global.", name)
        return EXIT_OK
    if CONTEXT.dry_run:
        logger.info("[dry-run] would make profile %s global", name)
        return EXIT_OK
    profile.pop("match")
    save_config(config)
    logger.info("Profile %s is now global and available on every machine.", name)
    return EXIT_OK


def _deep_copy(value: Any) -> Any:
    """Recursive copy of the plain data a config is made of.

    Written out rather than using copy.deepcopy to keep the import list
    minimal and because config values are only ever dicts, lists and
    scalars.
    """
    if isinstance(value, dict):
        return dict((k, _deep_copy(v)) for k, v in value.items())
    if isinstance(value, list):
        return [_deep_copy(v) for v in value]
    return value


def cmd_edit(args: argparse.Namespace, config: ConfigDict,
             profile_name: str) -> int:
    """Open the config in $EDITOR and validate it after the editor exits.

    The editor inherits the terminal directly rather than going through
    run(), which captures output and would leave a full-screen editor
    with nowhere to draw.

    Deliberately registered as NOT config-mutating in COMMANDS, so main()
    does not wrap the whole call in the config lock. The "modify" step here
    is a human typing, which can take minutes or be left open indefinitely;
    holding an exclusive lock for that long would block every other xtm
    invocation on the machine with no bound on the wait. Nothing is gained
    by it either, since xtm performs no read-modify-write cycle around the
    editor -- the editor writes the file itself. The one write xtm makes,
    restoring the reserved profile after the edit, takes the lock for itself.
    """
    # shlex.split, not a bare name: VISUAL and EDITOR routinely carry
    # arguments ("vim -p", "code --wait"), and treating the whole string as
    # one program name made those settings fail with "Could not start
    # editor" rather than opening the editor the user configured.
    editor = os.environ.get("VISUAL") or os.environ.get("EDITOR") or "vi"
    try:
        editor_argv = shlex.split(editor)
    except ValueError as e:
        return die("Could not read the editor setting %r: %s" % (editor, e))
    if not editor_argv:
        return die("The editor setting %r is empty. Set VISUAL or EDITOR to "
                   "a command, or unset both to use vi." % (editor,))
    if CONTEXT.dry_run:
        logger.info("[dry-run] would open %s in %s", CONTEXT.config_file, editor)
        return EXIT_OK
    logger.debug("Opening %s in %s", CONTEXT.config_file, editor)
    try:
        subprocess.call(editor_argv + [str(CONTEXT.config_file)])
    except OSError as e:
        die("Could not start editor %r: %s" % (editor, e))
    # Held only for the reload, which may rewrite the file to restore the
    # reserved "default" profile if the edit removed it. Only this reload:
    # the load before the editor opened left the file as the user had it.
    with config_lock():
        CONTEXT.may_write_config = True
        return validate_all(json_output=False)


def validate_all(json_output: bool) -> int:
    """Load and deep-validate every profile, reporting problems per profile."""
    config = load_config(create_if_missing=False)
    results = []  # type: List[Tuple[str, Optional[str]]]
    for name in sorted(config.get("profiles", {})):
        try:
            validate_profile(config, name)
        except XtmError as e:
            results.append((name, str(e)))
        else:
            results.append((name, None))
    bad = [name for name, error in results if error]
    if json_output:
        emit_json({
            "config": str(CONTEXT.config_file),
            "valid": not bad,
            "profiles": [{"name": name, "valid": error is None, "error": error}
                         for name, error in results],
        })
    else:
        print("Config: %s" % (CONTEXT.config_file,))
        for name, error in results:
            print("  %-20s %s" % (name, "OK" if error is None else "INVALID: " + error))
    return EXIT_ERROR if bad else EXIT_OK


# Shell words that are not programs on PATH, so a command starting with one
# is not reported as missing.
_SHELL_WORDS = ("cd", "exec", "source", ".", "export", "set", "eval", "env",
                "if", "for", "while", "case", "{", "(", "!", "time", "nohup")

CHECK_OK, CHECK_WARN, CHECK_FAIL = "ok", "warn", "fail"


def _command_program(command: str) -> Optional[str]:
    """The program a slot's command starts, when that can be told.

    None for anything a shell has to interpret first -- an assignment, a
    shell word, a line that does not split -- since guessing would report
    a working command as missing.
    """
    try:
        words = shlex.split(command)
    except ValueError:
        return None
    if not words or "=" in words[0] or words[0] in _SHELL_WORDS:
        return None
    return words[0]


def _profile_reason(args: argparse.Namespace, profile_name: str) -> str:
    """Why this profile is the one in effect, in the words the docs use."""
    if args.profile:
        return "named on the command line"
    if not args.auto and read_current_profile_state() == profile_name:
        return "saved for this machine"
    if profile_name == DEFAULT_PROFILE_NAME:
        return "the reserved default"
    return "matched to this hostname"


def run_checks(args: argparse.Namespace, config: ConfigDict,
               profile_name: str) -> List[Dict[str, str]]:
    """Everything that decides whether `xtm` will bring the profile up.

    Each check is {"check", "status", "detail"} with status ok, warn or
    fail. A failure is something that will stop a window from appearing
    where the profile says; a warning is something that may.
    """
    checks = []  # type: List[Dict[str, str]]

    def add(name: str, status: str, detail: str) -> None:
        checks.append({"check": name, "status": status, "detail": detail})

    profile = validate_profile(config, profile_name)
    add("profile", CHECK_OK, "%s, %s, %d slot(s)"
        % (profile_name, _profile_reason(args, profile_name),
           len(profile["sessions"])))
    display = os.environ.get("DISPLAY")
    add("display", CHECK_OK if display else CHECK_FAIL,
        display or "DISPLAY is not set, so no window can open")
    xterm = which_or_none("xterm")
    add("xterm", CHECK_OK if xterm else CHECK_FAIL,
        xterm or "not found on PATH")
    version = tmux_version()
    if version is None:
        add("tmux", CHECK_FAIL, "not found on PATH, or did not report a version")
    elif version < HOLD_FAILED_PANES_SINCE:
        add("tmux", CHECK_WARN, "%d.%d: a failing start command closes its window "
            "without a trace; 3.2 or newer keeps it on screen" % version)
    else:
        add("tmux", CHECK_OK, "%d.%d" % version)
    tool = detect_geometry_tool()
    if tool is None:
        add("geometry tool", CHECK_WARN, "none of %s: placement works, but "
            "--update-profile, --verify and --focus do not"
            % (", ".join(GEOMETRY_TOOLS),))
    elif tool == "xwininfo":
        add("geometry tool", CHECK_WARN, "xwininfo reads positions but cannot "
            "raise a window, so --focus needs xdotool or wmctrl")
    else:
        add("geometry tool", CHECK_OK, tool)
    monitors = screen_monitors() if display else None
    if monitors:
        add("monitors", CHECK_OK, _describe_monitors(monitors))
    else:
        add("monitors", CHECK_WARN, "unknown (no xrandr or xwininfo answer), "
            "so windows are not checked against the screen")

    for slot, session in profile_slots(profile):
        cfg = profile["sessions"][slot]
        label = "slot %s" % (slot,)
        problems = []  # type: List[Tuple[str, str]]
        cwd = cfg.get("cwd")
        if cwd and not os.path.isdir(os.path.expanduser(str(cwd))):
            problems.append((CHECK_FAIL, "cwd %s does not exist; tmux would "
                             "silently start in your home directory" % (cwd,)))
        command = cfg.get("command")
        program = _command_program(str(command)) if command else None
        if program:
            found = (os.access(os.path.expanduser(program), os.X_OK)
                     if "/" in program else which_or_none(program))
            if not found:
                problems.append((CHECK_WARN, "command %r: %s is not on PATH"
                                 % (command, program)))
        if monitors:
            fit = screen_fit(session_geometry(cfg), monitors)
            if fit != ON_SCREEN:
                problems.append((CHECK_FAIL, "%s on this screen; --clamp brings "
                                 "it onto a monitor" % (fit,)))
        if not problems:
            add(label, CHECK_OK, session)
        for status, detail in problems:
            add(label, status, detail)
    return checks


def cmd_check(args: argparse.Namespace, config: ConfigDict,
              profile_name: str) -> int:
    """Say whether `xtm` would bring the profile up without trouble.

    Changes nothing. Exits 1 when any check fails, so it can gate a login
    script; warnings alone exit 0.
    """
    checks = run_checks(args, config, profile_name)
    failed = [c for c in checks if c["status"] == CHECK_FAIL]
    if args.json:
        emit_json({"config": str(CONTEXT.config_file), "profile": profile_name,
                   "checks": checks, "ok": not failed})
    else:
        for c in checks:
            print("  %-5s %-16s %s" % (c["status"], c["check"], c["detail"]))
        warned = sum(1 for c in checks if c["status"] == CHECK_WARN)
        print("")
        print("%d problem(s), %d warning(s)." % (len(failed), warned))
    return EXIT_ERROR if failed else EXIT_OK


def cmd_validate(args: argparse.Namespace, config: ConfigDict,
                 profile_name: str) -> int:
    """Check every profile in the config file."""
    return validate_all(args.json)


# --------------------------------------------------------------------------
# Commands: reporting
# --------------------------------------------------------------------------

def emit_json(payload: Any) -> None:
    """Write a machine-readable result to stdout.

    All reporting commands share this so that --json output is uniform
    and always lands on stdout, separate from the log stream on stderr.
    """
    print(json.dumps(payload, indent=2, sort_keys=True))


def session_status(session: str, live: Dict[str, bool],
                   windows: Dict[str, List[str]]) -> str:
    """Human-readable running state of one session.

    "attached" means an xtm window shows it. "attached elsewhere" means
    only other terminals do -- an SSH login, a Konsole -- so a reset will
    open an xtm window beside them.
    """
    if session not in live:
        return "not running"
    if session in windows:
        return "attached"
    return "attached elsewhere" if live[session] else "detached"


def collect_status(config: ConfigDict, profile_name: str) -> Dict[str, Any]:
    """Gather everything --list reports, in one structure.

    Building the report as data first means the plain-text and JSON
    renderings cannot drift apart, and the live-position lookup happens
    exactly once either way.
    """
    profile = validate_profile(config, profile_name)
    tool = detect_geometry_tool()
    live = tmux_list_sessions()
    windows = xtm_windows()
    monitors = screen_monitors()

    sessions = []
    for slot, session in profile_slots(profile):
        cfg = profile["sessions"][slot]
        entry = {
            "name": slot,
            "session": session,
            "shared": bool(cfg.get("session")),
            "title": resolve_window_title(profile, profile_name, slot, session),
            "status": session_status(session, live, windows),
            "configured": session_geometry(cfg),
            "command": cfg.get("command"),
            "cwd": cfg.get("cwd"),
            "setup": cfg.get("setup"),
            "live": None,
            "placement": (screen_fit(session_geometry(cfg), monitors)
                          if monitors else None),
        }
        if session in windows and tool:
            entry["live"] = get_window_geometry(session, tool)
        sessions.append(entry)

    named = set(profile_sessions(profile))
    strays = sorted(name for name in windows if name not in named)
    # Say which other profile a stray belongs to, so an accumulation left
    # behind by an earlier profile is recognizable rather than mysterious.
    # The membership test runs once per session of every visible profile, so
    # it reads from a set rather than scanning the sorted list each time.
    stray_set = set(strays)
    owners = {}
    for other in visible_profile_names(config, include_all=True):
        if other == profile_name:
            continue
        candidate = config["profiles"][other]
        if not isinstance(candidate, dict):
            continue
        for name in profile_sessions(candidate):
            if name in stray_set:
                owners.setdefault(name, other)
    return {
        "profile": profile_name,
        "prefix": profile.get("prefix", ""),
        "on_switch": switch_mode(profile),
        "config": str(CONTEXT.config_file),
        "state_file": str(CONTEXT.state_file),
        "geometry_tool": tool,
        "frame_compensation": CONTEXT.frame_compensation,
        "monitors": monitors,
        "sessions": sessions,
        "strays": strays,
        "stray_owners": owners,
    }


def cmd_list(args: argparse.Namespace, config: ConfigDict, profile_name: str) -> int:
    """Show the status of every slot in the current profile."""
    status = collect_status(config, profile_name)
    if args.json:
        emit_json(status)
        return EXIT_OK

    print("Profile: %s" % (status["profile"],))
    print("Config:  %s" % (status["config"],))
    if status["prefix"]:
        print("Prefix:  %s" % (status["prefix"],))
    print("")
    print("Slots:")
    if not status["sessions"]:
        print("  (none configured)")
    for entry in status["sessions"]:
        configured = entry["configured"]
        position = "x=%s y=%s %sx%s" % (configured["x"], configured["y"],
                                        configured["width"], configured["height"])
        live_text = ""
        if entry["live"]:
            geometry = entry["live"]
            live_text = "  [live: x=%s y=%s %sx%s]" % (
                geometry["x"], geometry["y"], geometry["width"], geometry["height"])
        if entry["placement"] not in (None, ON_SCREEN):
            live_text += "  [%s]" % (entry["placement"],)
        print("  %-20s %-18s configured: %s%s"
              % (entry["name"], entry["status"], position, live_text))
        # Worth a line whenever the session name is not simply the slot
        # name, or whenever it is shared -- being shared changes what a
        # profile switch will do to it, so it should never be invisible.
        if entry["session"] != entry["name"] or entry["shared"]:
            print("  %-20s %-18s session:    %s%s"
                  % ("", "", entry["session"],
                     "  (shared, kept on profile switch)"
                     if entry["shared"] else ""))
        # Launch settings are shown only when set, on their own lines, so
        # the common case stays a single line per session while a session
        # that does something unusual on startup says so.
        if entry["command"]:
            print("  %-20s %-18s command:    %s" % ("", "", entry["command"]))
        if entry["cwd"]:
            print("  %-20s %-18s cwd:        %s" % ("", "", entry["cwd"]))
        if entry["setup"]:
            print("  %-20s %-18s setup:      %s" % ("", "", entry["setup"]))
    print("")
    print("Stray xtm windows (sessions this profile does not name):")
    if not status["strays"]:
        print("  (none)")
    for session in status["strays"]:
        owner = status["stray_owners"].get(session)
        print("  %s%s" % (session,
                          "  (belongs to profile %s)" % (owner,) if owner else ""))
    owned = sum(1 for s in status["strays"] if s in status["stray_owners"])
    if owned:
        logger.info("%d stray session(s) belong to another profile; switch to "
                    "it, or use --all --close-all to clean them up.", owned)
    if not status["geometry_tool"]:
        print("")
        print("(Install or locate %s to show live window positions here.)"
              % (", ".join(GEOMETRY_TOOLS),))
    return EXIT_OK


def _list_profiles_verbose(args: argparse.Namespace, config: ConfigDict,
                           profile_name: str) -> int:
    """Report every profile, its slots and their running state.

    Deliberately part of --list-profiles rather than --list: --list
    reports one profile, and --all already means "widen the scope",
    which would collide with naming a specific profile to look at.
    """
    live = tmux_list_sessions()
    windows = xtm_windows()
    names = visible_profile_names(config, args.all)
    report = []
    for name in names:
        profile = config["profiles"][name]
        if not isinstance(profile, dict):
            continue
        slots = []
        for slot, session in profile_slots(profile):
            cfg = profile["sessions"][slot]
            slots.append({
                "name": slot,
                "session": session,
                "shared": bool(cfg.get("session")),
                "status": session_status(session, live, windows),
            })
        report.append({
            "name": name,
            "current": name == profile_name,
            "prefix": profile.get("prefix", ""),
            "on_switch": switch_mode(profile),
            "slots": slots,
        })

    claimed = set()
    for entry in report:
        claimed.update(slot["session"] for slot in entry["slots"])
    unclaimed = sorted(name for name in windows if name not in claimed)

    if args.json:
        emit_json({"profiles": report, "unclaimed": unclaimed,
                   "config": str(CONTEXT.config_file)})
        return EXIT_OK

    print("Config: %s" % (CONTEXT.config_file,))
    for entry in report:
        header = entry["name"] + (" *" if entry["current"] else "")
        details = []
        if entry["prefix"]:
            details.append("prefix %s" % (entry["prefix"],))
        details.append("on_switch %s" % (entry["on_switch"],))
        print("")
        print("%s  (%s)" % (header, ", ".join(details)))
        if not entry["slots"]:
            print("  (no slots configured)")
        for slot in entry["slots"]:
            print("  %-18s %-14s %-18s%s"
                  % (slot["name"], slot["session"], slot["status"],
                     "  shared" if slot["shared"] else ""))
    print("")
    print("Sessions claimed by no profile:")
    if not unclaimed:
        print("  (none)")
    for session in unclaimed:
        print("  %s" % (session,))
    return EXIT_OK


def cmd_current_profile(args: argparse.Namespace, config: ConfigDict,
                        profile_name: str) -> int:
    """Print the profile this machine is currently using."""
    current = read_current_profile_state()
    if args.json:
        emit_json({"current_profile": current, "resolved_profile": profile_name,
                   "state_file": str(CONTEXT.state_file)})
        return EXIT_OK
    print(current or profile_name or "(no current profile set)")
    return EXIT_OK


def cmd_list_profiles(args: argparse.Namespace, config: ConfigDict,
                      profile_name: str) -> int:
    """List every profile in the config, marking the one in effect.

    The marked profile is the one a command would actually use right now,
    which is not always the persisted one: on a fresh install nothing has
    been selected yet and the built-in "default" is in effect. Marking
    the effective profile answers the question the listing is really
    being asked.
    """
    if args.verbose:
        return _list_profiles_verbose(args, config, profile_name)
    current = read_current_profile_state()
    # The profile this run acts on. The saved one only when nothing could
    # be resolved: under --auto they differ, and marking the saved profile
    # then pointed at one the run was not going to touch.
    effective = profile_name or current
    profiles = config.get("profiles", {})
    names = visible_profile_names(config, args.all)
    hidden = len(profiles) - len(names)
    if args.json:
        emit_json({
            "current": current,
            "effective": effective,
            "profiles": [
                {
                    "name": name,
                    "current": name == current,
                    "effective": name == effective,
                    "sessions": len(profiles[name].get("sessions", {}))
                    if isinstance(profiles[name], dict) else 0,
                    "match": profiles[name].get("match")
                    if isinstance(profiles[name], dict) else None,
                    "global": profile_is_visible(profiles[name])
                    and not (isinstance(profiles[name], dict)
                             and profiles[name].get("match")),
                }
                for name in names
            ],
            "hidden": hidden,
        })
        return EXIT_OK
    for name in names:
        print(name + (" *" if name == effective else ""))
    if hidden:
        logger.info("%d profile(s) belonging to other machines are hidden; "
                    "use --all to include them.", hidden)
    return EXIT_OK


# --------------------------------------------------------------------------
# Command table
# --------------------------------------------------------------------------
#
# Each entry is (argparse dest, handler, mutates_config, needs_profile,
# switches_profile). The table is the single source of truth for dispatch:
# which action ran, whether the config lock is needed, and which action a
# bare `xtm` falls back to.
#
# switches_profile: whether a profile named with -p becomes this machine's
# current profile, running the old one's on_switch. False for the commands
# that only report (--list, --list-profiles, --validate, --check): looking
# at a profile before switching to it must not switch to it. --current-profile
# keeps True, so `xtm -p work -C` selects and confirms in one call.

COMMANDS = (
    # (argparse dest, handler, mutates_config, needs_profile, switches_profile)
    ("open", cmd_open, False, True, True),
    ("reset", cmd_reset, False, True, True),
    ("reset_all", cmd_reset_all, False, True, True),
    # needs_profile: --close and --focus resolve a slot name against the
    # profile before acting, exactly as --detach does, so an unresolvable
    # profile is fatal for all three rather than for only one of them.
    ("close", cmd_close, False, True, True),
    ("close_all", cmd_close_all, False, True, True),
    ("focus", cmd_focus, False, True, True),
    ("list", cmd_list, False, True, False),
    ("list_profiles", cmd_list_profiles, False, False, False),
    ("current_profile", cmd_current_profile, False, False, True),
    ("update_profile", cmd_update_profile, True, True, True),
    ("new_profile", cmd_new_profile, True, False, True),
    ("set", cmd_set, True, True, True),
    ("delete_session", cmd_delete_session, True, True, True),
    ("delete_profile", cmd_delete_profile, True, False, True),
    ("copy_profile", cmd_copy_profile, True, False, True),
    ("rename_profile", cmd_rename_profile, True, False, True),
    ("detach", cmd_detach, False, True, True),
    ("make_global", cmd_make_global, True, False, True),
    # NOT marked config-mutating: cmd_edit takes the lock itself around
    # its short writes rather than holding it across an interactive
    # editor session. See its docstring.
    ("edit", cmd_edit, False, False, True),
    ("validate", cmd_validate, False, False, False),
    ("check", cmd_check, False, True, False),
)


def cmd_default(args: argparse.Namespace, config: ConfigDict,
                profile_name: str) -> int:
    """What a bare `xtm`, or `xtm NAME`, does: bring the profile up.

    Sitting down and typing one word should give back the whole layout, so
    the default is --reset -- which is idempotent: it opens what is missing
    and snaps back what has moved, and never closes anything. A profile with
    no slots has nothing to bring up, and --json asks for a report, so both
    get the status view instead: on a fresh install `xtm` explains itself
    rather than doing nothing.
    """
    profile = validate_profile(config, profile_name)
    if args.json or not profile["sessions"]:
        logger.debug("Showing status: %s.", "--json was given" if args.json
                     else "profile %s has no slots" % (profile_name,))
        args.list = True
        return cmd_list(args, config, profile_name)
    logger.debug("Bringing profile %s up.", profile_name)
    return cmd_reset(args, config, profile_name)


# What runs when no action is named. Not in COMMANDS: it is not an option.
DEFAULT_ENTRY = ("default", cmd_default, False, True, True)


def selected_action(args: argparse.Namespace
                    ) -> Tuple[str, Any, bool, bool, bool]:
    """Return the (dest, handler, mutates_config, needs_profile,
    switches_profile) entry for this run: the one action named, or
    cmd_default when none is."""
    for entry in COMMANDS:
        if getattr(args, entry[0], None):
            return entry
    logger.debug("No action requested; bringing the profile up.")
    return DEFAULT_ENTRY


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    """Construct the argument parser.

    Every frequently used option has both a short and a long form.
    """
    parser = argparse.ArgumentParser(
        prog="xtm",
        description="xterm + tmux workspace manager (version %s)" % (__version__,),
        epilog="With no action, xtm brings the profile up: it opens every "
               "slot's window that is missing and moves the rest back into "
               "place (--reset). A profile with no slots shows its status.",
    )

    parser.add_argument("--version", action="version",
                        version="xtm %s" % (__version__,))

    # Switching profiles is by far the most common thing the tool is asked
    # to do, so it gets the shortest possible spelling: `xtm desk`. The
    # --profile flag remains as an equivalent alias for existing scripts.
    parser.add_argument("profile_positional", nargs="?", metavar="PROFILE",
                        help="Profile to switch to, for example 'xtm desk'. "
                             "Equivalent to --profile NAME.")

    general = parser.add_argument_group("general options")
    general.add_argument("-p", "--profile", metavar="NAME",
                         help="Use profile NAME and persist it as this "
                              "machine's current profile. Same as giving the "
                              "name as a positional argument.")
    general.add_argument("-a", "--auto", action="store_true",
                         help="Ignore the saved current profile and select one "
                              "by matching the hostname instead.")
    general.add_argument("-A", "--all", action="store_true",
                         help="Widen the scope: include profiles belonging to "
                              "other machines when selecting or listing them, "
                              "and with --close-all cover every profile "
                              "VISIBLE ON THIS MACHINE rather than only the "
                              "current one. --close-all never reaches "
                              "profiles bound to another host.")
    general.add_argument("--on-switch", choices=SWITCH_MODES,
                         help="Override what happens to the previous profile's "
                              "windows when switching profiles.")
    general.add_argument("--capture-new", action="store_true",
                         help="With --update-profile, also add open windows "
                              "that are not part of the current profile.")
    general.add_argument("-v", "--verbose", action="store_true",
                         help="With --list-profiles, also show each profile's "
                              "slots, session names and running state.")
    general.add_argument("--detach-mode", action="store_true",
                         help="With --close-all, detach the windows instead of "
                              "killing the sessions.")
    general.add_argument("-c", "--config", metavar="PATH",
                         help="Path to the config file (default: "
                              "$XTM_CONFIG_DIR/config.yaml).")
    general.add_argument("--state-dir", metavar="PATH",
                         help="Directory holding the current-profile state "
                              "file (default: $XTM_STATE_DIR).")
    general.add_argument("--hostname", metavar="NAME",
                         help="Act as the machine NAME: match blocks, "
                              "profile visibility and the state file all "
                              "follow it. For checking a config written for "
                              "another machine; pair with --dry-run to "
                              "change nothing.")
    general.add_argument("-j", "--json", action="store_true",
                         help="Emit machine-readable JSON from reporting "
                              "commands (--list, --list-profiles, "
                              "--current-profile, --validate, --check).")
    general.add_argument("-N", "--dry-run", action="store_true",
                         help="Show what would happen without launching, "
                              "moving, closing or saving anything.")
    general.add_argument("-y", "--yes", action="store_true",
                         help="Answer yes to confirmation prompts.")
    general.add_argument("--verify", action="store_true",
                         help="After placing a window, read its position back "
                              "and report any difference from what was asked.")
    general.add_argument("--clamp", action="store_true",
                         help="When placing, move a window that would land "
                              "wholly or partly off the connected monitors "
                              "onto the one it overlaps most, instead of only "
                              "warning.")
    general.add_argument("--no-frame-compensation", action="store_true",
                         help="Do not correct captured positions for window "
                              "manager decorations (see README).")

    logging_group = parser.add_argument_group("logging options")
    logging_group.add_argument("-d", "--debug", action="store_true",
                               help="Verbose diagnostic logging to stderr: "
                                    "every subprocess call, parsing decision "
                                    "and file operation.")
    logging_group.add_argument("-q", "--quiet", action="store_true",
                               help="Log only warnings and errors to stderr.")
    logging_group.add_argument("--log-level", metavar="LEVEL",
                               choices=["debug", "info", "warning", "error",
                                        "critical"],
                               help="Set the stderr log level explicitly "
                                    "(overrides --debug/--quiet).")
    logging_group.add_argument("-L", "--log-file", metavar="PATH",
                               help="Also write full DEBUG-level logs to PATH. "
                                    "Off by default.")

    actions = parser.add_argument_group("actions (choose at most one)")
    action = actions.add_mutually_exclusive_group()
    action.add_argument("-o", "--open", metavar="SESSION",
                        help="Open one tmux session in a new xterm.")
    action.add_argument("-r", "--reset", action="store_true",
                        help="Reposition or open every named session in the "
                             "profile.")
    action.add_argument("-R", "--reset-all", action="store_true",
                        help="Like --reset, and also stack any stray sessions.")
    action.add_argument("-k", "--close", metavar="SESSION",
                        help="Kill one tmux session and close its window.")
    action.add_argument("-K", "--close-all", action="store_true",
                        help="Kill every running session named in the profile.")
    action.add_argument("-f", "--focus", metavar="SESSION",
                        help="Raise and focus one session's window.")
    action.add_argument("-l", "--list", action="store_true",
                        help="Show session and window status for the profile.")
    action.add_argument("-P", "--list-profiles",
                        action="store_true",
                        help="List every profile in the config.")
    action.add_argument("-C", "--current-profile",
                        action="store_true",
                        help="Print this machine's current profile.")
    action.add_argument("-u", "--update-profile",
                        action="store_true",
                        help="Save current window positions into the profile.")
    action.add_argument("-n", "--new-profile", metavar="NAME",
                        help="Create a new profile from the current layout.")
    action.add_argument("-s", "--set", nargs=2, metavar=("SESSION", "GEOMETRY"),
                        help="Set a session's position in the profile. "
                             "GEOMETRY is 'x,y,width,height'.")
    action.add_argument("-D", "--delete-session", metavar="SESSION",
                        help="Remove a session's entry from the profile "
                             "(does not kill a running session).")
    action.add_argument("--delete-profile", metavar="NAME",
                        help="Delete a profile from the config.")
    action.add_argument("--copy-profile", nargs=2, metavar=("SOURCE", "TARGET"),
                        help="Copy an existing profile to a new name.")
    action.add_argument("--rename-profile", nargs=2, metavar=("OLD", "NEW"),
                        help="Rename an existing profile.")
    action.add_argument("-t", "--detach", metavar="SESSION",
                        help="Close a session's window but leave the session, "
                             "its tabs and its panes running.")
    action.add_argument("--make-global", metavar="NAME",
                        help="Remove a profile's machine binding so it is "
                             "available on every machine. One-way.")
    action.add_argument("-e", "--edit", action="store_true",
                        help="Open the config in $EDITOR, then validate it.")
    action.add_argument("--check", action="store_true",
                        help="Check that the profile would come up without "
                             "trouble: display, xterm, tmux, tools, monitors, "
                             "and each slot's cwd, command and position. "
                             "Changes nothing.")
    action.add_argument("-V", "--validate", action="store_true",
                        help="Check every profile in the config file.")
    return parser


def apply_runtime_flags(args: argparse.Namespace) -> None:
    """Publish the cross-cutting run modes taken from parsed arguments."""
    CONTEXT.dry_run = bool(args.dry_run)
    CONTEXT.verify = bool(args.verify)
    CONTEXT.frame_compensation = not args.no_frame_compensation
    CONTEXT.clamp = bool(args.clamp)
    if args.hostname is not None:
        # Same charset as a profile name, because it ends up in the state
        # file's name: a hostname carrying a slash or a colon would write
        # outside the state directory or produce a file that cannot be read
        # back.
        CONTEXT.hostname = validate_profile_name(args.hostname)


def main(argv: Optional[List[str]] = None) -> int:
    """Parse arguments, resolve the profile, and run the chosen action."""
    parser = build_parser()
    args = parser.parse_args(argv)
    _merge_positional_profile(parser, args)

    setup_logging(console_level_from_args(args), args.log_file)
    apply_runtime_flags(args)
    resolve_paths(args.config, args.state_dir)
    logger.debug("xtm %s starting on python %s; args=%s", __version__,
                 platform.python_version(), vars(args))
    logger.debug("Config file: %s | state file: %s | PyYAML: %s",
                 CONTEXT.config_file, CONTEXT.state_file,
                 getattr(yaml, "__version__", "not installed"))
    if CONTEXT.dry_run:
        logger.info("Dry run: no windows or files will be changed.")
    if CONTEXT.hostname:
        # Said once, at INFO, because every later message about visibility
        # and matching only makes sense if the reader knows which machine
        # this run is pretending to be.
        logger.info("Acting as host %r rather than %s.",
                    CONTEXT.hostname, platform.node().split(".")[0])

    dest, handler, mutates, needs_profile, switches = selected_action(args)
    logger.debug("Action: %s (mutates config: %s, needs profile: %s, "
                 "switches profile: %s)", dest, mutates, needs_profile, switches)
    # --edit is marked as not mutating because it must not hold the lock
    # across an editor session; it grants itself permission for the one
    # write it makes, under the lock (cmd_edit).
    CONTEXT.may_write_config = mutates

    # Mutating commands hold the lock across the whole load/modify/save
    # cycle, so two concurrent runs cannot lose each other's edits.
    if mutates:
        with config_lock():
            return _run_action(args, handler, needs_profile, switches)
    return _run_action(args, handler, needs_profile, switches)


def _merge_positional_profile(parser: argparse.ArgumentParser,
                              args: argparse.Namespace) -> None:
    """Fold the positional PROFILE argument into args.profile.

    Supplying both spellings is rejected rather than resolved in favor
    of one, even when they name the same profile: silently picking a
    winner would hide a mistake in a script that builds its arguments
    programmatically.
    """
    positional = getattr(args, "profile_positional", None)
    if positional is None:
        return
    if args.profile is not None:
        parser.error("give the profile either as a positional argument or "
                     "with --profile, not both")
    args.profile = positional


def _run_action(args: argparse.Namespace, handler: Any,
                needs_profile: bool, switches: bool) -> int:
    """Load the config, resolve the profile and invoke one command.

    --profile is resolved before dispatch regardless of the action, so
    that `xtm -p work -C` both selects and reports the profile in one
    call rather than silently ignoring the selection. For a command that
    only reports (`switches` False) the named profile is used for this
    run and nothing is switched or saved.

    Commands that operate on the config as a whole rather than on one
    profile (listing, validating, renaming) tolerate an unresolvable
    selection: a config with no "default" profile and no saved choice
    must not stop the user from listing what profiles do exist.
    """
    config = load_config()
    try:
        profile_name = resolve_profile_name(config, args.profile, args.auto,
                                            args.all, args.on_switch, switches)
    except XtmError:
        if needs_profile or args.profile:
            raise
        logger.debug("No profile could be resolved, and this command does not "
                     "need one; continuing.")
        profile_name = None
    logger.debug("Resolved profile: %s", profile_name)
    return handler(args, config, profile_name)


def cli(argv: Optional[List[str]] = None) -> int:
    """Run xtm and turn its exceptions into exit codes.

    This, not main(), is the program's real entry point, and it is what
    both `python3 xtm.py` and the console script installed by
    pyproject.toml call. Keeping the error handling here rather than in
    the `if __name__ == "__main__"` block below is what makes the two
    behave the same: a pip-installed xtm imports this module and calls
    one function, so anything guarded only by that block would not run
    for it, and an ordinary bad config would reach the user as a Python
    traceback instead of the documented one-line message.

    main() stays free of this so that tests can call it and see the
    XtmError itself.
    """
    try:
        return main(argv)
    except XtmError as e:
        # The one-line message always shows; the traceback is additionally
        # available under --debug.
        logger.error("%s", e)
        logger.debug("Full exception detail:", exc_info=True)
        return EXIT_ERROR
    except KeyboardInterrupt:
        logger.debug("Interrupted by user (KeyboardInterrupt).")
        print("", file=sys.stderr)
        logger.warning("Interrupted.")
        return EXIT_INTERRUPTED


if __name__ == "__main__":
    sys.exit(cli())
