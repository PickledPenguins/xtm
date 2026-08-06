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
import fnmatch
import json
import logging
import math
import os
import platform
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

__version__ = "0.3"
# Versioning scheme: 0.<N>, N incrementing by 1 each release (0.9 -> 0.10,
# never rolling over to 1.0). See CHANGELOG.md for what changed per release.

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

# Resolved during startup by resolve_paths(); module-level so every helper
# can reach them without threading a context object through every call.
CONFIG_DIR = DEFAULT_CONFIG_DIR
CONFIG_FILE = CONFIG_DIR / "config.yaml"
STATE_DIR = DEFAULT_STATE_DIR
STATE_FILE = STATE_DIR / "current_profile"

TITLE_PREFIX = "xtm:"
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
DRY_RUN = False
VERIFY = False
FRAME_COMPENSATION = True

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
    if DRY_RUN:
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
        self.path = (path or CONFIG_FILE).with_suffix(
            (path or CONFIG_FILE).suffix + ".lock")
        self._handle = None  # type: Any

    def __enter__(self) -> "config_lock":
        """Take the exclusive lock, degrading to no locking on failure."""
        try:
            import fcntl
        except ImportError:  # pragma: no cover - non-POSIX platforms only
            logger.debug("fcntl unavailable; proceeding without a config lock")
            return self
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self._handle = open(str(self.path), "w")
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
# YAML support, with a dependency-free fallback
# --------------------------------------------------------------------------

try:
    import yaml as _pyyaml  # type: ignore

    HAVE_PYYAML = True
except ImportError:
    HAVE_PYYAML = False

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
        or bool(re.search(r'[:#\[\]{},"\'\n]', s))
        or s.strip() != s
        or s.startswith(("-", "?", "&", "*", "!", "%", "@", "`", ">", "|"))
    )
    if needs_quoting:
        return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'
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


def _split_top_level(text: str, separator: str = ",") -> List[str]:
    """Split on `separator` while ignoring separators nested inside
    quotes, brackets or braces. Needed to parse flow-style collections
    such as {x: 0, y: 0} and [a, b] without a full YAML engine."""
    parts = []  # type: List[str]
    depth = 0
    quote = ""
    current = []  # type: List[str]
    for ch in text:
        if quote:
            current.append(ch)
            if ch == quote:
                quote = ""
            continue
        if ch in "\"'":
            quote = ch
            current.append(ch)
            continue
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


def _simple_yaml_scalar_load(tok: str) -> Any:
    """Parse one scalar, flow list or flow mapping produced by hand or by
    _simple_yaml_dump."""
    tok = tok.strip()
    if tok in ("", "~", "null", "Null", "NULL"):
        return None
    if tok in ("true", "True", "TRUE"):
        return True
    if tok in ("false", "False", "FALSE"):
        return False
    if len(tok) >= 2 and tok[0] == tok[-1] == '"':
        return tok[1:-1].replace('\\"', '"').replace("\\\\", "\\")
    if len(tok) >= 2 and tok[0] == tok[-1] == "'":
        return tok[1:-1].replace("''", "'")
    if tok.startswith("{") and tok.endswith("}"):
        return _simple_yaml_flow_mapping_load(tok)
    if tok.startswith("[") and tok.endswith("]"):
        inner = tok[1:-1].strip()
        if not inner:
            return []
        return [_simple_yaml_scalar_load(p) for p in _split_top_level(inner)]
    if re.match(r"^-?\d+$", tok):
        return int(tok)
    if re.match(r"^-?(\d+\.\d*|\.\d+)([eE][-+]?\d+)?$", tok):
        return float(tok)
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
        if ":" not in part:
            die("Malformed flow mapping entry %r in config." % (part,))
        key, _, value = part.partition(":")
        key = _simple_yaml_scalar_load(key.strip())
        result[str(key)] = _simple_yaml_scalar_load(value)
    return result


