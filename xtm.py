#!/usr/bin/env python3
"""
xtm - xterm + tmux workspace manager

Manages a set of xterm windows, each attached to a named tmux session,
positioned on screen according to a per-machine "profile" stored in a
small YAML config file.

No non-standard tools are required for day-to-day use (opening, resetting
and closing sessions): only xterm, tmux and python3. Window placement is
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

Compatibility: this targets plain Python 3.6+ with no third-party packages
required, since it is meant to run unmodified on servers where the user
cannot install anything. That rules out PEP 604 "X | Y" unions (3.10+),
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
        # Read each window's position back after placing it and report the
        # difference from what was asked for.
        self.verify = False
        # Correct captured positions for window-manager decorations.
        self.frame_compensation = True

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
# contain ':' (which would break TITLE_PREFIX parsing) or YAML-structural
# characters (which would break the dependency-free config parser/dumper).
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

# Runtime switches set once from parsed arguments. Kept as module state
# rather than passed through every signature because they are read-only
# cross-cutting concerns (a global "pretend" mode and a global "check the
# result" mode) that would otherwise appear in a dozen unrelated
# signatures purely to be forwarded.
CONTEXT.dry_run = False
CONTEXT.verify = False
CONTEXT.frame_compensation = True

# Exit codes. Documented in README.md; keep the two in sync.
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
    """Same charset rules as session names. This keeps profile names safe
    as YAML mapping keys (notably free of ':') for both the PyYAML and the
    dependency-free parser, and safe to store as plain text in the
    current-profile state file."""
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
    """Serialise read-modify-write cycles on the config file.

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
            # nothing to serialise anyway, since no write will follow.
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
# YAML: which backend is available
# --------------------------------------------------------------------------

try:
    import yaml as _pyyaml  # type: ignore

    HAVE_PYYAML = True
except ImportError:
    HAVE_PYYAML = False

# --------------------------------------------------------------------------
# YAML: the parity guard, the reader, the writer
# --------------------------------------------------------------------------
#
# A map of this region, because every defect found in it so far has lived in
# the seam between two of these functions rather than inside any one of them.
# Each line names the question the function answers; nothing else in the file
# answers that question.
#
#   THE GUARD -- refuses input the two backends would read differently. It
#   produces no value; it either returns or raises.
#     _scan_for_ambiguous_scalars   which line is being checked, and in what
#                                   context (block, flow, key, value)
#     _reject_ambiguous_indent      may this line's whitespace be read by both
#     _reject_ambiguous_scalar      is this plain value read the same by both
#     _reject_reserved_key          is this key a NAME to both
#     _reject_renumbered_key        does this key's text survive being a number
#     _reject_flow_entry            is this `k: v` inside { } split the same
#     _reject_flow_item             is this item inside [ ] read the same
#     _reject_flow_indicator_in_scalar  does this scalar end where both agree
#     _reject_unsupported_escape    is this escape implemented on both sides
#     _reject_unbalanced            does this quoted or bracketed token close
#
#   THE READER -- turns text into data, and runs only when PyYAML is absent.
#     _simple_yaml_load             block structure: indentation and nesting
#     _simple_yaml_scalar_load      one value's type
#     _simple_yaml_key_load         one key's text (a name, never a value)
#     _simple_yaml_flow_mapping_load  one { } mapping
#     _unescape_double_quoted       one double-quoted body's escapes
#
#   SHARED TEXT PRIMITIVES -- used by both, which is why they are here and
#   not inside either.
#     _partition_key                where a key ends and its value begins
#     _split_top_level              where one flow item ends and the next starts
#     _strip_inline_comment         where a value ends and a comment begins
#
#   The writer (_simple_yaml_dump and below) is further down, next to
#   dump_yaml, because it is the part that changes on its own.
#
# THE PAIRS THAT MUST AGREE. These are the seams, and each one has been wrong
# at least once:
#
#   _partition_key and _split_top_level must answer "is this colon a
#   separator" and "is this quote an opener" the same way, in every context.
#   Both once said yes anywhere, and both produced silent divergence.
#
#   _simple_yaml_scalar_dump and _reject_ambiguous_scalar must agree on what
#   needs quoting. They no longer can disagree: the dumper asks the guard
#   (_would_be_refused_unquoted) rather than keeping a second list.
#
#   _reject_unsupported_escape and _unescape_double_quoted must implement the
#   same escape set. They share _YAML_ESCAPES for that reason.
#
#   _reject_reserved_key and _normalize_config_keys together decide which key
#   spellings are safe: a numeric key needs no rule because normalisation
#   makes 2 and "2" the same name, while a boolean one does.
#
# Adding a rule means naming which question it answers and which of these
# pairs it touches. A rule that does not fit an existing question is a sign
# the question list is wrong, not that the rule needs a new home.
#
# PyYAML implements YAML 1.1, which reads a number of unquoted scalars in
# ways the built-in parser deliberately does not: 0755 is octal (493), 1:30
# is sexagesimal (90), 0x1f is hex, 1_000 has digit separators, `yes` is
# boolean True, and 2026-01-01 is a date object. The built-in parser reads
# every one of those as either a plain decimal number or a string.
#
# That means the SAME config file could place a window at two different
# coordinates depending only on whether PyYAML happens to be installed --
# silently, because both readings are valid numbers that pass validation.
# For a tool whose whole point is running unmodified on machines where
# nothing can be installed, that is the worst possible failure mode.
#
# Rather than teach the built-in parser all of YAML 1.1 (a large amount of
# code, and it would import YAML 1.1's footguns on purpose), these forms are
# rejected before either backend sees them. Both backends then behave
# identically: a clear error naming the value and how to fix it. Nothing is
# lost, because xtm's schema is pixel geometry and a handful of strings --
# no config has a legitimate use for octal, hex, sexagesimal or dates, and
# any of them can still be written as a quoted string.
#
# Each pattern is anchored and matches a WHOLE token, so ordinary values that
# merely start with a digit, dot or dash -- `.config/xtm`, `-e`, `2>&1` --
# are untouched.

# Every numeric pattern accepts a leading '+' as well as '-'. YAML 1.1 does,
# so "+0755" is octal to PyYAML exactly as "0755" is; a sign-blind pattern
# would let the signed spelling through the guard it was written to close.
_YAML_AMBIGUOUS_PATTERNS = (
    (re.compile(r"^[-+]?0[0-9_]+\Z"),
     "a leading zero, which PyYAML reads as octal and the built-in parser "
     "reads as decimal"),
    (re.compile(r"^[-+]?0[xXbBoO][0-9a-fA-F_]+\Z"),
     "a base prefix, which only PyYAML understands"),

    (re.compile(r"^[-+]?[0-9][0-9_]*(:[0-9]{1,2})+(\.[0-9_]*)?\Z"),
     "colons, which PyYAML reads as a sexagesimal number (1:30 becomes 90)"),
    (re.compile(r"^[-+]?([0-9]+\.?[0-9]*|\.[0-9]+)[eE][-+]?[0-9]+\Z"),
     "scientific notation, which the two parsers disagree about"),
    (re.compile(r"^[-+]?\.(inf|Inf|INF|nan|NaN|NAN)\Z"),
     "an infinity or not-a-number literal, which only PyYAML understands"),
    (re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}([Tt ][0-9:.+\-Zz]*)?\Z"),
     "a date, which PyYAML turns into a date object"),
)

# A number once its digit separators are stripped. Used to spot "1_000" and
# "1_0.5" without the regex accidentally matching plain "1.5" -- an earlier
# attempt to fold this into the pattern table above did exactly that and
# rejected every ordinary decimal.
_YAML_NUMBER_RE = re.compile(
    r"^[-+]?([0-9]+\.?[0-9]*|\.[0-9]+)([eE][-+]?[0-9]+)?\Z")


def _split_top_level(text: str, separator: str = ",") -> List[str]:
    """Split on `separator` while ignoring separators nested inside
    quotes, brackets or braces. Needed to parse flow-style collections
    such as {x: 0, y: 0} and [a, b] without a full YAML engine.

    A quote counts as one only where a scalar may begin -- after "[",
    "{", a separator, or a key's ": ". Everywhere else YAML reads it as
    an ordinary character in a plain scalar, and treating it as a
    delimiter suppressed the split PyYAML performs: `{command: echo
    "hi, there"}` was one command here and a truncated command plus a
    stray key to PyYAML, so the same config ran two different commands
    depending on which backend read it.
    """
    parts = []  # type: List[str]
    depth = 0
    quote = ""
    at_item_start = True
    current = []  # type: List[str]
    for index, ch in enumerate(text):
        if quote:
            current.append(ch)
            if ch == quote:
                quote = ""
            continue
        if ch in "\"'" and at_item_start:
            quote = ch
            current.append(ch)
            at_item_start = False
            continue
        if ch not in " \t":
            at_item_start = (ch in "[{" or ch == separator
                             or (ch == ":"
                                 and text[index + 1:index + 2] in ("", " ")))
        if ch in "[{":
            depth += 1
        elif ch in "]}":
            depth -= 1
        if ch == separator and depth == 0:
            parts.append("".join(current))
            current = []
            continue
        current.append(ch)
    parts.append("".join(current))
    return parts


def _strip_inline_comment(line: str) -> str:
    """Remove a trailing YAML comment from a line.

    A '#' only starts a comment when it is at the start of the line or
    preceded by whitespace, and is never a comment inside a quoted
    scalar. Without this, a perfectly ordinary annotated config line such
    as `offset_x: 40  # per-window offset` would load as the string
    "40  # per-window offset" instead of the number 40.
    """
    quote = ""
    for i, ch in enumerate(line):
        if quote:
            if ch == quote:
                quote = ""
            continue
        if ch in "\"'":
            quote = ch
            continue
        if ch == "#" and (i == 0 or line[i - 1] in " \t"):
            return line[:i]
    return line


def _has_digit_separators(raw: str) -> bool:
    """Whether `raw` is a number written with underscores, which only PyYAML
    understands (it reads 1_000 as 1000; the built-in parser sees text)."""
    return "_" in raw and bool(_YAML_NUMBER_RE.match(raw.replace("_", "")))


