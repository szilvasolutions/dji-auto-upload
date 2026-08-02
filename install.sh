#!/usr/bin/env bash
# dji-auto-upload bootstrap for Linux and macOS.
#
#   curl -fsSL https://raw.githubusercontent.com/szilvasolutions/dji-auto-upload/main/install.sh | bash
#
# Installs rclone if missing, installs dji-auto-upload for the current user,
# then starts the interactive setup wizard, which ends by offering to install
# the plug-in auto-trigger (that step prints the sudo command on Linux).

set -euo pipefail

# Everything lives inside main() so that when this script is streamed via
# `curl | bash`, bash has parsed the whole file before the first command runs —
# otherwise a command that reads stdin could eat the rest of the script.
main() {
    step() { printf '\033[36m==> %s\033[0m\n' "$*"; }
    local ZIP_URL="https://github.com/szilvasolutions/dji-auto-upload/archive/refs/heads/main.zip"

    echo
    echo "dji-auto-upload installer"
    echo "-------------------------"

    # --- Python ---------------------------------------------------------------
    if ! command -v python3 >/dev/null 2>&1; then
        echo "python3 not found. Install it first:"
        echo "  Debian/Ubuntu: sudo apt install python3 python3-pip"
        echo "  Fedora:        sudo dnf install python3 python3-pip"
        echo "  macOS:         brew install python"
        exit 1
    fi
    if ! python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)'; then
        echo "python3 is older than 3.10 — please upgrade it first." >&2
        exit 1
    fi
    step "Python found: $(python3 --version)"

    # --- rclone ---------------------------------------------------------------
    if command -v rclone >/dev/null 2>&1; then
        step "rclone found: $(rclone version 2>/dev/null | head -1)"
    else
        step "rclone not found — installing"
        if [ "$(uname)" = "Darwin" ] && command -v brew >/dev/null 2>&1; then
            brew install rclone
        elif command -v sudo >/dev/null 2>&1; then
            curl -fsSL https://rclone.org/install.sh | sudo bash
        else
            echo "Could not install rclone automatically — install it from https://rclone.org/install/ and re-run." >&2
            exit 1
        fi
    fi

    # --- dji-auto-upload --------------------------------------------------------
    step "Installing dji-auto-upload"
    # Newer distros mark the system Python "externally managed" (PEP 668) and
    # refuse --user installs without an explicit override. Try the polite way
    # first; the override only touches ~/.local, never system site-packages.
    if ! python3 -m pip install --user --upgrade --quiet "$ZIP_URL" 2>/dev/null; then
        python3 -m pip install --user --upgrade --quiet --break-system-packages "$ZIP_URL"
    fi

    # --- Setup wizard -----------------------------------------------------------
    step "Starting setup (the wizard also offers to install the plug-in trigger)"
    echo
    # Module invocation works even when ~/.local/bin is not on PATH. When the
    # script itself is being streamed, stdin is the pipe — hand the wizard the
    # real keyboard instead. `-e /dev/tty` is true even with no controlling
    # terminal, so actually try opening it.
    if ( : < /dev/tty ) 2>/dev/null; then
        python3 -m dji_auto_upload setup < /dev/tty
    else
        echo "No terminal available — run this yourself:  python3 -m dji_auto_upload setup"
    fi
}

main "$@"