def _simple_yaml_load(text: str) -> Dict[str, Any]:
    """Minimal indentation-based YAML reader matching _simple_yaml_dump's
    output, plus inline comments and flow-style collections.

    Only mappings of mappings of scalars/flow-collections are supported,
    which is all xtm's config ever needs. A bare "key:" with nothing after
    it always starts a nested mapping rather than meaning an explicit
    null: that is indistinguishable from a genuinely empty mapping using
    the line alone, and a nested mapping is what xtm wants in every case
    that can actually occur (an empty `sessions:` block should become {},
    not None).
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
    stack = [(-1, root)]  # type: List[Tuple[int, Dict[str, Any]]]
    for indent, content in lines:
        while len(stack) > 1 and stack[-1][0] >= indent:
            stack.pop()
        parent = stack[-1][1]
        if ":" not in content:
            die("Malformed config line: %r" % (content,))
        key, _, rest = content.partition(":")
        key = str(_simple_yaml_scalar_load(key.strip()))
        rest = rest.strip()
        if rest == "":
            new_dict = {}  # type: Dict[str, Any]
            parent[key] = new_dict
            stack.append((indent, new_dict))
        else:
            parent[key] = _simple_yaml_scalar_load(rest)
    return root


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
            return _pyyaml.safe_dump(data, sort_keys=False, default_flow_style=False)
        except TypeError:
            logger.debug("PyYAML is too old for sort_keys=; using the "
                         "built-in dumper to preserve config key order.")
    return _simple_yaml_dump(data) + "\n"


def load_yaml(text: str) -> Dict[str, Any]:
    """Parse config text with PyYAML when available, else the built-in
    reader."""
    if HAVE_PYYAML:
        try:
            return _pyyaml.safe_load(text) or {}
        except _pyyaml.YAMLError as e:
            return die("%s is not valid YAML: %s" % (CONFIG_FILE, e))
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
    """
    global CONFIG_DIR, CONFIG_FILE, STATE_DIR, STATE_FILE
    if config_path:
        CONFIG_FILE = Path(os.path.expanduser(config_path))
        CONFIG_DIR = CONFIG_FILE.parent
    else:
        env_dir = os.environ.get("XTM_CONFIG_DIR")
        CONFIG_DIR = Path(os.path.expanduser(env_dir)) if env_dir else DEFAULT_CONFIG_DIR
        CONFIG_FILE = CONFIG_DIR / "config.yaml"

    if state_dir:
        STATE_DIR = Path(os.path.expanduser(state_dir))
    else:
        env_state = os.environ.get("XTM_STATE_DIR")
        STATE_DIR = Path(os.path.expanduser(env_state)) if env_state else DEFAULT_STATE_DIR
    # The current profile is per-machine: a shared or NFS-mounted home
    # directory would otherwise let two machines fight over one "current
    # profile", which defeats the entire point of per-machine profiles.
    STATE_FILE = STATE_DIR / ("current_profile." + short_hostname())


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
    if not CONFIG_FILE.exists():
        if not create_if_missing:
            die("No config file at %s." % (CONFIG_FILE,))
        logger.info("No config file at %s; creating a default one.", CONFIG_FILE)
        _write_text_safe(CONFIG_FILE, dump_yaml(DEFAULT_CONFIG), "config file")
        if DRY_RUN:
            return dict(DEFAULT_CONFIG)
    raw_text = _read_text_safe(CONFIG_FILE, "config file")
    data = load_yaml(raw_text)
    if not isinstance(data, dict) or "profiles" not in data:
        die("%s does not look like an xtm config (no 'profiles' key)." % (CONFIG_FILE,))
    if not isinstance(data["profiles"], dict):
        die("%s: 'profiles' must be a mapping of profile name -> settings."
            % (CONFIG_FILE,))
    _normalize_config_keys(data)
    for name in data["profiles"]:
        # Validated on load as well as on input, because a hand-edited
        # name containing ':' would round-trip through the built-in
        # dumper into a file that can no longer be parsed.
        validate_profile_name(name)
    logger.debug("Loaded config from %s with profiles: %s",
                 CONFIG_FILE, ", ".join(sorted(data["profiles"])) or "(none)")
    return data