# YAML 1.1 booleans. `true`/`false` are read the same way by both parsers and
# so are deliberately absent here; these four are not.
_YAML_AMBIGUOUS_WORDS = ("yes", "no", "on", "off")

# YAML's structural indicators, which mean something to PyYAML and nothing to
# the built-in parser when they OPEN an unquoted value:
#
#   &anchor  *alias   PyYAML resolves them; `x: &a 5` is 5 to PyYAML and the
#                     text "&a 5" here, and `x: *a` is an error to PyYAML
#   !tag              `!!str 7` is the string "7" to PyYAML, "!!str 7" here
#   | >               block scalar headers; `title: |` opens a folded value
#                     PyYAML reads from the following lines
#   % @ ` ,           reserved indicators PyYAML refuses outright
#
# Only as the first character. `tail -f x | grep y`, `user@host`, `50%` and
# `sleep 5 &` are ordinary values that both backends read identically, and
# rejecting those would break real configs to close a hole they are not in.
# `]` and `}` are here rather than in _reject_unbalanced_flow because a
# value may not OPEN with a closing bracket: PyYAML refuses `a: ]x` as a
# stray flow indicator, and nothing legitimate starts that way.
# `?` and `:` join them as the FIRST character of a plain scalar: PyYAML
# reads them as the complex-key and value indicators and refuses `a: ?x`
# and `a: :x`, while the built-in parser read both as ordinary text, so
# the config loaded only where PyYAML was absent.
_YAML_INDICATOR_CHARS = "&*!%@`|>,]}?:"

# Whole tokens PyYAML refuses as a VALUE and the built-in parser reads as
# ordinary text. Both are YAML 1.1 spellings rather than indicators, so they
# are matched exactly: `<<x`, `<x` and `=x` are ordinary values that both
# backends agree on, and only the bare token diverges.
_YAML_RESERVED_VALUE_TOKENS = ("<<", "=")

# A key PyYAML gives meaning to and the built-in parser treats as an ordinary
# name. `<<` is the merge key: PyYAML folds the mapping it names into the
# enclosing one, so a profile written with `<<: {prefix: x-}` HAS that prefix
# on a machine with PyYAML and does not have it on a machine without, with no
# error on either side. That silence is what makes it worth naming here.
_YAML_RESERVED_KEYS = ("<<",)

# Document markers. PyYAML accepts them; the built-in parser has no concept of
# a document and reports "Malformed config line", so a config carrying the
# `---` header that many editors and people write by habit loads on a machine
# with PyYAML and fails on one without -- exactly the machines xtm exists for.
_YAML_DOCUMENT_MARKERS = ("---", "...")


def _reject_ambiguous_scalar(tok: str, context: str,
                             in_flow: bool = False) -> None:
    """Refuse one unquoted scalar that the two YAML backends read differently.

    Quoted scalars are exempt: quoting is exactly how a user says "this is
    the literal string", and both backends agree on quoted text.
    """
    raw = tok.strip()
    if not raw:
        return
    if raw[0] == '"':
        _reject_unsupported_escape(raw, context)
        _reject_control_characters(raw, context)
        return
    if raw[0] == "'":
        _reject_control_characters(raw, context)
        return
    if in_flow:
        _reject_flow_indicator_in_scalar(raw, context)
        if raw.startswith("- "):
            # A bare "-" is the string "-" to both backends and stays
            # legal; "- x" is a block sequence item, which PyYAML refuses
            # inside a flow collection while the built-in parser read it
            # as text.
            die("%s: the value %s inside a flow collection begins with a "
                "block sequence indicator, which PyYAML refuses there while "
                "the built-in parser reads it as text. Quote it if the dash "
                "is meant literally." % (context, raw))
    if raw in _YAML_RESERVED_VALUE_TOKENS:
        die("%s: the unquoted value %s is reserved in YAML, so PyYAML "
            "refuses it while the built-in parser reads it as text and this "
            "config would load only where PyYAML is absent. Quote it as "
            "\"%s\" if you mean the literal text." % (context, raw, raw))
    if raw[0] in _YAML_INDICATOR_CHARS:
        die("%s: the unquoted value %s begins with %r, which YAML reads as a "
            "structural indicator rather than as text, so this config would "
            "be read differently depending on whether PyYAML is installed. "
            "Quote it as \"%s\" if you mean the literal text."
            % (context, raw, raw[0], raw))
    _reject_control_characters(raw, context)
    if raw.lower() in _YAML_AMBIGUOUS_WORDS:
        die("%s: the unquoted value %s is read as a boolean by PyYAML and as "
            "text by the built-in parser, so this config would behave "
            "differently depending on whether PyYAML is installed. Quote it "
            "as \"%s\" if you mean the word." % (context, raw, raw))
    if _has_digit_separators(raw):
        die("%s: the value %s has digit separators, which only PyYAML "
            "understands, so this config would be read differently depending "
            "on whether PyYAML is installed. Write it as a plain decimal "
            "number, or quote it as \"%s\" if you mean the literal text."
            % (context, raw, raw))
    for pattern, reason in _YAML_AMBIGUOUS_PATTERNS:
        if pattern.match(raw):
            die("%s: the value %s has %s, so this config would be read "
                "differently depending on whether PyYAML is installed. Write "
                "it as a plain decimal number, or quote it as \"%s\" if you "
                "mean the literal text." % (context, raw, reason, raw))


def _scan_for_ambiguous_scalars(text: str) -> None:
    """Check every unquoted value in a config for cross-backend ambiguity.

    Runs on the raw text before either backend parses it, which is the only
    point where the original spelling is still available: once PyYAML has
    turned 0755 into 493 the evidence is gone.
    """
    for lineno, raw_line in enumerate(text.split("\n"), 1):
        stripped = _strip_inline_comment(raw_line)
        line = stripped.strip()
        if not line or line.startswith("#"):
            continue
        context = "%s line %d" % (CONTEXT.config_file, lineno)
        # The built-in parser's own indentation rules, enforced here so they
        # apply under PyYAML too. They were only in _simple_yaml_load, so a
        # tab-indented or three-space-indented config loaded on a machine
        # with PyYAML and failed on one without -- the same divergence as any
        # other in this scan, just spelled with whitespace.
        _reject_ambiguous_indent(stripped, raw_line, context)
        if line in _YAML_DOCUMENT_MARKERS or line.startswith("--- "):
            die("%s: %s is a YAML document marker. PyYAML accepts it and the "
                "built-in parser does not, so this config would load only on "
                "a machine with PyYAML installed. Remove the line; xtm's "
                "config is always a single document."
                % (context, line.split()[0]))
        if line == "-" or line.startswith("- "):
            # Block sequence item: the whole remainder is the value.
            item = line[1:].strip()
            _, item_sep, _ = _partition_key(item)
            if item_sep and not item.startswith(("[", "{", '"', "'")):
                die("%s: %r opens a mapping inside a list item. PyYAML reads "
                    "it as one and the built-in parser has no nested mapping "
                    "to read it into, so the two disagree. xtm's config has "
                    "no use for it: write the mapping under its own key."
                    % (context, line))
            _scan_value(item, context)
            continue
        key, _, value = _partition_key(line)
        _reject_reserved_key(key.strip(), context)
        _scan_value(value.strip(), context)


def _reject_ambiguous_indent(line: str, raw_line: str, context: str) -> None:
    """Refuse indentation the built-in parser cannot read.

    Deliberately the same two rules _simple_yaml_load applies, stated once
    more here so that they gate PyYAML as well. Both messages name the
    offending line, and both have the same fix: use two spaces per level.
    """
    # Anywhere in the line, not only in the indentation. PyYAML refuses a
    # tab inside a plain scalar too ("a: 1\t2"), while the built-in parser
    # reads it as ordinary text, so restricting the rule to leading
    # whitespace left the same divergence in the middle of a value.
    if "\t" in line:
        die("%s: config lines must not contain tabs, because the built-in "
            "YAML parser cannot handle them reliably. Use spaces: %r"
            % (context, raw_line))
    leading = line[:len(line) - len(line.lstrip(" "))]
    if len(leading) % 2 != 0:
        die("%s: config indentation must be in multiples of 2 spaces, or the "
            "built-in YAML parser cannot read it: %r" % (context, raw_line))


def _reject_flow_item(item: str, whole: str, context: str) -> None:
    """Refuse a flow-sequence item the two backends read differently.

    Two shapes, both refused for both backends so that neither gets to act
    on its own reading:

    * An empty item, which a trailing comma produces. `[a, b,]` is a
      two-item list to PyYAML and a three-item list ending in null to the
      built-in parser.
    * An unquoted item containing a colon. PyYAML may read it as a nested
      one-key mapping (`[b40: 2]` is `[{"b40": 2}]`) or as text depending
      on what follows the colon, and the built-in parser has no nested
      mapping to read it into.

    Quoted items are exempt, which is what keeps real values working:
    `xterm_args: [-fa, "Monospace:size=11"]` is a fontconfig name, not a
    mapping, and quoting is how the user already says so.
    """
    if item.startswith("- "):
        # A bare "-" stays legal: PyYAML reads `[-]` as the one-item list
        # ["-"], and so does the built-in parser.
        die("%s: the list item %s begins with a block sequence indicator, "
            "which PyYAML refuses inside a flow sequence while the built-in "
            "parser reads it as text. Write the item as plain text, or quote "
            "it." % (context, item))
    if not item:
        die("%s: the list %s has an empty item, usually a trailing comma. "
            "PyYAML drops it and the built-in parser reads it as a null "
            "entry, so the two disagree about the list's length."
            % (context, whole))
    if item[0] in "\"'[{":
        return
    _, sep, _ = _partition_key(item)
    # endswith(":") as well as a colon-plus-space: a trailing colon is
    # still a key to PyYAML, which reads `[a:]` as a one-item list holding
    # the mapping {"a": None}, while the built-in parser refuses it. Only
    # _partition_key's "colon then space" spelling was checked, so the
    # trailing one reached the loader and the two disagreed.
    if sep or item.endswith(":"):
        die("%s: the list item %s contains a colon, which PyYAML may read "
            "as a nested mapping rather than as text. Quote it as \"%s\" if "
            "it is meant to be text." % (context, item, item))


