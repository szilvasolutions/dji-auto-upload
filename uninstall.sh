#!/usr/bin/env bash
# dji-auto-upload uninstaller for Linux and macOS.
#
#   curl -fsSL https://raw.githubusercontent.com/szilvasolutions/dji-auto-upload/main/uninstall.sh | bash
#
# Stops the watcher, removes the trigger (udev rule / LaunchAgent), deletes
# settings, and uninstalls the package.
#
# Your footage is NOT deleted. rclone and its remotes are NOT touched.

set -euo pipefail

# Wrapped in main() so the whole script is parsed before anything runs — it is
# usually piped straight from curl.
main() {
    step() { printf '\033[36m==> %s\033[0m\n' "$*"; }

    echo
    echo "dji-auto-upload uninstaller"
    echo "---------------------------"

    local BIN=""
    if command -v dji-auto-upload >/dev/null 2>&1; then
        BIN="dji-auto-upload"
    elif command -v python3 >/dev/null 2>&1 && python3 -c 'import dji_auto_upload' 2>/dev/null; then
        BIN="python3 -m dji_auto_upload"
    fi

    # Find the footage folder BEFORE removing anything, so it can be reported.
    local FOOTAGE=""
    if [ -n "$BIN" ]; then
        FOOTAGE=$($BIN status 2>/dev/null | awk '/stage dir/ {print $3}' || true)
    fi

    # --- Let the tool clean up after itself while it still knows its paths -----
    if [ -n "$BIN" ]; then
        step "Removing trigger and settings"
        $BIN uninstall --yes || true
    else
        step "dji-auto-upload not importable - cleaning up by hand"
    fi

    # --- Stop anything still resident ------------------------------------------
    step "Stopping any watcher still running"
    pkill -f 'dji_auto_upload.*_watch' 2>/dev/null || true

    # --- Triggers the CLI may not have been able to remove ---------------------
    if [ "$(uname)" = "Darwin" ]; then
        launchctl bootout "gui/$(id -u)/com.dji-auto-upload.watcher" 2>/dev/null || true
        rm -f "$HOME/Library/LaunchAgents/com.dji-auto-upload.watcher.plist"
    else
        if [ -f /etc/udev/rules.d/99-dji-auto-upload.rules ]; then
            step "Removing the udev rule (needs sudo)"
            if command -v sudo >/dev/null 2>&1; then
                sudo rm -f /etc/udev/rules.d/99-dji-auto-upload.rules
                sudo udevadm control --reload-rules 2>/dev/null || true
            else
                echo "  no sudo available - remove it yourself:"
                echo "    sudo rm /etc/udev/rules.d/99-dji-auto-upload.rules"
            fi
        fi
    fi

    # --- The package ------------------------------------------------------------
    step "Uninstalling the package"
    python3 -m pip uninstall -y dji-auto-upload 2>/dev/null \
        || python3 -m pip uninstall -y --break-system-packages dji-auto-upload 2>/dev/null \
        || true

    # --- Leftover settings/logs (never the footage) -----------------------------
    if [ "$(uname)" = "Darwin" ]; then
        rm -rf "$HOME/Library/Application Support/dji-auto-upload" \
               "$HOME/Library/Logs/dji-auto-upload" 2>/dev/null || true
    else
        rm -rf "${XDG_CONFIG_HOME:-$HOME/.config}/dji-auto-upload" \
               "${XDG_STATE_HOME:-$HOME/.local/state}/dji-auto-upload" 2>/dev/null || true
    fi

    echo
    if command -v dji-auto-upload >/dev/null 2>&1; then
        printf '\033[33mMostly done — the command is still on PATH (another Python may have it).\033[0m\n'
    else
        printf '\033[32mUninstalled.\033[0m\n'
    fi
    if [ -n "$FOOTAGE" ] && [ -d "$FOOTAGE" ]; then
        printf '\033[36mYour footage is still at %s\033[0m\n' "$FOOTAGE"
    fi
    printf '\033[90mrclone and its remotes were left alone.\033[0m\n'
}

main "$@"