def save_config(config: ConfigDict) -> None:
    """Persist the whole config file."""
    _write_text_safe(CONFIG_FILE, dump_yaml(config), "config file")


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
    """Validate a session's geometry plus its optional launch settings."""
    validate_geometry(entry, context)
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


def validate_match(entry: Any, context: str) -> None:
    """Validate a profile's optional auto-selection block."""
    if not isinstance(entry, dict):
        die("%s: 'match' must be a mapping." % (context,))
    for key, value in entry.items():
        if key not in ("hostname", "display"):
            die("%s: unknown 'match' key %r (expected 'hostname' or 'display')."
                % (context, key))
        if not isinstance(value, str):
            die("%s: 'match.%s' must be a string, got %r." % (context, key, value))


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
            % (name, CONFIG_FILE, ", ".join(sorted(profiles)) or "(none)"))
    profile = profiles[name]
    if not isinstance(profile, dict):
        die("Profile %r in %s must be a mapping, got %s."
            % (name, CONFIG_FILE, type(profile).__name__))
    profile.setdefault("stack", dict(DEFAULT_STACK))
    profile.setdefault("sessions", {})
    if not isinstance(profile["stack"], dict):
        die("Profile %r: 'stack' must be a mapping." % (name,))
    if not isinstance(profile["sessions"], dict):
        die("Profile %r: 'sessions' must be a mapping." % (name,))

    validate_geometry(profile["stack"], "profile %r stack" % (name,),
                      require_offsets=True)
    if "match" in profile:
        validate_match(profile["match"], "profile %r" % (name,))
    for sess_name, sess_cfg in profile["sessions"].items():
        validate_session_name(sess_name)
        if not isinstance(sess_cfg, dict):
            die("Profile %r session %r must be a mapping, got %s."
                % (name, sess_name, type(sess_cfg).__name__))
        validate_session_entry(sess_cfg, "profile %r session %r" % (name, sess_name))
    return profile


# Kept as the historical name used across the command implementations.
get_profile = validate_profile


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
    for candidate in (STATE_FILE, STATE_DIR / "current_profile"):
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
    _write_text_safe(STATE_FILE, name + "\n", "current-profile state file")
    logger.debug("Persisted current profile as %r in %s", name, STATE_FILE)


def match_profile(config: ConfigDict) -> Optional[str]:
    """Pick a profile whose optional 'match' block fits this machine.

    Patterns are shell globs tested against the short hostname and
    against $DISPLAY, which together identify "which desk am I sitting
    at" well enough to select a layout without being told. Profiles are
    considered in sorted order so the choice is deterministic when more
    than one matches; a profile with no 'match' block never matches.
    """
    host = short_hostname()
    display = os.environ.get("DISPLAY", "")
    for name in sorted(config.get("profiles", {})):
        profile = config["profiles"][name]
        if not isinstance(profile, dict):
            continue
        criteria = profile.get("match")
        if not isinstance(criteria, dict) or not criteria:
            continue
        if "hostname" in criteria and not fnmatch.fnmatch(host, criteria["hostname"]):
            continue
        if "display" in criteria and not fnmatch.fnmatch(display, criteria["display"]):
            continue
        logger.debug("Profile %r matched host=%r display=%r", name, host, display)
        return name
    return None


