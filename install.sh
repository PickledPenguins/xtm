#!/bin/sh
#
# install.sh - install xtm
#
# Copies xtm.py to a directory on PATH as the executable "xtm", and
# optionally installs the bash completion file. Requires nothing beyond a
# POSIX shell; no network access and no package manager are used.

set -eu

PREFIX="$HOME/bin"
COMPLETION_DIR=""
INSTALL_COMPLETION=0
UNINSTALL=0

SOURCE_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)

usage() {
    cat <<EOF
Usage: install.sh [options]

Options:
  --prefix DIR        Install into DIR (default: \$HOME/bin).
  --completion        Also install the bash completion file.
  --completion-dir D  Install completion into D
                      (default: \$HOME/.local/share/bash-completion/completions).
  --uninstall         Remove a previous installation.
  -h, --help          Show this message.
EOF
}

while [ $# -gt 0 ]; do
    case "$1" in
        --prefix)
            [ $# -ge 2 ] || { echo "install.sh: --prefix needs a directory" >&2; exit 2; }
            PREFIX="$2"; shift 2 ;;
        --prefix=*)
            PREFIX="${1#*=}"; shift ;;
        --completion)
            INSTALL_COMPLETION=1; shift ;;
        --completion-dir)
            [ $# -ge 2 ] || { echo "install.sh: --completion-dir needs a directory" >&2; exit 2; }
            COMPLETION_DIR="$2"; INSTALL_COMPLETION=1; shift 2 ;;
        --completion-dir=*)
            COMPLETION_DIR="${1#*=}"; INSTALL_COMPLETION=1; shift ;;
        --uninstall)
            UNINSTALL=1; shift ;;
        -h|--help)
            usage; exit 0 ;;
        *)
            echo "install.sh: unknown option '$1'" >&2; usage >&2; exit 2 ;;
    esac
done

: "${COMPLETION_DIR:=$HOME/.local/share/bash-completion/completions}"
TARGET="$PREFIX/xtm"

if [ "$UNINSTALL" -eq 1 ]; then
    removed=0
    if [ -e "$TARGET" ]; then
        rm -f "$TARGET"
        echo "Removed $TARGET"
        removed=1
    fi
    if [ -e "$COMPLETION_DIR/xtm" ]; then
        rm -f "$COMPLETION_DIR/xtm"
        echo "Removed $COMPLETION_DIR/xtm"
        removed=1
    fi
    [ "$removed" -eq 1 ] || echo "Nothing to remove."
    echo "Configuration in ~/.config/xtm and state in ~/.local/state/xtm were left in place."
    exit 0
fi

# Verify the interpreter before installing, so a version problem is reported
# now rather than the first time the tool is run.
if ! command -v python3 >/dev/null 2>&1; then
    echo "install.sh: python3 was not found on PATH." >&2
    exit 1
fi
if ! python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 6) else 1)'; then
    echo "install.sh: python3 is older than 3.6." >&2
    exit 1
fi

[ -f "$SOURCE_DIR/xtm.py" ] || {
    echo "install.sh: xtm.py not found next to this script." >&2
    exit 1
}

mkdir -p "$PREFIX"
cp "$SOURCE_DIR/xtm.py" "$TARGET"
chmod 755 "$TARGET"
echo "Installed $TARGET"

if [ "$INSTALL_COMPLETION" -eq 1 ]; then
    if [ -f "$SOURCE_DIR/xtm-completion.bash" ]; then
        mkdir -p "$COMPLETION_DIR"
        cp "$SOURCE_DIR/xtm-completion.bash" "$COMPLETION_DIR/xtm"
        echo "Installed $COMPLETION_DIR/xtm"
        echo "Start a new shell, or source it now, to enable completion."
    else
        echo "install.sh: xtm-completion.bash not found; skipping completion." >&2
    fi
fi

# Warn rather than fail: installing to a directory that is not yet on PATH is
# a reasonable thing to do deliberately.
case ":$PATH:" in
    *":$PREFIX:"*) ;;
    *) echo "Note: $PREFIX is not on PATH. Add it with:"
       echo "  export PATH=\"$PREFIX:\$PATH\"" ;;
esac

"$TARGET" --version
