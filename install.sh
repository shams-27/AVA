#!/usr/bin/env bash
# ==============================================================================
# AVA bootstrap (Linux / macOS)
#
# Usage:
#   curl -fsSL https://raw.githubusercontent.com/shams-27/AVA/main/install.sh | bash
# ==============================================================================
set -euo pipefail

AVA_RAW_URL="https://raw.githubusercontent.com/shams-27/AVA/main/AVA.py"

TMP_DIR="$(mktemp -d)"
AVA_PATH="$TMP_DIR/AVA.py"
cleanup() { rm -rf "$TMP_DIR"; }
trap cleanup EXIT

info()  { printf '  \033[36mi\033[0m  %s\n' "$1"; }
ok()    { printf '  \033[32m✔\033[0m  %s\n' "$1"; }
err()   { printf '  \033[31m✖\033[0m  %s\n' "$1" >&2; }

# --- 1. Locate an existing Python 3 interpreter -----------------------------
find_python() {
    for candidate in python3 python; do
        if command -v "$candidate" >/dev/null 2>&1; then
            ver="$("$candidate" -c 'import sys; print(sys.version_info[0])' 2>/dev/null || echo 0)"
            if [ "$ver" = "3" ]; then
                echo "$candidate"
                return 0
            fi
        fi
    done
    return 1
}

PYTHON_BIN="$(find_python || true)"

# --- 2. Install Python 3 if missing, using the available package manager ----
if [ -z "$PYTHON_BIN" ]; then
    info "Python 3 not found. Attempting to install it..."

    OS="$(uname -s)"

    if [ "$OS" = "Darwin" ]; then
        if command -v brew >/dev/null 2>&1; then
            brew install python3
        else
            err "Homebrew not found. Install it from https://brew.sh, then re-run this script."
            exit 1
        fi
    elif [ "$OS" = "Linux" ]; then
        if command -v apt-get >/dev/null 2>&1; then
            sudo apt-get update -qq
            sudo apt-get install -y python3 python3-pip
        elif command -v dnf >/dev/null 2>&1; then
            sudo dnf install -y python3 python3-pip
        elif command -v pacman >/dev/null 2>&1; then
            sudo pacman -Sy --noconfirm python
        elif command -v zypper >/dev/null 2>&1; then
            sudo zypper install -y python3 python3-pip
        elif command -v apk >/dev/null 2>&1; then
            sudo apk add python3 py3-pip
        else
            err "Could not detect a supported package manager (apt/dnf/pacman/zypper/apk)."
            err "Please install Python 3 manually, then re-run this script."
            exit 1
        fi
    else
        err "Unsupported OS: $OS"
        exit 1
    fi

    PYTHON_BIN="$(find_python || true)"
    if [ -z "$PYTHON_BIN" ]; then
        err "Python 3 installation appears to have failed. Please install manually."
        exit 1
    fi
    ok "Python 3 installed successfully."
else
    info "Python 3 found: $($PYTHON_BIN --version)"
fi

# --- 3. Download AVA.py ------------------------------------------------------
info "Downloading AVA..."
if command -v curl >/dev/null 2>&1; then
    curl -fsSL "$AVA_RAW_URL" -o "$AVA_PATH"
elif command -v wget >/dev/null 2>&1; then
    wget -q "$AVA_RAW_URL" -O "$AVA_PATH"
else
    err "Neither curl nor wget is available to download AVA.py."
    exit 1
fi

# --- 4. Launch AVA -----------------------------------------------------------
# stdin is bound directly to the tty so the interactive menu still works
# even though this script was invoked via 'curl | bash'.
"$PYTHON_BIN" "$AVA_PATH" "$@" < /dev/tty