def resolve_profile_name(config: ConfigDict, explicit: Optional[str],
                         auto: bool = False) -> str:
    """Decide which profile this invocation uses.

    Order of precedence:
      1. --profile NAME, which is also persisted as the new current profile.
      2. The persisted current profile for this machine, unless --auto
         was given.
      3. A profile whose 'match' block fits this hostname and $DISPLAY.
      4. The profile literally named "default".

    Step 4 is what makes a bare `xtm` with no arguments work on a fresh
    install: the auto-created config always contains a "default" profile.
    """
    profiles = config.get("profiles", {})
    if explicit:
        if explicit not in profiles:
            die("Profile %r does not exist. Known profiles: %s"
                % (explicit, ", ".join(sorted(profiles)) or "(none)"))
        write_current_profile_state(explicit)
        logger.debug("Using profile %r (explicit --profile)", explicit)
        return explicit

    if not auto:
        current = read_current_profile_state()
        if current:
            if current not in profiles:
                die("Current profile %r (from %s) no longer exists in %s. "
                    "Choose a valid one with --profile NAME."
                    % (current, STATE_FILE, CONFIG_FILE))
            logger.debug("Using profile %r (persisted current profile)", current)
            return current

    matched = match_profile(config)
    if matched:
        logger.info("Auto-selected profile %r for host %s / DISPLAY %s",
                    matched, short_hostname(), os.environ.get("DISPLAY", "(unset)"))
        return matched

    if DEFAULT_PROFILE_NAME in profiles:
        logger.debug("Using profile %r (built-in default)", DEFAULT_PROFILE_NAME)
        return DEFAULT_PROFILE_NAME
    return die("No current profile is set and no profile named %r exists. "
               "Choose one with --profile NAME (see --list-profiles)."
               % (DEFAULT_PROFILE_NAME,))


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
    if DRY_RUN:
        logger.info("[dry-run] would kill tmux session %r", name)
        return True
    res = run(["tmux", "kill-session", "-t", name])
    if res is None or res.returncode != 0:
        logger.debug("kill-session %r failed: %s", name,
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
    if DRY_RUN:
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
    if VERIFY:
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
            % (", ".join(GEOMETRY_TOOLS), CONFIG_FILE))
    return tool


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
    if compensate and FRAME_COMPENSATION:
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
    if DRY_RUN:
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