def _reject_flow_entry(part: str, sep: str, value: str, context: str) -> None:
    """Refuse a flow-mapping entry the two backends split differently.

    In YAML a colon separates a key from its value only when a space or the
    end of the entry follows it, so PyYAML reads `{x:0, y:0}` as two keys
    named "x:0" and "y:0" with no values at all. The built-in parser split
    on the first colon regardless, read the same text as x=0 and y=0, and
    placed the window where PyYAML would have failed validation.

    An entry with no colon (`{a}`) is the other half: a valid YAML set
    entry to PyYAML, and nothing the built-in parser can represent. Neither
    spelling has a use in an xtm config, and both have an obvious fix.
    """
    if not part:
        die("%s: the flow mapping has an empty entry, usually a stray or "
            "trailing comma. PyYAML refuses it and the built-in parser drops "
            "it, so the two disagree about the mapping's contents."
            % (context,))
    if not sep:
        die("%s: the flow mapping entry %s has no value. Write it as "
            "'key: value'." % (context, part))
    if value and not value.startswith((" ", "\t")):
        die("%s: the flow mapping entry %s has no space after its colon, so "
            "PyYAML reads the whole entry as a key with no value while the "
            "built-in parser splits it in two. Write it as 'key: value'."
            % (context, part))


# Which indicators may open a KEY depends on where the key sits. PyYAML
# reads `:x: 1` and `?x: 1` as ordinary block keys and refuses neither, so
# refusing them here would make xtm stricter than PyYAML in block context.
# Inside a flow mapping the same two are structure: `{:x: 1}` is refused
# outright and `{?x: 1}` becomes the key "x" with the indicator eaten,
# which is a silent disagreement with the built-in parser's "?x".
# `[` and `{` are here as well as `]` and `}`: a key that OPENS a flow
# collection is structure to PyYAML, which refuses `{<+/A: 1}` as a key,
# while the built-in parser read it as an ordinary name.
_YAML_BLOCK_KEY_INDICATORS = "&*!%@`|>,[]{}"

# Spellings YAML gives a type rather than a name. A numeric key is absent
# on purpose: PyYAML's 2 and the built-in parser's "2" both normalise to
# the name "2", while PyYAML's None and True normalise to "None" and
# "True" against the built-in parser's "~" and "true".
_YAML_TYPED_KEY_SPELLINGS = ("~", "null", "Null", "NULL",
                             "true", "True", "TRUE",
                             "false", "False", "FALSE")


def _reject_reserved_key(key: str, context: str,
                         in_flow: bool = False) -> None:
    """Refuse a key that only PyYAML gives meaning to.

    Quoted keys are exempt for the same reason quoted values are: quoting
    is how a user says "this is a name", and both backends agree on it.
    """
    if key[:1] in "\"'":
        # A quoted key must close on its last character and carry nothing
        # after it, exactly as a quoted value must: PyYAML refuses
        # `""!||90:` and the built-in parser read it as an ordinary name.
        _reject_unbalanced(key)
        if key[:1] == '"':
            # And it carries escapes, exactly as a quoted value does. This
            # rule used to apply to values only, so `"a\nb":` was a name
            # with a real newline under PyYAML and the literal four
            # characters without it -- two profile names, no error either
            # way.
            _reject_unsupported_escape(key, context)
        return
    if in_flow:
        # Inside a flow mapping a bracket or brace ends the key, so
        # PyYAML refuses `{1a{]: 1}` while the built-in parser read the
        # whole thing as a name.
        _reject_flow_indicator_in_scalar(key, context)
    indicators = (_YAML_INDICATOR_CHARS if in_flow
                  else _YAML_BLOCK_KEY_INDICATORS)
    if key[:1] in indicators:
        die("%s: the key %s begins with %r, which YAML reads as a structural "
            "indicator rather than as a name. PyYAML refuses it and the "
            "built-in parser reads it as an ordinary key. Quote it as \"%s\" "
            "if that really is the name." % (context, key, key[0], key))
    if key in _YAML_RESERVED_KEYS:
        die("%s: %s is a YAML merge key. PyYAML merges the mapping it names "
            "into this one and the built-in parser treats it as an ordinary "
            "key, so this config would mean different things on two machines "
            "with no error on either. Write the keys out in full."
            % (context, key))
    # The ambiguity rules apply to a key as much as to a value: `yes:` is
    # the key True to PyYAML and the key "yes" to the built-in parser, and
    # _normalize_config_keys turns those into "True" and "yes" -- two
    # different profile names. A purely NUMERIC key needs no rule here,
    # because that same normalisation makes 2 and "2" the same name.
    _reject_ambiguous_scalar(key, context, in_flow)
    _reject_renumbered_key(key, context)
    if key in _YAML_TYPED_KEY_SPELLINGS:
        die("%s: the key %s is a YAML null or boolean rather than a name. "
            "PyYAML reads it as that value and the built-in parser reads "
            "the word, so the two end up naming different things. Quote it "
            "as \"%s\"." % (context, key, key))


def _reject_renumbered_key(key: str, context: str) -> None:
    """Refuse a numeric key whose text changes when YAML reads it.

    `123:` is safe: PyYAML types it as the integer 123, the built-in
    parser keeps the text, and _normalize_config_keys turns both into the
    name "123". `7.:` is not: PyYAML reads the float 7.0 and normalises
    to "7.0" where the built-in parser normalises to "7.", so the same
    line names two different profiles.

    The test is exactly that -- whether the name survives the round trip
    -- rather than "is it numeric", so the ordinary numeric names people
    actually use keep working.
    """
    # Ask YAML's number syntax, not Python's. float() accepts "infinity",
    # "inf", "nan" and "1e3"; YAML reads none of those as a number in key
    # position, so asking Python refused names both backends agree on --
    # which is the same defect as accepting what they disagree on, pointing
    # the other way.
    if not _YAML_NUMBER_RE.match(key):
        return
    for parse in (int, float):
        try:
            parsed = parse(key)
        except ValueError:
            continue
        if str(parsed) != key:
            die("%s: the key %s is a number whose text changes when YAML "
                "reads it -- PyYAML makes it %s and the built-in parser "
                "keeps %s, so the two name different things. Quote it as "
                "\"%s\"." % (context, key, parsed, key, key))
        return


def _reject_flow_indicator_in_scalar(raw: str, context: str) -> None:
    """Refuse a plain scalar inside [ ] or { } that carries an indicator.

    Inside a flow collection a bracket or a brace ends the scalar, so
    PyYAML refuses `[a{b]` and `{k: a]b}` outright while the built-in
    parser splits only on the top-level commas and hands the text back.
    In BLOCK context the same characters are ordinary -- `title: 3{x}`
    is a perfectly good value -- which is why this rule applies only
    where the scan is already inside a flow collection.

    Commas and quotes are deliberately absent: PyYAML reads `[a"b]` as
    the one-item list `['a"b']` and treats a comma as a separator, so
    refusing either here would make the built-in parser stricter than
    PyYAML, which is the same divergence pointing the other way.
    """
    for ch in raw:
        if ch in "[]{}":
            die("%s: the value %s inside a flow collection contains %r, "
                "which YAML reads as structure there rather than as text. "
                "PyYAML refuses it and the built-in parser reads it as "
                "text, so the two disagree. Quote the whole value, or move "
                "it onto its own line." % (context, raw, ch))


def _reject_control_characters(raw: str, context: str) -> None:
    """Refuse a raw control character the two backends do not agree on.

    A newline and a tab have escapes on both sides; nothing else below
    space does. Written raw into a quoted value the built-in reader takes
    it back happily and PyYAML refuses the file, so a config saved with
    one loads on half the machines it is copied to.
    """
    for char in raw:
        if char < " " and char not in "\n\t":
            die("%s: the value %r contains the control character %r, which "
                "the two YAML readers do not agree on and neither can write "
                "back safely. Remove it, or write it as text."
                % (context, raw, char))


def _reject_unsupported_escape(raw: str, context: str) -> None:
    """Refuse an escape in a double-quoted scalar that the two backends
    read differently.

    A double-quoted scalar is the one quoted form YAML processes escapes
    in, so it is the one place where quoting does NOT mean the two
    backends agree: PyYAML turns `"a\\tb"` into a real tab and the
    built-in loader, which understands only `\\"` and `\\\\`, keeps the
    backslash and the letter. Neither reports anything, so the same
    config placed a differently-titled window on two machines. PyYAML
    also refuses an escape YAML does not define (`"\\z"`) while the
    built-in loader accepts it.

    Single quotes have no escapes at all in YAML, which is why they are
    the fix this message names.
    """
    index = 1
    while index < len(raw) - 1:
        if raw[index] != "\\":
            index += 1
            continue
        following = raw[index + 1:index + 2]
        if following not in _YAML_ESCAPES:
            die("%s: the double-quoted value %s contains the escape %s, "
                "which PyYAML interprets and the built-in parser does not, "
                "so this config would mean different things depending on "
                "whether PyYAML is installed. Use single quotes if the "
                "backslash is meant literally."
                % (context, raw, "\\" + following if following else "\\"))
        index += 2


def _scan_value(value: str, context: str, in_flow: bool = False) -> None:
    """Check one value, descending into flow collections."""
    if not value:
        return
    if value.startswith("{") and value.endswith("}"):
        inner_text = value[1:-1].strip()
        for part in ([] if not inner_text else _split_top_level(inner_text)):
            key, sep, inner = _partition_key(part.strip())
            _reject_flow_entry(part.strip(), sep, inner, context)
            if part.strip():
                _reject_reserved_key(key.strip(), context, in_flow=True)
                _scan_value(inner.strip(), context, in_flow=True)
        return
    if value.startswith("[") and value.endswith("]"):
        inner = value[1:-1].strip()
        for part in ([] if not inner else _split_top_level(inner)):
            _reject_flow_item(part.strip(), value, context)
            _scan_value(part.strip(), context, in_flow=True)
        return
    _reject_ambiguous_scalar(value, context, in_flow)