def build_xterm_command(session: str, sess_cfg: Optional[GeometryDict]) -> List[str]:
    """Assemble the xterm argument vector for one session.

    Every window gets allowWindowOps (required for placement to work at
    all), a title, and an instance name for robust identification. Any
    per-session xterm_args are inserted before -e, so a profile can set
    fonts or colours -- colour-coding a production cluster differently
    from a development one, for example.

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
        "-T", window_title(session),
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


def spawn_xterm(session: str, sess_cfg: Optional[GeometryDict] = None
                ) -> Optional[subprocess.Popen]:
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
    argv = build_xterm_command(session, sess_cfg)
    logger.debug("Spawning: %s", " ".join(argv))
    if DRY_RUN:
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
                 sess_cfg: Optional[GeometryDict] = None) -> None:
    """Open one session in a new window and place it. Errors if the
    session already has a window attached."""
    validate_session_name(session)
    if tmux_session_attached(session):
        die("tmux session %r is already attached to a window." % (session,))
    proc = spawn_xterm(session, sess_cfg)
    if DRY_RUN:
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
                       attached: Optional[bool] = None) -> None:
    """Move an already-open session's window, or open it if it is not up.

    `attached` lets a caller pass in a session snapshot it already has,
    avoiding one `tmux list-sessions` call per session on a bulk reset.
    """
    validate_session_name(session)
    if attached is None:
        attached = tmux_session_attached(session)
    if attached:
        if DRY_RUN:
            logger.info("[dry-run] would reposition %s to x=%s y=%s %sx%s",
                        session, geometry["x"], geometry["y"],
                        geometry["width"], geometry["height"])
            return
        place_window(session, geometry)
        logger.info("Repositioned %s to x=%s y=%s %sx%s", session, geometry["x"],
                    geometry["y"], geometry["width"], geometry["height"])
    else:
        open_session(session, geometry, sess_cfg)


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
    named = set(profile["sessions"])
    live = tmux_list_sessions() if live is None else live
    return sum(1 for name, attached in live.items()
               if attached and name not in named)


# --------------------------------------------------------------------------
# Commands: window actions
# --------------------------------------------------------------------------

def cmd_open(args: argparse.Namespace, config: ConfigDict, profile_name: str) -> int:
    """Open one session, at its configured position or the next stack slot."""
    profile = get_profile(config, profile_name)
    session = validate_session_name(args.open)
    sess_cfg = profile["sessions"].get(session)
    if sess_cfg:
        geometry = session_geometry(sess_cfg)
    else:
        slot = count_unnamed_open(profile)
        geometry = stack_slot(profile["stack"], slot)
        logger.info("%s has no configured position in profile %s; using stacked "
                    "slot #%d.", session, profile_name, slot)
    open_session(session, geometry, sess_cfg)
    return EXIT_OK


def cmd_reset(args: argparse.Namespace, config: ConfigDict, profile_name: str) -> int:
    """Reposition, or open, every named session in the profile."""
    profile = get_profile(config, profile_name)
    if not profile["sessions"]:
        logger.info("Profile %s has no named sessions configured.", profile_name)
        return EXIT_OK
    live = tmux_list_sessions()
    failures = []
    for session, sess_cfg in profile["sessions"].items():
        try:
            reposition_or_open(session, session_geometry(sess_cfg), sess_cfg,
                               attached=live.get(session, False))
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

    named = set(profile["sessions"])
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


def cmd_close(args: argparse.Namespace, config: ConfigDict, profile_name: str) -> int:
    """Kill one tmux session, which closes its window with it."""
    session = validate_session_name(args.close)
    live = tmux_list_sessions()
    if session not in live:
        logger.error("No tmux session named %r is running.", session)
        return EXIT_ERROR
    if not tmux_kill_session(session):
        logger.error("Could not kill tmux session %r.", session)
        return EXIT_ERROR
    logger.info("Closed %s", session)
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
    targets = [s for s in profile["sessions"] if s in live]
    if not targets:
        logger.info("No sessions from profile %s are running.", profile_name)
        return EXIT_OK
    if not confirm("Close %d session(s) from profile %s (%s)?"
                   % (len(targets), profile_name, ", ".join(targets)), args.yes):
        logger.info("Cancelled.")
        return EXIT_OK
    failures = [s for s in targets if not tmux_kill_session(s)]
    for session in targets:
        if session not in failures:
            logger.info("Closed %s", session)
    if failures:
        logger.error("Could not close: %s", ", ".join(failures))
        return EXIT_ERROR
    return EXIT_OK


def cmd_focus(args: argparse.Namespace, config: ConfigDict, profile_name: str) -> int:
    """Raise and focus one session's window."""
    session = validate_session_name(args.focus)
    tool = require_geometry_tool()
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
    if assume_yes or DRY_RUN:
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
    updated = added = 0
    for session, geometry in layout.items():
        existing = profile["sessions"].get(session)
        if isinstance(existing, dict):
            # Preserve command/cwd/xterm_args: only placement is captured
            # from the screen, and silently dropping launch settings would
            # be a destructive surprise.
            existing.update(geometry)
            updated += 1
        else:
            profile["sessions"][session] = geometry
            added += 1
        logger.debug("Captured %s at x=%s y=%s %sx%s", session, geometry["x"],
                     geometry["y"], geometry["width"], geometry["height"])
    save_config(config)
    logger.info("Profile %s updated: %d session(s) updated, %d added. Saved to %s",
                profile_name, updated, added, CONFIG_FILE)
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

    config["profiles"][new_name] = {"stack": base_stack, "sessions": layout}
    save_config(config)
    logger.info("Created profile %s with %d session(s) captured from the current "
                "layout. Saved to %s", new_name, len(layout), CONFIG_FILE)
    return EXIT_OK


def cmd_set(args: argparse.Namespace, config: ConfigDict, profile_name: str) -> int:
    """Add or update one session's position in the profile."""
    profile = get_profile(config, profile_name)
    session = validate_session_name(args.set[0])
    geometry = parse_geometry_spec(args.set[1])
    existing = profile["sessions"].get(session)
    if isinstance(existing, dict):
        existing.update(geometry)
        action = "Updated"
    else:
        profile["sessions"][session] = geometry
        action = "Added"
    save_config(config)
    logger.info("%s %s in profile %s: x=%s y=%s %sx%s", action, session,
                profile_name, geometry["x"], geometry["y"],
                geometry["width"], geometry["height"])
    return EXIT_OK


def cmd_delete_session(args: argparse.Namespace, config: ConfigDict,
                       profile_name: str) -> int:
    """Remove one session's entry from the profile.

    This edits configuration only. A running session of the same name
    keeps running; use --close to kill it.
    """
    profile = get_profile(config, profile_name)
    session = args.delete_session
    if session not in profile["sessions"]:
        logger.error("Session %r is not configured in profile %s.",
                     session, profile_name)
        return EXIT_ERROR
    del profile["sessions"][session]
    save_config(config)
    logger.info("Removed %s from profile %s. Any running session of that name is "
                "untouched; use --close to end it.", session, profile_name)
    return EXIT_OK


def cmd_delete_profile(args: argparse.Namespace, config: ConfigDict,
                       profile_name: str) -> int:
    """Delete a whole profile from the config."""
    name = validate_profile_name(args.delete_profile)
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
    logger.info("Deleted profile %s from %s", name, CONFIG_FILE)
    return EXIT_OK


def clear_current_profile_state() -> None:
    """Forget the persisted current profile for this machine."""
    if DRY_RUN:
        logger.info("[dry-run] would clear %s", STATE_FILE)
        return
    try:
        if STATE_FILE.exists():
            STATE_FILE.unlink()
    except OSError as e:
        logger.warning("Could not clear %s: %s", STATE_FILE, e)