def _partition_key(line: str) -> Tuple[str, str, str]:
    """Split "key: value" at the first colon that is OUTSIDE quotes.

    str.partition(":") is not good enough: a quoted key such as
    "bad:name": {...} would be cut in half, yielding the key '"bad'. That
    is a third way for the two backends to disagree, since PyYAML honours
    the quotes. Returns (key, separator, value), with an empty separator
    when the line holds no top-level colon at all.
    """
    quote = ""
    for i, ch in enumerate(line):
        if quote:
            if ch == quote:
                quote = ""
            continue
        # Only a quote that OPENS the key is a quote. Mid-key it is an
        # ordinary character in a plain scalar -- PyYAML reads `4~x'5: {}`
        # as the name "4~x'5" -- and treating it as a delimiter swallowed
        # the colon that follows, so the built-in parser reported a
        # malformed line for a config PyYAML reads.
        if ch in "\"'" and i == 0:
            quote = ch
            continue
        # A colon separates a key from a value only when a space or the
        # end of the line follows it. `119%:~5: {}` is one name to
        # PyYAML, not the key `119%`, and splitting at every colon made
        # the built-in parser read a different key -- or refuse the line
        # outright -- for a config PyYAML reads without complaint.
        if ch == ":" and line[i + 1:i + 2] in ("", " "):
            return line[:i], ":", line[i + 1:]
    return line, "", ""


def _reject_unbalanced(tok: str) -> None:
    """Refuse a quoted scalar or flow collection that does not close cleanly.

    Only a token that OPENS with a quote or a bracket is one, which is the
    whole subtlety here. In YAML those characters are indicators at the
    start of a scalar and ordinary text anywhere else, so `command: sed
    's/a/b/'` and `title: 3{x}` are perfectly good plain scalars that
    PyYAML reads without complaint. Scanning every token for balance
    instead made the built-in parser STRICTER than PyYAML and produced the
    same divergence from the other direction: a config that loaded with
    PyYAML installed and failed without it.

    What is refused is a token that announces a quoted scalar or a flow
    collection and then does not deliver one: `a: "unterminated`,
    `a: {x: 1, y: 2`, `a: []1A`. PyYAML refuses all three.
    """
    if not tok:
        return
    first = tok[0]
    if first in "\"'":
        _reject_unterminated_quote(tok)
        return
    if first in "[{":
        _reject_unbalanced_flow(tok)


def _reject_unterminated_quote(tok: str) -> None:
    """Check a token that opens with a quote: it must close on the last
    character, with nothing after it."""
    quote = tok[0]
    index = 1
    while index < len(tok):
        ch = tok[index]
        if quote == '"' and ch == "\\":
            index += 2      # an escape consumes the character after it
            continue
        if ch == quote:
            if quote == "'" and tok[index + 1:index + 2] == "'":
                index += 2  # '' is how a single-quoted scalar escapes a quote
                continue
            break
        index += 1
    else:
        return die("Unterminated %s quote in config value: %s" % (quote, tok))
    if index != len(tok) - 1:
        die("Unexpected text after the closing %s quote in config value: %s"
            % (quote, tok))


def _reject_unbalanced_flow(tok: str) -> None:
    """Check a token that opens with `[` or `{`: it must be one collection,
    closed by the last character."""
    closing = {"[": "]", "{": "}"}[tok[0]]
    quote = ""
    depth = 0
    # A quote only opens a quoted scalar where an item may begin -- after
    # "[", "{", "," or a key's ": ", and any spaces. Elsewhere it is an
    # ordinary character in a plain scalar, which is why `[a"b]` is a
    # one-item list to PyYAML rather than an unterminated quote.
    #
    # The colon belongs in that set because a mapping's VALUE begins after
    # it: leaving it out made `{title: "v}w"}` read as plain text, counted
    # the brace inside the quotes as structure, and refused a config
    # PyYAML accepts -- one that would then load only where PyYAML is
    # installed. It counts only when a space or the end of the token
    # follows, which is exactly when YAML treats it as a separator rather
    # than as a character in a plain scalar.
    at_item_start = True
    for index, ch in enumerate(tok):
        if quote:
            if ch == quote:
                quote = ""
            continue
        if ch in "\"'" and at_item_start:
            quote = ch
        elif ch in "[{":
            depth += 1
        elif ch in "]}":
            depth -= 1
            if depth == 0 and index != len(tok) - 1:
                die("Unexpected text after the closing %s in config value: %s"
                    % (ch, tok))
            if depth < 0:
                die("Unbalanced brackets or braces in config value: %s" % (tok,))
        if ch not in " \t":
            at_item_start = (ch in "[{,"
                             or (ch == ":"
                                 and tok[index + 1:index + 2] in ("", " ")))
    if quote:
        die("Unterminated %s quote in config value: %s" % (quote, tok))
    if depth or tok[-1] != closing:
        die("Unbalanced brackets or braces in config value: %s" % (tok,))


def _reject_plain_scalar_colon(tok: str, in_flow: bool = False) -> None:
    """Refuse an unquoted value that YAML would read as a nested mapping.

    In YAML a colon only ends a key when it is followed by whitespace or by
    the end of the line, so `ssh host:port` and `http://example/x` are
    perfectly ordinary values -- but `a: some value:` is a syntax error,
    because that trailing colon looks like the start of a nested mapping.
    PyYAML raises on it; the built-in parser used to hand back the string
    "some value:" instead.

    Only reached for plain scalars: quoted values and flow collections are
    handled by the branches above, and a flow mapping legitimately contains
    ": " everywhere.
    """
    if tok.endswith(":") or ": " in tok:
        die("Unquoted value %r contains a colon followed by a space or the "
            "end of the value, which YAML reads as the start of a nested "
            "mapping. Quote it if it is meant to be text." % (tok,))
    # "- " opens a block sequence, which cannot begin on the value line.
    # A bare "-fn" is an ordinary value and is deliberately unaffected: only
    # a dash followed by a space, or a dash alone, is the list indicator.
    #
    # in_flow suppresses this rule, because inside [ ] a dash is not an
    # indicator at all: PyYAML reads `a: [-]` as the one-item list ["-"],
    # and refusing it here made the built-in parser reject a config PyYAML
    # accepts -- the same divergence, pointing the other way.
    if in_flow:
        return
    if tok == "-" or tok.startswith("- "):
        die("Unquoted value %r starts with a list indicator, which YAML "
            "cannot read on the same line as its key. Put the list items on "
            "their own lines, or quote the value if it is meant to be text."
            % (tok,))


def _simple_yaml_scalar_load(tok: str, in_flow: bool = False) -> Any:
    """Parse one scalar, flow list or flow mapping produced by hand or by
    _simple_yaml_dump.

    `in_flow` says the token came from inside a flow collection, where a
    leading dash is ordinary text rather than a list indicator.
    """
    tok = tok.strip()
    _reject_unbalanced(tok)
    if tok in ("", "~", "null", "Null", "NULL"):
        return None
    if tok in ("true", "True", "TRUE"):
        return True
    if tok in ("false", "False", "FALSE"):
        return False
    if len(tok) >= 2 and tok[0] == tok[-1] == '"':
        return _unescape_double_quoted(tok[1:-1])
    if len(tok) >= 2 and tok[0] == tok[-1] == "'":
        return tok[1:-1].replace("''", "'")
    if tok.startswith("{") and tok.endswith("}"):
        return _simple_yaml_flow_mapping_load(tok)
    if tok.startswith("[") and tok.endswith("]"):
        inner = tok[1:-1].strip()
        if not inner:
            return []
        return [_simple_yaml_scalar_load(p, in_flow=True)
                for p in _split_top_level(inner)]
    _reject_plain_scalar_colon(tok, in_flow)
    # [0-9] rather than \d, and \Z rather than $, both deliberately. In
    # Python 3 \d also matches non-ASCII decimal digits and int() accepts
    # them, so "\uff11\uff12\uff13" would become 123 here while PyYAML --
    # which takes only ASCII digits -- keeps it a string. And $ also matches
    # just before a trailing newline, so "12\n" would parse as 12.
    if re.match(r"^[-+]?[0-9]+\Z", tok):
        return int(tok)
    # The sign placement mirrors PyYAML's float grammar exactly: it allows a
    # sign on "1.5" but not on ".5", so "+.5" and "-.5" are strings to it.
    # Accepting them as floats here would put the divergence back.
    if re.match(r"^([-+]?[0-9]+\.[0-9]*|\.[0-9]+)([eE][-+][0-9]+)?\Z", tok):
        return float(tok)
    return tok


# The escapes a double-quoted scalar may carry. PyYAML implements far more
# (\u, \x, \0 and the rest); those stay refused, because the built-in reader
# would hand back the text and the two backends would disagree in silence.
# These four are implemented on both sides, and the dumper needs the last two:
# a newline or a tab written literally would split the line.
_YAML_ESCAPES = {'"': '"', "\\": "\\", "n": "\n", "t": "\t"}


def _unescape_double_quoted(body: str) -> str:
    """Read one double-quoted scalar's body, resolving the escapes YAML
    defines and this reader implements.

    A single left-to-right pass rather than chained replaces: `"a\\\\nb"` is a
    backslash followed by an n, and replacing `\\n` first would turn it into a
    newline.
    """
    out = []
    index = 0
    while index < len(body):
        char = body[index]
        if char != "\\" or index + 1 >= len(body):
            out.append(char)
            index += 1
            continue
        following = body[index + 1]
        # An escape the guard refuses cannot reach here; anything that does
        # is one of the four, and is consumed with the backslash.
        out.append(_YAML_ESCAPES.get(following, "\\" + following))
        index += 2
    return "".join(out)


def _simple_yaml_key_load(tok: str) -> str:
    """Read one mapping key, which is a name rather than a value.

    A key has already been separated from its value by _partition_key, so
    what remains is text: `oo@>:` is a perfectly good name to PyYAML, and
    running the value rules over it refused names PyYAML accepts. Only
    the quoting is interpreted here, because quoting is how a user says
    where a name begins and ends.

    Everything a key is looked up by is a string in xtm, and
    _normalize_config_keys forces PyYAML's typed keys back to str, so
    returning the text is also what makes the two backends agree.
    """
    tok = tok.strip()
    if len(tok) >= 2 and tok[0] == tok[-1] == '"':
        # The same unescaper the value path uses. A second implementation
        # here is what let a quoted key resolve a different escape set from
        # the value beside it.
        return _unescape_double_quoted(tok[1:-1])
    if len(tok) >= 2 and tok[0] == tok[-1] == "'":
        return tok[1:-1].replace("''", "'")
    return tok