def cmd_copy_profile(args: argparse.Namespace, config: ConfigDict,
                     profile_name: str) -> int:
    """Duplicate an existing profile under a new name."""
    source, target = args.copy_profile
    validate_profile_name(source)
    validate_profile_name(target)
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
    """
    editor = os.environ.get("VISUAL") or os.environ.get("EDITOR") or "vi"
    if not CONFIG_FILE.exists():
        _write_text_safe(CONFIG_FILE, dump_yaml(DEFAULT_CONFIG), "config file")
    if DRY_RUN:
        logger.info("[dry-run] would open %s in %s", CONFIG_FILE, editor)
        return EXIT_OK
    logger.debug("Opening %s in %s", CONFIG_FILE, editor)
    try:
        subprocess.call([editor, str(CONFIG_FILE)])
    except OSError as e:
        die("Could not start editor %r: %s" % (editor, e))
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
            "config": str(CONFIG_FILE),
            "valid": not bad,
            "profiles": [{"name": name, "valid": error is None, "error": error}
                         for name, error in results],
        })
    else:
        print("Config: %s" % (CONFIG_FILE,))
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
    for session, cfg in profile["sessions"].items():
        entry = {
            "name": session,
            "status": session_status(session, live),
            "configured": session_geometry(cfg),
            "command": cfg.get("command"),
            "cwd": cfg.get("cwd"),
            "live": None,
        }
        if live.get(session) and tool:
            entry["live"] = get_window_geometry(session, tool)
        sessions.append(entry)

    named = set(profile["sessions"])
    strays = sorted(name for name, attached in live.items()
                    if attached and name not in named)
    return {
        "profile": profile_name,
        "config": str(CONFIG_FILE),
        "state_file": str(STATE_FILE),
        "geometry_tool": tool,
        "frame_compensation": FRAME_COMPENSATION,
        "sessions": sessions,
        "strays": strays,
    }


def cmd_list(args: argparse.Namespace, config: ConfigDict, profile_name: str) -> int:
    """Show the status of every session in the profile."""
    status = collect_status(config, profile_name)
    if args.json:
        emit_json(status)
        return EXIT_OK

    print("Profile: %s" % (status["profile"],))
    print("Config:  %s" % (status["config"],))
    print("")
    print("Named sessions:")
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
        if entry["command"]:
            print("  %-20s %-12s command:    %s" % ("", "", entry["command"]))
    print("")
    print("Stray attached sessions (no configured position):")
    if not status["strays"]:
        print("  (none)")
    for session in status["strays"]:
        print("  %s" % (session,))
    if not status["geometry_tool"]:
        print("")
        print("(Install or locate %s to show live window positions here.)"
              % (", ".join(GEOMETRY_TOOLS),))
    return EXIT_OK


def cmd_current_profile(args: argparse.Namespace, config: ConfigDict,
                        profile_name: str) -> int:
    """Print the profile this machine is currently using."""
    current = read_current_profile_state()
    if args.json:
        emit_json({"current_profile": current, "resolved_profile": profile_name,
                   "state_file": str(STATE_FILE)})
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
    current = read_current_profile_state()
    effective = current or profile_name
    profiles = config.get("profiles", {})
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
                }
                for name in sorted(profiles)
            ],
        })
        return EXIT_OK
    for name in sorted(profiles):
        print(name + (" *" if name == effective else ""))
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
    ("close", cmd_close, False, False),
    ("close_all", cmd_close_all, False, True),
    ("focus", cmd_focus, False, False),
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
    ("edit", cmd_edit, True, False),
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

    general = parser.add_argument_group("general options")
    general.add_argument("-p", "--profile", "-profile", metavar="NAME",
                         help="Use profile NAME and persist it as this "
                              "machine's current profile.")
    general.add_argument("-a", "--auto", action="store_true",
                         help="Ignore the saved current profile and select one "
                              "by matching hostname/DISPLAY instead.")
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
    action.add_argument("-e", "--edit", action="store_true",
                        help="Open the config in $EDITOR, then validate it.")
    action.add_argument("-V", "--validate", action="store_true",
                        help="Check every profile in the config file.")
    return parser


def apply_runtime_flags(args: argparse.Namespace) -> None:
    """Publish the cross-cutting run modes taken from parsed arguments."""
    global DRY_RUN, VERIFY, FRAME_COMPENSATION
    DRY_RUN = bool(args.dry_run)
    VERIFY = bool(args.verify)
    FRAME_COMPENSATION = not args.no_frame_compensation


def main(argv: Optional[List[str]] = None) -> int:
    """Parse arguments, resolve the profile, and run the chosen action."""
    parser = build_parser()
    args = parser.parse_args(argv)

    setup_logging(console_level_from_args(args), args.log_file)
    apply_runtime_flags(args)
    resolve_paths(args.config, args.state_dir)
    logger.debug("xtm %s starting on python %s; args=%s", __version__,
                 platform.python_version(), vars(args))
    logger.debug("Config file: %s | state file: %s | PyYAML: %s",
                 CONFIG_FILE, STATE_FILE, "yes" if HAVE_PYYAML else "no")
    if DRY_RUN:
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
        profile_name = resolve_profile_name(config, args.profile, args.auto)
    except XtmError:
        if needs_profile or args.profile:
            raise
        logger.debug("No profile could be resolved, and this command does not "
                     "need one; continuing.")
        profile_name = None
    logger.debug("Resolved profile: %s", profile_name)
    return handler(args, config, profile_name)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except XtmError as e:
        # The one-line message always shows; the traceback is additionally
        # available under --debug.
        logger.error("%s", e)
        logger.debug("Full exception detail:", exc_info=True)
        sys.exit(EXIT_ERROR)
    except KeyboardInterrupt:
        logger.debug("Interrupted by user (KeyboardInterrupt).")
        print("", file=sys.stderr)
        logger.warning("Interrupted.")
        sys.exit(EXIT_INTERRUPTED)