def _simple_yaml_flow_mapping_load(tok: str) -> Dict[str, Any]:
    """Parse a flow-style mapping such as {x: 0, y: 0, width: 900}.

    Supported because the documented config examples use this compact
    form, and because PyYAML accepts it; without it, a config that works
    on a machine with PyYAML installed would fail on one without it.
    """
    inner = tok[1:-1].strip()
    result = {}  # type: Dict[str, Any]
    if not inner:
        return result
    for part in _split_top_level(inner):
        part = part.strip()
        if not part:
            continue
        key, sep, value = _partition_key(part)
        if not sep:
            die("Malformed flow mapping entry %r in config." % (part,))
        key = _simple_yaml_key_load(key)
        # in_flow for the same reason the sequence branch passes it: inside
        # a flow collection a leading dash is ordinary text, not the block
        # sequence indicator, and PyYAML reads `{title: -}` as "-".
        result[str(key)] = _simple_yaml_scalar_load(value, in_flow=True)
    return result


def _simple_yaml_load(text: str) -> Dict[str, Any]:
    """Minimal indentation-based YAML reader matching _simple_yaml_dump's
    output, plus inline comments and flow-style collections.

    Only mappings of mappings of scalars/flow-collections are supported,
    which is all xtm's config ever needs. A bare "key:" with nothing after
    it is a null value, which is what YAML says it is and what PyYAML
    returns. Reading it as an empty mapping instead made `empty:` a valid
    empty profile on a machine without PyYAML and a fatal "must be a
    mapping" on one with it, so the same config file was usable on one
    host and not the other.
    """
    lines = []  # type: List[Tuple[int, str]]
    for raw in text.split("\n"):
        line = _strip_inline_comment(raw).rstrip()
        if not line.strip():
            continue
        leading = line[:len(line) - len(line.lstrip(" \t"))]
        if "\t" in leading:
            die("Config indentation must use spaces, not tabs, because the "
                "built-in YAML parser cannot handle tabs reliably: %r" % (raw,))
        indent = len(leading)
        if indent % 2 != 0:
            die("Config indentation must be in multiples of 2 spaces: %r" % (raw,))
        lines.append((indent // 2, line.strip()))

    root = {}  # type: Dict[str, Any]
    stack = [(-1, root)]  # type: List[Tuple[int, Any]]
    # A "key:" with nothing after it cannot be classified from its own line:
    # it may open a nested mapping, open a block sequence, or have no children
    # at all. It is therefore held here until the following line settles it.
    pending = None  # type: Optional[Tuple[int, Dict[str, Any], str]]

    def settle_pending_as_null() -> None:
        """A bare "key:" with no children is null, as PyYAML reads it."""
        if pending is not None:
            pending[1][pending[2]] = None

    for indent, content in lines:
        is_item = content == "-" or content.startswith("- ")

        if is_item:
            if pending is not None and indent >= pending[0]:
                # First item of a block sequence. Note the container is
                # pushed at the KEY's level, not the item's, so that items
                # sitting at either the key's indent (what PyYAML writes) or
                # one level deeper (what people write by hand) both land in
                # it.
                new_list = []  # type: List[Any]
                pending[1][pending[2]] = new_list
                stack.append((pending[0], new_list))
                pending = None
            while len(stack) > 1 and (stack[-1][0] > indent
                                      or not isinstance(stack[-1][1], list)):
                stack.pop()
            container = stack[-1][1]
            if not isinstance(container, list):
                die("Config has a list item that belongs to no list: %r"
                    % (content,))
            container.append(_simple_yaml_scalar_load(content[1:].strip()))
            continue

        if pending is not None:
            if indent > pending[0]:
                nested = {}  # type: Dict[str, Any]
                pending[1][pending[2]] = nested
                stack.append((pending[0], nested))
            else:
                settle_pending_as_null()
            pending = None

        while len(stack) > 1 and stack[-1][0] >= indent:
            stack.pop()
        parent = stack[-1][1]
        if not isinstance(parent, dict):
            die("Config mixes a mapping into a list: %r" % (content,))
        key, sep, rest = _partition_key(content)
        if not sep:
            die("Malformed config line: %r" % (content,))
        key = _simple_yaml_key_load(key)
        rest = rest.strip()
        if rest == "":
            pending = (indent, parent, key)
        else:
            parent[key] = _simple_yaml_scalar_load(rest)

    settle_pending_as_null()
    return root


# Bare words that would be read back as non-string YAML types, so the
# dependency-free dumper must quote them when they occur as strings (a
# font literally named "Null" has to round-trip as the string "Null",
# not as Python None).
_YAML_RESERVED_WORDS = {
    "true", "True", "TRUE", "false", "False", "FALSE",
    "null", "Null", "NULL", "None", "~",
    "yes", "Yes", "YES", "no", "No", "NO",
    "on", "On", "ON", "off", "Off", "OFF",
}
_YAML_LOOKS_NUMERIC_RE = re.compile(r"^-?\d+(\.\d+)?$")


def _would_be_refused_unquoted(s: str) -> bool:
    """Whether the parity guard would refuse `s` as an unquoted scalar.

    Defined here and used by the dumper so that the rule lives in exactly
    one place. _reject_ambiguous_scalar is defined further down the file;
    Python resolves the name at call time, and the dumper never runs at
    import time, so the forward reference is fine.
    """
    try:
        _reject_ambiguous_scalar(s, "dump")
    except XtmError:
        return True
    return False


def _reads_back_as_a_different_number(s: str) -> bool:
    """Whether YAML would read `s` as a number that prints differently."""
    for parse in (int, float):
        try:
            return str(parse(s)) != s
        except ValueError:
            continue
    return False


def _simple_yaml_scalar_dump(v: Any) -> str:
    """Render one scalar for the dependency-free dumper, quoting anything
    that would not survive the round trip unquoted."""
    if v is None:
        return "null"
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return str(v)
    s = str(v)
    needs_quoting = (
        s == ""
        or s in _YAML_RESERVED_WORDS
        or bool(_YAML_LOOKS_NUMERIC_RE.match(s))
        # A spelling YAML reads as a number whose TEXT differs -- "9." is
        # the float 9.0, "07" the integer 7. As a value the difference is
        # invisible; as a key it renames the profile or slot, which is why
        # the guard refuses it. Quoting here is what stops xtm writing a
        # config its own guard would then refuse to read.
        or _reads_back_as_a_different_number(s)
        or bool(re.search(r'[:#\[\]{},"\']', s))
        # Any control character, not the two that have escapes: a raw one
        # inside the quotes is what the guard above refuses on the way back
        # in, so writing it would produce a file xtm could not read.
        or any(c < " " for c in s)
        or s.strip() != s
        or s.startswith(("-", "?", "&", "*", "!", "%", "@", "`", ">", "|"))
        # The decisive test, and the reason the list above is only a fast
        # path: ask the parity guard itself whether it would refuse this
        # text unquoted. Keeping a second list of what needs quoting is how
        # the dumper came to write `display: :0` and `title: 7_`, both of
        # which the guard then refused -- a saved config that would not load
        # again. One rule, consulted from both sides, cannot drift.
        or _would_be_refused_unquoted(s)
    )
    if needs_quoting:
        # A newline or a tab has to leave as an escape. Written literally
        # inside the quotes it splits the line, and the reader -- which is
        # line-based -- then refuses the file the dumper just wrote. The
        # guard refuses every input that could carry one today, so this is
        # the second rule keeping that true rather than the first.
        escaped = (s.replace("\\", "\\\\").replace('"', '\\"')
                    .replace("\n", "\\n").replace("\t", "\\t"))
        return '"' + escaped + '"'
    return s


def _simple_yaml_dump(data: Dict[str, Any], indent: int = 0) -> str:
    """Minimal YAML writer for the shape xtm's config actually uses:
    nested mappings of scalars, with flow-style lists as values."""
    lines = []
    pad = "  " * indent
    for key, value in data.items():
        key_text = _simple_yaml_scalar_dump(str(key))
        if isinstance(value, dict):
            if not value:
                lines.append("%s%s: {}" % (pad, key_text))
            else:
                lines.append("%s%s:" % (pad, key_text))
                lines.append(_simple_yaml_dump(value, indent + 1))
        elif isinstance(value, (list, tuple)):
            items = ", ".join(_simple_yaml_scalar_dump(v) for v in value)
            lines.append("%s%s: [%s]" % (pad, key_text, items))
        else:
            lines.append("%s%s: %s" % (pad, key_text, _simple_yaml_scalar_dump(value)))
    return "\n".join(lines)


def dump_yaml(data: Dict[str, Any]) -> str:
    """Serialise the config, preferring PyYAML when it is both present
    and new enough.

    PyYAML gained safe_dump(sort_keys=...) in 5.1; older releases (still
    shipped by long-lived enterprise distributions) raise TypeError on
    it. Without sort_keys=False the config would be rewritten in
    alphabetical order on every save, so rather than accept that churn,
    fall back to the built-in dumper, which is always available and
    always preserves order.
    """
    if HAVE_PYYAML:
        try:
            text = _pyyaml.safe_dump(data, sort_keys=False,
                                     default_flow_style=False)
        except TypeError:
            logger.debug("PyYAML is too old for sort_keys=; using the "
                         "built-in dumper to preserve config key order.")
        else:
            # PyYAML quotes what PyYAML would misread, which is not the same
            # set as what the two backends would read differently: it writes
            # `display: :0` unquoted, and the parity guard then refuses the
            # file on the way back in. Saving a config must never produce one
            # xtm will not load, so the output is checked and the built-in
            # dumper -- which quotes for the guard's rules -- is used instead
            # when it would not survive.
            try:
                _scan_for_ambiguous_scalars(text)
                return text
            except XtmError as e:
                logger.debug("PyYAML's output would not survive the config "
                             "guard (%s); using the built-in dumper.", e)
    return _simple_yaml_dump(data) + "\n"


def load_yaml(text: str) -> Dict[str, Any]:
    """Parse config text with PyYAML when available, else the built-in
    reader."""
    # Before either backend, so both reject the same inputs. See
    # _scan_for_ambiguous_scalars for why this cannot run afterwards.
    _scan_for_ambiguous_scalars(text)
    if HAVE_PYYAML:
        try:
            return _pyyaml.safe_load(text) or {}
        except _pyyaml.YAMLError as e:
            return die("%s is not valid YAML: %s" % (CONTEXT.config_file, e))
    return _simple_yaml_load(text)


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
    profile names so it is always safe inside a filename."""
    name = platform.node().split(".")[0] or "unknown"
    return re.sub(r"[^A-Za-z0-9_.\-]", "_", name)


def _normalize_config_keys(config: ConfigDict) -> None:
    """Force profile and session names back to str after loading.

    PyYAML parses mapping keys that look like numbers as int/float, so a
    session literally named "123" would load under the int key 123. xtm
    always looks names up as strings, so without this a purely numeric
    name would silently fail every lookup rather than working or raising
    a clear error. Mutates `config` in place.
    """
    profiles = config.get("profiles")
    if not isinstance(profiles, dict):
        return
    config["profiles"] = dict((str(k), v) for k, v in profiles.items())
    for profile in config["profiles"].values():
        if isinstance(profile, dict) and isinstance(profile.get("sessions"), dict):
            profile["sessions"] = dict(
                (str(k), v) for k, v in profile["sessions"].items())


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
    # A bare "profiles:" with nothing under it reads as None through PyYAML
    # and as {} through the built-in parser. Both mean "no profiles yet", so
    # they are folded together here rather than being allowed to produce a
    # different outcome depending on which backend is installed.
    if data["profiles"] is None:
        data["profiles"] = {}
    if not isinstance(data["profiles"], dict):
        die("%s: 'profiles' must be a mapping of profile name -> settings."
            % (CONTEXT.config_file,))
    _normalize_config_keys(data)
    for name in data["profiles"]:
        # Validated on load as well as on input, because a hand-edited
        # name containing ':' would round-trip through the built-in
        # dumper into a file that can no longer be parsed.
        validate_profile_name(name)
    if DEFAULT_PROFILE_NAME not in data["profiles"]:
        # The reserved profile is the last resort of resolution, so it has
        # to exist. Recreating it keeps a config written by an older
        # version, or one whose default was removed by hand, working.
        logger.info("Adding the reserved %r profile to %s.",
                    DEFAULT_PROFILE_NAME, CONTEXT.config_file)
        data["profiles"][DEFAULT_PROFILE_NAME] = {
            "stack": dict(DEFAULT_STACK),
            "sessions": {},
        }
        save_config(data)
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


def validate_session_entry(entry: GeometryDict, context: str) -> None:
    """Validate a slot's geometry plus its optional launch settings."""
    validate_geometry(entry, context)
    session = entry.get("session")
    if session is not None:
        if not isinstance(session, str):
            die("%s: 'session' must be a string, got %r." % (context, session))
        validate_session_name(session)
    title = entry.get("title")
    if title is not None:
        if not isinstance(title, str):
            die("%s: 'title' must be a string, got %r." % (context, title))
        validate_title_template(title, context)
    command = entry.get("command")
    if command is not None and not isinstance(command, str):
        die("%s: 'command' must be a string, got %r." % (context, command))
    cwd = entry.get("cwd")
    if cwd is not None and not isinstance(cwd, str):
        die("%s: 'cwd' must be a string, got %r." % (context, cwd))
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
    if not isinstance(value, str):
        die("%s: 'prefix' must be a string, got %r." % (context, value))
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
        if key == "display":
            # Named explicitly rather than folded into the generic
            # unknown-key error: a config written for 0.3 would otherwise
            # fail with a message that does not explain what happened.
            die("%s: matching on 'display' was removed in version 0.4. "
                "Use 'hostname' instead." % (context,))
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
    """Normalise a 'hostname' value to a list of patterns.

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
    # Same fold as in load_config: an empty "stack:" or "sessions:" block is
    # None under PyYAML and {} under the built-in parser. setdefault cannot
    # do this, because the key is present -- it just has no value.
    # An empty "stack:" block is None under PyYAML and {} under the built-in
    # parser, so both are folded to the defaults -- a stack with no fields is
    # unusable either way, and a profile missing the section already
    # self-heals into a complete one.
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
        if not isinstance(profile["title"], str):
            die("profile %r: 'title' must be a string, got %r."
                % (name, profile["title"]))
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


# Kept as the historical name used across the command implementations.
get_profile = validate_profile


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
    the first-match behaviour a linear scan had; validate_profile() rejects
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
# a shell session. README.md documents an optional shell wrapper for those
# who also want a real environment variable.

def read_current_profile_state() -> Optional[str]:
    """Return the persisted current profile for this machine, if any.

    Falls back to the pre-0.3 machine-independent state file so that an
    existing setup keeps working after the upgrade.
    """
    for candidate in (CONTEXT.state_file, CONTEXT.state_dir / "current_profile"):
        if not candidate.exists():
            continue
        raw = _read_text_safe(candidate, "current-profile state file")
        # Only the first line is meaningful; anything odd appended after
        # it is ignored rather than returned as a name that could never
        # match a real profile.
        first_line = raw.splitlines()[0].strip() if raw.strip() else ""
        if first_line:
            logger.debug("Current profile %r read from %s", first_line, candidate)
            return first_line
    return None


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
                         switch_override: Optional[str] = None) -> str:
    """Decide which profile this invocation uses.

    Order of precedence:
      1. An explicitly named profile, from the positional argument or
         --profile, which is also persisted as the new current profile.
         Switching away from a different profile disposes of that
         profile's windows according to its 'on_switch' setting.
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
    live = tmux_list_sessions()
    targets = [s for s in profile_sessions(old_profile)
               if live.get(s) and s not in keep]
    if not targets:
        logger.debug("No windows from profile %s need %sing.",
                     old_profile_name, mode)
        return
    action = tmux_detach_session if mode == "detach" else tmux_kill_session
    done, failed = [], []
    for session in targets:
        (done if action(session) else failed).append(session)
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

def tmux_list_sessions() -> Dict[str, bool]:
    """Return {session_name: is_attached} for every tmux session.

    An empty dict means no tmux server is running yet; tmux exits
    non-zero in that case, which is expected rather than an error worth
    surfacing.
    """
    res = run(["tmux", "list-sessions", "-F", "#{session_name}\t#{session_attached}"],
              required=False)
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
        name, _, attached = line.partition("\t")
        # #{session_attached} is a client COUNT, not strictly 0/1, so any
        # non-zero, non-empty value means at least one client is attached.
        sessions[name] = attached.strip() not in ("0", "")
    return sessions


def tmux_session_attached(name: str) -> bool:
    """Whether a tmux session currently has at least one client."""
    return tmux_list_sessions().get(name, False)


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


def tmux_detach_session(name: str) -> bool:
    """Detach every client from a session, closing its window.

    The window closes as a consequence rather than being killed: the
    xterm's `tmux new-session -A` command returns when its client
    detaches, so the terminal exits on its own. The session, its windows,
    its panes and everything running in them stay alive.
    """
    if CONTEXT.dry_run:
        logger.info("[dry-run] would detach tmux session %r", name)
        return True
    res = run(["tmux", "detach-client", "-s", name])
    if res is None or res.returncode != 0:
        logger.debug("detach-client %r failed: %s", name,
                     _truncate(res.stderr if res else ""))
        return False
    return True


def tmux_client_tty(name: str) -> Optional[str]:
    """tty device of the first client attached to `name`, or None.

    Used only to detect THAT something has attached (see
    wait_for_client_tty). Once a specific client has to be acted on,
    tmux_client_tty_for_pid() is used instead, because it can tell xtm's
    own client apart from an unrelated second one.
    """
    res = run(["tmux", "list-clients", "-t", name, "-F", "#{client_tty}"])
    if res is None or res.returncode != 0:
        return None
    for line in res.stdout.splitlines():
        line = line.strip()
        if line:
            return line
    return None


def _read_proc_ppid(pid: int) -> Optional[int]:
    """Parent PID of `pid`, read from /proc/<pid>/stat.

    Linux-specific, which is fine because xtm targets RHEL/Ubuntu hosts.
    Returns None when it cannot be read (process already gone, no /proc).
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


def tmux_client_tty_for_pid(session: str, xterm_pid: Optional[int]) -> Optional[str]:
    """Like tmux_client_tty, but prefer the client running inside
    `xterm_pid` when several clients are attached to `session`.

    Falls back to the first-listed client when `xterm_pid` is None or
    none of the attached clients descend from it, which keeps this safe
    to call when the caller has no PID to check -- for instance during
    --reset, repositioning a session opened by a previous, now-exited
    invocation. That PID is deliberately not persisted: PIDs are reused,
    so trusting a stale one would be worse than having none. In practice
    this therefore disambiguates the narrow race at open time, not a
    stray extra client attached to a long-running session.
    """
    res = run(["tmux", "list-clients", "-t", session,
               "-F", "#{client_tty}\t#{client_pid}"])
    if res is None or res.returncode != 0:
        return None
    clients = []  # type: List[Tuple[str, Optional[int]]]
    for line in res.stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        tty, _, pid_str = line.partition("\t")
        try:
            clients.append((tty, int(pid_str)))
        except ValueError:
            clients.append((tty, None))
    if not clients:
        return None
    if xterm_pid is not None:
        for tty, client_pid in clients:
            if client_pid is not None and _is_descendant_of(client_pid, xterm_pid):
                logger.debug("Matched tmux client %s (pid=%s) as a descendant of "
                             "xterm pid=%d for session %r",
                             tty, client_pid, xterm_pid, session)
                return tty
        logger.debug("No tmux client of %r descends from xterm pid=%d; using the "
                     "first-listed client.", session, xterm_pid)
    return clients[0][0]


def wait_for_client_tty(session: str, proc: Optional[subprocess.Popen] = None,
                        timeout: float = WAIT_FOR_ATTACH_TIMEOUT_SECONDS
                        ) -> Optional[str]:
    """Poll until a tmux client attaches to `session`, or time out.

    When the spawned xterm process is supplied, its exit is detected
    immediately and reported with its real exit status, instead of making
    the user wait out the full timeout for a generic message.
    """
    logger.debug("Waiting up to %.1fs for a client to attach to %r", timeout, session)
    deadline = time.time() + timeout
    while time.time() < deadline:
        tty = tmux_client_tty(session)
        if tty:
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


def place_window(session: str, geometry: GeometryDict,
                 xterm_pid: Optional[int] = None) -> None:
    """Move and resize one session's window to `geometry`.

    The client tty is resolved once and reused for both operations: two
    lookups meant two `tmux list-clients` calls that could disagree if a
    client attached or detached between them.

    Resize happens before move because on some window managers a resize
    can shift the window's anchor point, so moving last guarantees the
    final position is the requested one either way.

    `xterm_pid` is known only when this invocation just spawned the
    window itself, and lets tmux_client_tty_for_pid() pick the right
    client when several are attached. It is None for --reset, which has
    no reliable record of a past invocation's PID and deliberately does
    not persist one.
    """
    tty = tmux_client_tty_for_pid(session, xterm_pid)
    if not tty:
        die("No attached client for tmux session %r; cannot place its window."
            % (session,))
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
    window manager, which is free to honour it approximately (size
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
# Stacking (default placement for sessions with no configured position)
# --------------------------------------------------------------------------

def stack_slot(stack_cfg: GeometryDict, index: int) -> GeometryDict:
    """Diagonally offset position and size for the Nth (0-based) window
    that has no configured position. All stacked windows share one size.

    Known limitation, by design rather than by oversight: xtm does not
    know the monitor resolution, so with enough simultaneous stray
    sessions the diagonal offsets eventually push a window off screen.
    There is no wraparound; lower offset_x/offset_y, or give the sessions
    explicit positions, if that becomes a practical problem.
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

def build_xterm_command(session: str, sess_cfg: Optional[GeometryDict],
                        title: Optional[str] = None) -> List[str]:
    """Assemble the xterm argument vector for one session.

    Every window gets allowWindowOps (required for placement to work at
    all), a title, and an instance name for robust identification. Any
    per-session xterm_args are inserted before -e, so a profile can set
    fonts or colours -- colour-coding a production cluster differently
    from a development one, for example.

    The title is cosmetic and may be overridden by the profile; the
    instance name is the identity and is always derived from the session
    name, so a custom title can never break window discovery.

    The tmux side uses `new-session -A`, which attaches to the session
    when it already exists and creates it otherwise. Note that -c and the
    startup command apply only when the session is actually created;
    tmux ignores them when attaching to an existing session.
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

    return argv + ["-e"] + tmux_argv


def spawn_xterm(session: str, sess_cfg: Optional[GeometryDict] = None,
                title: Optional[str] = None) -> Optional[subprocess.Popen]:
    """Launch a new xterm attached to `session` and return the process.

    Does not wait for the tmux client to attach; see wait_for_client_tty.
    The PID is threaded through to place_window() so that, when more than
    one client ends up attached to a session, xtm can identify its own
    rather than acting on whichever tmux happens to list first.

    Uses Popen directly rather than run(): this starts the user's real
    interactive terminal, so unlike xtm's information-gathering calls it
    must inherit the user's environment and locale untouched.
    """
    if not os.environ.get("DISPLAY"):
        die("$DISPLAY is not set, so xterm has nowhere to open a window. Make "
            "sure this shell has a working X display (an X-forwarded or local "
            "session rather than a plain non-X SSH login).")
    argv = build_xterm_command(session, sess_cfg, title)
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


def open_session(session: str, geometry: GeometryDict,
                 sess_cfg: Optional[GeometryDict] = None,
                 title: Optional[str] = None) -> None:
    """Open one session in a new window and place it. Errors if the
    session already has a window attached."""
    validate_session_name(session)
    if tmux_session_attached(session):
        die("tmux session %r is already attached to a window." % (session,))
    proc = spawn_xterm(session, sess_cfg, title)
    if CONTEXT.dry_run:
        logger.info("[dry-run] would place %r at x=%s y=%s %sx%s", session,
                    geometry["x"], geometry["y"], geometry["width"],
                    geometry["height"])
        return
    tty = wait_for_client_tty(session, proc)
    if not tty:
        die("Timed out waiting for tmux session %r to attach. Check that xterm "
            "is installed and DISPLAY is set correctly." % (session,))
    place_window(session, geometry, proc.pid if proc else None)
    logger.info("Opened %s at x=%s y=%s %sx%s", session, geometry["x"],
                geometry["y"], geometry["width"], geometry["height"])


def reposition_or_open(session: str, geometry: GeometryDict,
                       sess_cfg: Optional[GeometryDict] = None,
                       attached: Optional[bool] = None,
                       title: Optional[str] = None) -> None:
    """Move an already-open session's window, or open it if it is not up.

    `attached` lets a caller pass in a session snapshot it already has,
    avoiding one `tmux list-sessions` call per session on a bulk reset.
    """
    validate_session_name(session)
    if attached is None:
        attached = tmux_session_attached(session)
    if attached:
        if CONTEXT.dry_run:
            logger.info("[dry-run] would reposition %s to x=%s y=%s %sx%s",
                        session, geometry["x"], geometry["y"],
                        geometry["width"], geometry["height"])
            return
        place_window(session, geometry)
        logger.info("Repositioned %s to x=%s y=%s %sx%s", session, geometry["x"],
                    geometry["y"], geometry["width"], geometry["height"])
    else:
        open_session(session, geometry, sess_cfg, title)


def count_unnamed_open(profile: ProfileDict,
                       live: Optional[Dict[str, bool]] = None) -> int:
    """How many attached tmux sessions are not named in this profile.

    Used to choose the next stack slot for --open on an unconfigured
    session. This is deliberately a different ordering strategy from
    --reset-all, which re-stacks every stray in alphabetical order for a
    fully deterministic layout; --open simply appends one more window to
    however many are already out there. Both are self-consistent, they
    just serve different purposes.
    """
    named = set(profile_sessions(profile))
    live = tmux_list_sessions() if live is None else live
    return sum(1 for name, attached in live.items()
               if attached and name not in named)


# --------------------------------------------------------------------------
# Commands: window actions
# --------------------------------------------------------------------------

def cmd_open(args: argparse.Namespace, config: ConfigDict, profile_name: str) -> int:
    """Open one session, at its configured position or the next stack slot."""
    profile = get_profile(config, profile_name)
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
    open_session(session, geometry, slot_cfg, title)
    return EXIT_OK


def cmd_reset(args: argparse.Namespace, config: ConfigDict, profile_name: str) -> int:
    """Reposition, or open, every named session in the profile."""
    profile = get_profile(config, profile_name)
    if not profile["sessions"]:
        logger.info("Profile %s has no slots configured.", profile_name)
        return EXIT_OK
    live = tmux_list_sessions()
    failures = []
    for slot, session in profile_slots(profile):
        slot_cfg = profile["sessions"][slot]
        try:
            reposition_or_open(
                session, session_geometry(slot_cfg), slot_cfg,
                attached=live.get(session, False),
                title=resolve_window_title(profile, profile_name, slot, session))
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
    """Reset named sessions, then stack every stray session as well."""
    profile = get_profile(config, profile_name)
    status = cmd_reset(args, config, profile_name)

    named = set(profile_sessions(profile))
    strays = sorted(name for name, attached in tmux_list_sessions().items()
                    if attached and name not in named)
    failures = []
    for index, session in enumerate(strays):
        geometry = stack_slot(profile["stack"], index)
        try:
            place_window(session, geometry)
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
    profile = get_profile(config, profile_name)
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
    profile = get_profile(config, profile_name)
    session = _resolve_target_session(profile,
                                      validate_session_name(args.detach))
    live = tmux_list_sessions()
    if session not in live:
        logger.error("No tmux session named %r is running.", session)
        return EXIT_ERROR
    if not live[session]:
        logger.info("Session %s has no window attached.", session)
        return EXIT_OK
    if not tmux_detach_session(session):
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
    profile = get_profile(config, profile_name)
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
    targets = sorted(s for s in wanted if s in live)
    verb = "Detach" if args.detach_mode else "Close"
    if not targets:
        logger.info("No sessions from %s are running.", scope)
        return EXIT_OK
    if not confirm("%s %d session(s) from %s (%s)?"
                   % (verb, len(targets), scope, ", ".join(targets)), args.yes):
        logger.info("Cancelled.")
        return EXIT_OK
    action = tmux_detach_session if args.detach_mode else tmux_kill_session
    failures = [s for s in targets if not action(s)]
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
    profile = get_profile(config, profile_name)
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
    profile = get_profile(config, profile_name)
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
                # An unrecognised window is recorded under its own name with
                # an explicit 'session', so the entry means exactly the
                # session that was captured regardless of any prefix.
                entry = dict(geometry)
                if session != "%s%s" % (profile.get("prefix", ""), session):
                    entry["session"] = session
                profile["sessions"][session] = entry
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
    profile = get_profile(config, profile_name)
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
    profile = get_profile(config, profile_name)
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
        logger.info("Cancelled.")
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
    if read_current_profile_state() == old:
        write_current_profile_state(new)
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
    editor -- the editor writes the file itself. The two short writes this
    command really does make take the lock individually.
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
    if not CONTEXT.config_file.exists():
        with config_lock():
            # Re-check inside the lock: another xtm may have created the
            # config between the test above and the lock being granted.
            if not CONTEXT.config_file.exists():
                _write_text_safe(CONTEXT.config_file, dump_yaml(DEFAULT_CONFIG),
                                 "config file")
    if CONTEXT.dry_run:
        logger.info("[dry-run] would open %s in %s", CONTEXT.config_file, editor)
        return EXIT_OK
    logger.debug("Opening %s in %s", CONTEXT.config_file, editor)
    try:
        subprocess.call(editor_argv + [str(CONTEXT.config_file)])
    except OSError as e:
        die("Could not start editor %r: %s" % (editor, e))
    # Held only for the reload: load_config() may rewrite the file to restore
    # the reserved "default" profile if the edit removed it.
    with config_lock():
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
        if not results:
            print("  (no profiles defined)")
    return EXIT_ERROR if bad else EXIT_OK


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


def session_status(session: str, live: Dict[str, bool]) -> str:
    """Human-readable running state of one session."""
    if session not in live:
        return "not running"
    return "attached" if live[session] else "detached"


def collect_status(config: ConfigDict, profile_name: str) -> Dict[str, Any]:
    """Gather everything --list reports, in one structure.

    Building the report as data first means the plain-text and JSON
    renderings cannot drift apart, and the live-position lookup happens
    exactly once either way.
    """
    profile = get_profile(config, profile_name)
    tool = detect_geometry_tool()
    live = tmux_list_sessions()

    sessions = []
    for slot, session in profile_slots(profile):
        cfg = profile["sessions"][slot]
        entry = {
            "name": slot,
            "session": session,
            "shared": bool(cfg.get("session")),
            "title": resolve_window_title(profile, profile_name, slot, session),
            "status": session_status(session, live),
            "configured": session_geometry(cfg),
            "command": cfg.get("command"),
            "cwd": cfg.get("cwd"),
            "live": None,
        }
        if live.get(session) and tool:
            entry["live"] = get_window_geometry(session, tool)
        sessions.append(entry)

    named = set(profile_sessions(profile))
    strays = sorted(name for name, attached in live.items()
                    if attached and name not in named)
    # Say which other profile a stray belongs to, so an accumulation left
    # behind by an earlier profile is recognisable rather than mysterious.
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
        print("  %-20s %-12s configured: %s%s"
              % (entry["name"], entry["status"], position, live_text))
        # Worth a line whenever the session name is not simply the slot
        # name, or whenever it is shared -- being shared changes what a
        # profile switch will do to it, so it should never be invisible.
        if entry["session"] != entry["name"] or entry["shared"]:
            print("  %-20s %-12s session:    %s%s"
                  % ("", "", entry["session"],
                     "  (shared, kept on profile switch)"
                     if entry["shared"] else ""))
        # Launch settings are shown only when set, on their own lines, so
        # the common case stays a single line per session while a session
        # that does something unusual on startup says so.
        if entry["command"]:
            print("  %-20s %-12s command:    %s" % ("", "", entry["command"]))
        if entry["cwd"]:
            print("  %-20s %-12s cwd:        %s" % ("", "", entry["cwd"]))
    print("")
    print("Stray attached sessions (no configured position):")
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
                "status": session_status(session, live),
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
    unclaimed = sorted(name for name, attached in live.items()
                       if attached and name not in claimed)

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
            print("  %-18s %-14s %-12s%s"
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
    effective = current or profile_name
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
# Each entry is (argparse dest, handler, mutates_config). The table is the
# single source of truth for dispatch: which action ran, whether the config
# lock is needed, and which action a bare `xtm` falls back to.

COMMANDS = (
    # (argparse dest, handler, mutates_config, needs_profile)
    ("open", cmd_open, False, True),
    ("reset", cmd_reset, False, True),
    ("reset_all", cmd_reset_all, False, True),
    # needs_profile: --close and --focus resolve a slot name against the
    # profile before acting, exactly as --detach does, so an unresolvable
    # profile is fatal for all three rather than for only one of them.
    ("close", cmd_close, False, True),
    ("close_all", cmd_close_all, False, True),
    ("focus", cmd_focus, False, True),
    ("list", cmd_list, False, True),
    ("list_profiles", cmd_list_profiles, False, False),
    ("current_profile", cmd_current_profile, False, False),
    ("update_profile", cmd_update_profile, True, True),
    ("new_profile", cmd_new_profile, True, False),
    ("set", cmd_set, True, True),
    ("delete_session", cmd_delete_session, True, True),
    ("delete_profile", cmd_delete_profile, True, False),
    ("copy_profile", cmd_copy_profile, True, False),
    ("rename_profile", cmd_rename_profile, True, False),
    ("detach", cmd_detach, False, True),
    ("make_global", cmd_make_global, True, False),
    # NOT marked config-mutating: cmd_edit takes the lock itself around
    # its short writes rather than holding it across an interactive
    # editor session. See its docstring.
    ("edit", cmd_edit, False, False),
    ("validate", cmd_validate, False, False),
)

DEFAULT_ACTION = "list"


def selected_action(args: argparse.Namespace) -> Tuple[str, Any, bool, bool]:
    """Return the (dest, handler, mutates_config, needs_profile) entry for
    this run.

    With no action flag at all, xtm reports the status of the resolved
    profile. That makes a bare `xtm` both safe and informative: it answers
    "what am I set up for right now" without opening, moving or closing
    anything.
    """
    for entry in COMMANDS:
        if getattr(args, entry[0], None):
            return entry
    logger.debug("No action requested; defaulting to --%s.",
                 DEFAULT_ACTION.replace("_", "-"))
    setattr(args, DEFAULT_ACTION, True)
    for entry in COMMANDS:
        if entry[0] == DEFAULT_ACTION:
            return entry
    raise AssertionError("DEFAULT_ACTION is not present in COMMANDS")


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    """Construct the argument parser.

    Every frequently used option has both a short and a long form. The
    single-dash long spellings from earlier releases (-open, -reset,
    -resetall, -updateprofile, -newprofile, -listprofiles,
    -currentprofile, -log-file) are kept as hidden aliases so existing
    scripts and shell functions keep working.
    """
    parser = argparse.ArgumentParser(
        prog="xtm",
        description="xterm + tmux workspace manager (version %s)" % (__version__,),
        epilog="With no action, xtm prints the status of the current profile.",
    )

    parser.add_argument("--version", "-version", action="version",
                        version="xtm %s" % (__version__,))

    # Switching profiles is by far the most common thing the tool is asked
    # to do, so it gets the shortest possible spelling: `xtm desk`. The
    # --profile flag remains as an equivalent alias for existing scripts.
    parser.add_argument("profile_positional", nargs="?", metavar="PROFILE",
                        help="Profile to switch to, for example 'xtm desk'. "
                             "Equivalent to --profile NAME.")

    general = parser.add_argument_group("general options")
    general.add_argument("-p", "--profile", "-profile", metavar="NAME",
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
    general.add_argument("-j", "--json", action="store_true",
                         help="Emit machine-readable JSON from reporting "
                              "commands (--list, --list-profiles, "
                              "--current-profile, --validate).")
    general.add_argument("-N", "--dry-run", action="store_true",
                         help="Show what would happen without launching, "
                              "moving, closing or saving anything.")
    general.add_argument("-y", "--yes", action="store_true",
                         help="Answer yes to confirmation prompts.")
    general.add_argument("--verify", action="store_true",
                         help="After placing a window, read its position back "
                              "and report any difference from what was asked.")
    general.add_argument("--no-frame-compensation", action="store_true",
                         help="Do not correct captured positions for window "
                              "manager decorations (see README).")

    logging_group = parser.add_argument_group("logging options")
    logging_group.add_argument("-d", "--debug", "-debug", action="store_true",
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
    logging_group.add_argument("-L", "--log-file", "-log-file", metavar="PATH",
                               help="Also write full DEBUG-level logs to PATH. "
                                    "Off by default.")

    actions = parser.add_argument_group("actions (choose at most one)")
    action = actions.add_mutually_exclusive_group()
    action.add_argument("-o", "--open", "-open", metavar="SESSION",
                        help="Open one tmux session in a new xterm.")
    action.add_argument("-r", "--reset", "-reset", action="store_true",
                        help="Reposition or open every named session in the "
                             "profile.")
    action.add_argument("-R", "--reset-all", "-resetall", action="store_true",
                        help="Like --reset, and also stack any stray sessions.")
    action.add_argument("-k", "--close", metavar="SESSION",
                        help="Kill one tmux session and close its window.")
    action.add_argument("-K", "--close-all", action="store_true",
                        help="Kill every running session named in the profile.")
    action.add_argument("-f", "--focus", metavar="SESSION",
                        help="Raise and focus one session's window.")
    action.add_argument("-l", "--list", "-list", action="store_true",
                        help="Show session and window status for the profile "
                             "(the default action).")
    action.add_argument("-P", "--list-profiles", "-listprofiles",
                        action="store_true",
                        help="List every profile in the config.")
    action.add_argument("-C", "--current-profile", "-currentprofile",
                        action="store_true",
                        help="Print this machine's current profile.")
    action.add_argument("-u", "--update-profile", "-updateprofile",
                        action="store_true",
                        help="Save current window positions into the profile.")
    action.add_argument("-n", "--new-profile", "-newprofile", metavar="NAME",
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
    action.add_argument("-V", "--validate", action="store_true",
                        help="Check every profile in the config file.")
    return parser


def apply_runtime_flags(args: argparse.Namespace) -> None:
    """Publish the cross-cutting run modes taken from parsed arguments."""
    CONTEXT.dry_run = bool(args.dry_run)
    CONTEXT.verify = bool(args.verify)
    CONTEXT.frame_compensation = not args.no_frame_compensation


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
                 CONTEXT.config_file, CONTEXT.state_file, "yes" if HAVE_PYYAML else "no")
    if CONTEXT.dry_run:
        logger.info("Dry run: no windows or files will be changed.")

    dest, handler, mutates, needs_profile = selected_action(args)
    logger.debug("Action: %s (mutates config: %s, needs profile: %s)",
                 dest, mutates, needs_profile)

    # Mutating commands hold the lock across the whole load/modify/save
    # cycle, so two concurrent runs cannot lose each other's edits.
    if mutates:
        with config_lock():
            return _run_action(args, handler, needs_profile)
    return _run_action(args, handler, needs_profile)


def _merge_positional_profile(parser: argparse.ArgumentParser,
                              args: argparse.Namespace) -> None:
    """Fold the positional PROFILE argument into args.profile.

    Supplying both spellings is rejected rather than resolved in favour
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
                needs_profile: bool) -> int:
    """Load the config, resolve the profile and invoke one command.

    --profile is resolved before dispatch regardless of the action, so
    that `xtm -p work -C` both selects and reports the profile in one
    call rather than silently ignoring the selection.

    Commands that operate on the config as a whole rather than on one
    profile (listing, validating, renaming) tolerate an unresolvable
    selection: a config with no "default" profile and no saved choice
    must not stop the user from listing what profiles do exist.
    """
    config = load_config()
    try:
        profile_name = resolve_profile_name(config, args.profile, args.auto,
                                            args.all, args.on_switch)
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
