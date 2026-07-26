#!/usr/bin/env python3
# ==============================================================================
# AVA — Terminal Assistant (Linux & Windows)
# Interactive dev-environment setup tool. Standard library only.
#
#   python3 ava.py                 (Linux / macOS)
#   py ava.py                      (Windows)
#   python3 ava.py --force-windows (test Windows-only menu items elsewhere)
# ==============================================================================

from __future__ import annotations

import contextlib
import ctypes
import os
import platform
import re
import select
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request
from pathlib import Path
from typing import Callable

AVA_VERSION = "0.12.0"
VSCODE_PROFILE_DEFAULT_URL = (
    "https://github.com/shams-27/VS-Code-Profile/blob/main/shams_vscode.code-profile"
)
GCC_SCRIPT_URL = "https://raw.githubusercontent.com/ShamsKabir/tools/main/shams_gcc.ps1"

IS_WINDOWS = platform.system() == "Windows"
IS_MACOS = platform.system() == "Darwin"
IS_LINUX = platform.system() == "Linux"

# --- Test hook -------------------------------------------------------------
# Simulates Windows detection on any OS to exercise Windows-only menu items
# (e.g. Configure GCC) without a real Windows machine. Actual Windows-only
# commands (winget/choco/powershell) still fail cleanly since they don't exist.
FORCE_WINDOWS = "--force-windows" in sys.argv
if FORCE_WINDOWS:
    sys.argv.remove("--force-windows")
    IS_WINDOWS = True

# Keyed off the real OS, not IS_WINDOWS, so --force-windows testing doesn't
# try to import termios/tty or msvcrt on the wrong platform.
_REAL_WINDOWS = platform.system() == "Windows"

if not _REAL_WINDOWS:
    import termios
    import tty


# ------------------------------------------------------------------------------
# Terminal capability and color setup
# ------------------------------------------------------------------------------
def _enable_windows_vt() -> bool:
    """Turn on ANSI/VT100 escape processing in the Windows console."""
    if not IS_WINDOWS:
        return True
    try:
        kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
        ENABLE_VIRTUAL_TERMINAL_PROCESSING = 0x0004
        handle = kernel32.GetStdHandle(-11)  # STD_OUTPUT_HANDLE
        mode = ctypes.c_uint32()
        if not kernel32.GetConsoleMode(handle, ctypes.byref(mode)):
            return False
        new_mode = mode.value | ENABLE_VIRTUAL_TERMINAL_PROCESSING
        return bool(kernel32.SetConsoleMode(handle, new_mode))
    except Exception:
        return False


_TTY = sys.stdout.isatty()
_COLOR_OK = _TTY and (_enable_windows_vt() if IS_WINDOWS else True)


class C:
    CYAN = "\033[38;5;51m" if _COLOR_OK else ""
    PURPLE = "\033[38;5;141m" if _COLOR_OK else ""
    GREEN = "\033[38;5;71m" if _COLOR_OK else ""
    RED = "\033[38;5;196m" if _COLOR_OK else ""
    YELLOW = "\033[38;5;220m" if _COLOR_OK else ""
    GRAY = "\033[38;5;244m" if _COLOR_OK else ""
    DARK = "\033[38;5;238m" if _COLOR_OK else ""
    BOLD = "\033[1m" if _COLOR_OK else ""
    DIM = "\033[2m" if _COLOR_OK else ""
    RESET = "\033[0m" if _COLOR_OK else ""


# ------------------------------------------------------------------------------
# Unicode glyph support detection.
# Legacy Windows conhost (classic cmd.exe/PowerShell without Windows
# Terminal) often can't render box-drawing/symbol glyphs correctly. Detect
# known modern-terminal markers and fall back to plain ASCII otherwise.
# ------------------------------------------------------------------------------
def _detect_unicode_support() -> bool:
    if not IS_WINDOWS:
        return True  # Linux/macOS terminals overwhelmingly handle this fine.

    # Windows Terminal sets WT_SESSION; ConEmu sets ConEmuANSI; VS Code's
    # integrated terminal (and other modern hosts) set TERM_PROGRAM.
    if os.environ.get("WT_SESSION") or os.environ.get("WT_PROFILE_ID"):
        return True
    if os.environ.get("ConEmuANSI") == "ON":
        return True
    if os.environ.get("TERM_PROGRAM"):
        return True
    return False


UNICODE_OK = _detect_unicode_support()

if UNICODE_OK:
    ICON_SUCCESS = "\u2714"
    ICON_WARN = "\u26a0"
    ICON_ERROR = "\u2716"
    ICON_BOLT = "\u26a1"
    ICON_PROMPT = "\u276f\u276f"
    EM_DASH = "\u2014"
    BOLT_IS_WIDE = True  # ⚡ renders wider than one column in most terminals
    BOX_TL, BOX_TR, BOX_BL, BOX_BR = "\u256d", "\u256e", "\u2570", "\u256f"
    BOX_ML, BOX_MR = "\u251c", "\u2524"
    BOX_H, BOX_V = "\u2500", "\u2502"
else:
    ICON_SUCCESS = "+"
    ICON_WARN = "!"
    ICON_ERROR = "x"
    ICON_BOLT = "*"
    ICON_PROMPT = ">>"
    EM_DASH = "-"
    BOLT_IS_WIDE = False
    BOX_TL, BOX_TR, BOX_BL, BOX_BR = "+", "+", "+", "+"
    BOX_ML, BOX_MR = "+", "+"
    BOX_H, BOX_V = "-", "|"


# ------------------------------------------------------------------------------
# Logging helpers
# ------------------------------------------------------------------------------
def log_info(msg: str) -> None:
    print(f" {C.CYAN}i{C.RESET}  {msg}")


def log_success(msg: str) -> None:
    print(f" {C.GREEN}{ICON_SUCCESS}{C.RESET}  {msg}")


def log_warn(msg: str) -> None:
    print(f" {C.YELLOW}{ICON_WARN}{C.RESET}  {msg}", file=sys.stderr)


def log_error(msg: str) -> None:
    print(f" {C.RED}{ICON_ERROR}{C.RESET}  {msg}", file=sys.stderr)


def clear_screen() -> None:
    """Clears the terminal via the native console-clear command. Keyed off
    the real OS, not IS_WINDOWS, so --force-windows testing still works."""
    if not _TTY:
        return
    os.system("cls" if platform.system() == "Windows" else "clear")


# ------------------------------------------------------------------------------
# Dynamic header box (mirrors render_intro_box from the bash version)
# ------------------------------------------------------------------------------
def render_intro_box() -> None:
    box_width = 64
    inner_width = box_width - 2

    top = BOX_TL + BOX_H * inner_width + BOX_TR
    mid = BOX_ML + BOX_H * inner_width + BOX_MR
    bot = BOX_BL + BOX_H * inner_width + BOX_BR

    line1_plain = f"  {ICON_BOLT} AVA {EM_DASH} Terminal Assistant (v{AVA_VERSION})"
    wide_adjust = 1 if BOLT_IS_WIDE else 0
    pad1 = " " * max(0, inner_width - len(line1_plain) - wide_adjust)
    line2_str = "Automating your developer environment configurations."
    line2_plain = f"  {line2_str}"
    pad2 = " " * max(0, inner_width - len(line2_plain))

    print(f"{C.CYAN}{top}{C.RESET}")
    print(
        f"{C.CYAN}{BOX_V}{C.RESET}  {C.BOLD}{C.PURPLE}{ICON_BOLT} AVA{C.RESET} "
        f"{C.GRAY}{EM_DASH} Terminal Assistant{C.RESET} {C.DIM}(v{AVA_VERSION}){C.RESET}"
        f"{pad1}{C.CYAN}{BOX_V}{C.RESET}"
    )
    print(f"{C.CYAN}{mid}{C.RESET}")
    print(f"{C.CYAN}{BOX_V}{C.RESET}  {C.GRAY}{line2_str}{C.RESET}{pad2}{C.CYAN}{BOX_V}{C.RESET}")
    print(f"{C.CYAN}{bot}{C.RESET}")


# ------------------------------------------------------------------------------
# Runs a command, tailing its last output line into a small live status box,
# and reports success/failure. Falls back to a plain line when not a tty.
# ------------------------------------------------------------------------------
def run_with_status(label: str, cmd: list[str] | str, shell: bool = False) -> bool:
    log_file = tempfile.TemporaryFile(mode="w+", encoding="utf-8", errors="replace")

    try:
        proc = subprocess.Popen(
            cmd,
            shell=shell,
            stdout=log_file,
            stderr=subprocess.STDOUT,
            text=True,
        )
    except FileNotFoundError as exc:
        log_error(f"{label} failed to start: {exc}")
        log_file.close()
        return False

    box_width = 64
    inner_width = box_width - 8
    top_dashes = BOX_H * max(1, box_width - len(label) - 6)
    bot_dashes = BOX_H * (box_width - 3)

    first_render = True
    last_line = ""

    def read_last_line() -> str:
        log_file.seek(0)
        lines = [ln.strip() for ln in log_file.readlines() if ln.strip()]
        return lines[-1] if lines else ""

    while proc.poll() is None:
        if _TTY:
            status_line = read_last_line() or "Processing task..."
            status_line = re.sub(r"\s+", " ", status_line)
            if len(status_line) > inner_width:
                status_line = status_line[: inner_width - 3] + "..."

            if not first_render:
                sys.stdout.write("\033[3A")
            first_render = False

            sys.stdout.write(f"\r\033[K {C.CYAN}{BOX_TL}{BOX_H}{C.RESET} {C.BOLD}{C.PURPLE}{label}{C.RESET} {C.CYAN}{top_dashes}{BOX_TR}{C.RESET}\n")
            sys.stdout.write(f"\r\033[K {C.CYAN}{BOX_V}{C.RESET} {C.CYAN}{ICON_BOLT}{C.RESET} {status_line:<{inner_width}} {C.CYAN}{BOX_V}{C.RESET}\n")
            sys.stdout.write(f"\r\033[K {C.CYAN}{BOX_BL}{bot_dashes}{BOX_BR}{C.RESET}\n")
            sys.stdout.flush()
        time.sleep(0.08)

    proc.wait()
    exit_code = proc.returncode

    if _TTY and not first_render:
        sys.stdout.write("\033[3A\033[2K\n\033[2K\n\033[2K\033[2A\r")
        sys.stdout.flush()
    elif not _TTY:
        print(f" {label}...", end=" ")

    if exit_code != 0:
        log_error(f"{label} failed.")
        log_file.seek(0)
        tail = [ln for ln in log_file.readlines() if ln.strip()][-4:]
        if tail:
            print(f"    {C.GRAY}Error details:{C.RESET}")
            for ln in tail:
                print(f"    {ln.rstrip()}")
        log_file.close()
        return False

    log_file.close()
    return True


# ------------------------------------------------------------------------------
# Privilege handling — sudo on Linux, admin check on Windows. Windows console
# apps can't self-elevate mid-run, so we prompt the user to re-run elevated.
# ------------------------------------------------------------------------------
def is_admin() -> bool:
    if IS_WINDOWS:
        try:
            return bool(ctypes.windll.shell32.IsUserAnAdmin())  # type: ignore[attr-defined]
        except Exception:
            return False
    return os.geteuid() == 0  # type: ignore[attr-defined]


def ensure_privileges() -> list[str] | None:
    """Returns the command prefix needed for elevated actions, or None if
    privileges could not be secured."""
    if is_admin():
        return []

    if IS_WINDOWS:
        log_error("Administrator privileges are required for this action.")
        log_info("Re-run this script from an elevated (Run as Administrator) terminal.")
        return None

    if shutil.which("sudo"):
        # Prime the sudo timestamp up front so later calls don't interrupt
        # a running status box with a password prompt.
        try:
            subprocess.run(["sudo", "-v"], check=True)
        except subprocess.CalledProcessError:
            log_error("Sudo authentication failed.")
            return None
        return ["sudo"]

    log_error("Root privileges or 'sudo' are required for this action.")
    return None


# ------------------------------------------------------------------------------
# FEATURE: Download VS Code Profile
# ------------------------------------------------------------------------------
def download_vscode_profile() -> None:
    raw_url = VSCODE_PROFILE_DEFAULT_URL
    if "github.com" in raw_url and "/blob/" in raw_url:
        raw_url = raw_url.replace("github.com/", "raw.githubusercontent.com/").replace("/blob/", "/")

    download_dir = Path.home() / "Downloads"
    download_dir.mkdir(parents=True, exist_ok=True)

    filename = raw_url.rsplit("/", 1)[-1]
    output = download_dir / filename

    if output.exists():
        log_info(f"VS Code profile already exists at {C.BOLD}{C.CYAN}{output}{C.RESET}.")
        log_success("Skipping download.")
        return

    def _download() -> None:
        with urllib.request.urlopen(raw_url, timeout=30) as resp, open(output, "wb") as f:
            shutil.copyfileobj(resp, f)

    ok = _run_python_task("Downloading VS Code Profile", _download)
    if ok:
        log_success(f"Profile saved to {C.BOLD}{C.CYAN}{output}{C.RESET}")
    elif output.exists():
        output.unlink(missing_ok=True)


def _run_python_task(label: str, fn) -> bool:
    """Like run_with_status, but for a plain Python callable."""
    result: dict[str, object] = {}

    def _target() -> None:
        try:
            fn()
            result["ok"] = True
        except Exception as exc:  # noqa: BLE001
            result["ok"] = False
            result["error"] = str(exc)

    t = threading.Thread(target=_target, daemon=True)
    t.start()

    spin = "|/-\\"
    i = 0
    while t.is_alive():
        if _TTY:
            sys.stdout.write(f"\r {C.CYAN}{spin[i % len(spin)]}{C.RESET} {label}...")
            sys.stdout.flush()
            i += 1
        time.sleep(0.08)
    t.join()

    if _TTY:
        sys.stdout.write("\r\033[K")

    if not result.get("ok"):
        log_error(f"{label} failed: {result.get('error', 'unknown error')}")
        return False
    return True


# ------------------------------------------------------------------------------
# Package manager detection
# ------------------------------------------------------------------------------
LINUX_MANAGERS = ["apt-get", "dnf", "pacman", "zypper", "apk"]
WINDOWS_MANAGERS = ["winget", "choco"]


def detect_package_manager() -> str | None:
    candidates = WINDOWS_MANAGERS if IS_WINDOWS else LINUX_MANAGERS
    for pm in candidates:
        if shutil.which(pm):
            return pm
    return None


def _pm_display_name(pm: str) -> str:
    return {"apt-get": "apt"}.get(pm, pm)


# ------------------------------------------------------------------------------
# FEATURE: Configure OpenJDK
# ------------------------------------------------------------------------------
def configure_openjdk() -> None:
    if shutil.which("java"):
        log_info("OpenJDK is already installed on this system.")
        version = subprocess.run(
            ["java", "-version"], capture_output=True, text=True
        ).stderr.splitlines()
        if version:
            print(f"    {C.GRAY}Active Version:{C.RESET} {C.DIM}{version[0]}{C.RESET}")
        log_success("Skipping installation.")
        return

    prefix = ensure_privileges()
    if prefix is None:
        return

    pm = detect_package_manager()
    if pm is None:
        log_error("Could not detect a supported package manager.")
        return
    log_info(f"Package manager: {C.BOLD}{C.CYAN}{_pm_display_name(pm)}{C.RESET}")

    install_cmds: dict[str, list[list[str]]] = {
        "apt-get": [prefix + ["apt-get", "update", "-qq"], prefix + ["apt-get", "install", "-y", "default-jdk"]],
        "dnf": [prefix + ["dnf", "install", "-y", "java-17-openjdk-devel"]],
        "pacman": [prefix + ["pacman", "-S", "--noconfirm", "jdk-openjdk"]],
        "zypper": [prefix + ["zypper", "install", "-y", "java-17-openjdk-devel"]],
        "apk": [prefix + ["apk", "add", "openjdk17-jdk"]],
        "winget": [["winget", "install", "-e", "--id", "EclipseAdoptium.Temurin.17.JDK", "--silent",
                     "--accept-package-agreements", "--accept-source-agreements"]],
        "choco": [prefix + ["choco", "install", "temurin17", "-y"]],
    }

    ok = True
    for cmd in install_cmds[pm]:
        if not run_with_status("Installing OpenJDK", cmd):
            ok = False
            break

    if ok:
        log_success("OpenJDK configured successfully!")
        log_info("Open a new terminal so PATH changes take effect if 'java' isn't found yet.")


# ------------------------------------------------------------------------------
# FEATURE: Configure Node.js & npm
# ------------------------------------------------------------------------------
def configure_nodejs() -> None:
    if shutil.which("node"):
        log_info("Node.js is already installed on this system.")
        node_v = subprocess.run(["node", "-v"], capture_output=True, text=True).stdout.strip()
        print(f"    {C.GRAY}Node Version:{C.RESET} {C.DIM}{node_v}{C.RESET}")
        if shutil.which("npm"):
            npm_v = subprocess.run(["npm", "-v"], capture_output=True, text=True, shell=IS_WINDOWS).stdout.strip()
            print(f"    {C.GRAY}npm Version:{C.RESET}  {C.DIM}{npm_v}{C.RESET}")
        log_success("Skipping installation.")
        return

    prefix = ensure_privileges()
    if prefix is None:
        return

    pm = detect_package_manager()
    if pm is None:
        log_error("Could not detect a supported package manager.")
        return
    log_info(f"Package manager: {C.BOLD}{C.CYAN}{_pm_display_name(pm)}{C.RESET}")

    install_cmds: dict[str, list[list[str]]] = {
        "apt-get": [prefix + ["apt-get", "update", "-qq"], prefix + ["apt-get", "install", "-y", "nodejs", "npm"]],
        "dnf": [prefix + ["dnf", "install", "-y", "nodejs", "npm"]],
        "pacman": [prefix + ["pacman", "-S", "--noconfirm", "nodejs", "npm"]],
        "zypper": [prefix + ["zypper", "install", "-y", "nodejs", "npm"]],
        "apk": [prefix + ["apk", "add", "nodejs", "npm"]],
        "winget": [["winget", "install", "-e", "--id", "OpenJS.NodeJS.LTS", "--silent",
                     "--accept-package-agreements", "--accept-source-agreements"]],
        "choco": [prefix + ["choco", "install", "nodejs-lts", "-y"]],
    }

    ok = True
    for cmd in install_cmds[pm]:
        if not run_with_status("Installing Node.js & npm", cmd):
            ok = False
            break

    if ok:
        log_success("Node.js & npm configured successfully!")
        log_info("Open a new terminal so PATH changes take effect if 'node' isn't found yet.")


# ------------------------------------------------------------------------------
# FEATURE: Configure GCC (Windows only)
# Hands off to an external PowerShell script with stdio inherited directly,
# so its interactive UI draws normally; control returns once it exits.
# ------------------------------------------------------------------------------
def configure_gcc() -> None:
    if not IS_WINDOWS:
        log_error("Configure GCC is only available on Windows.")
        return

    if not shutil.which("powershell") and not shutil.which("pwsh"):
        log_error("PowerShell was not found on this system.")
        return

    ps_exe = "pwsh" if shutil.which("pwsh") else "powershell"
    ps_command = f"irm {GCC_SCRIPT_URL} | iex"

    log_info("Launching GCC configuration script...")
    print()
    try:
        subprocess.run([ps_exe, "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", ps_command])
    except FileNotFoundError:
        log_error("PowerShell was not found on this system.")
        return

    print()
    log_success(f"GCC configuration script finished {EM_DASH} back in AVA.")


# ------------------------------------------------------------------------------
# Raw single-keypress reading (arrow keys, space, enter, quit).
# Unix: cbreak mode via termios/tty. Windows: msvcrt.getch(), unbuffered.
# ------------------------------------------------------------------------------
@contextlib.contextmanager
def raw_input_mode():
    """Enables immediate single-keypress reads with no Enter or echo.
    No-op on Windows, where msvcrt.getch() already behaves this way."""
    if _REAL_WINDOWS:
        yield
        return
    fd = sys.stdin.fileno()
    old_settings = termios.tcgetattr(fd)
    try:
        tty.setcbreak(fd)
        yield
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old_settings)


def _read_key_unix() -> str:
    # Read raw bytes from the fd directly; sys.stdin's internal buffering
    # would hide already-arrived bytes from the select() peek below.
    fd = sys.stdin.fileno()
    ch = os.read(fd, 1).decode(errors="replace")
    if ch == "\x03":  # Ctrl-C
        raise KeyboardInterrupt
    if ch in ("\r", "\n"):
        return "ENTER"
    if ch == " ":
        return "SPACE"
    if ch in ("q", "Q"):
        return "QUIT"
    if ch == "\x1b":
        # Distinguish a lone Escape from an arrow-key sequence (ESC [ A/B
        # or ESC O A/B) by peeking ahead with a short timeout.
        ESC_WAIT = 0.15
        if select.select([fd], [], [], ESC_WAIT)[0]:
            ch2 = os.read(fd, 1).decode(errors="replace")
            if ch2 in ("[", "O") and select.select([fd], [], [], ESC_WAIT)[0]:
                ch3 = os.read(fd, 1).decode(errors="replace")
                return {"A": "UP", "B": "DOWN"}.get(ch3, "OTHER")
            return "OTHER"
        return "QUIT"
    return "OTHER"


def _read_key_windows() -> str:
    import msvcrt  # only importable on Windows

    ch = msvcrt.getch()
    if ch == b"\x03":  # Ctrl-C
        raise KeyboardInterrupt
    if ch in (b"\r", b"\n"):
        return "ENTER"
    if ch == b" ":
        return "SPACE"
    if ch in (b"q", b"Q"):
        return "QUIT"
    if ch == b"\x1b":
        return "QUIT"
    if ch in (b"\x00", b"\xe0"):  # extended-key prefix
        ch2 = msvcrt.getch()
        return {b"H": "UP", b"P": "DOWN"}.get(ch2, "OTHER")
    return "OTHER"


def read_key() -> str:
    """Blocks for one logical keypress. Returns one of:
    'UP', 'DOWN', 'SPACE', 'ENTER', 'QUIT', or 'OTHER' for anything else."""
    return _read_key_windows() if _REAL_WINDOWS else _read_key_unix()


def checkbox_menu(items: list[str]) -> list[int] | None:
    """Arrow-key + space multi-select menu. Returns the selected indices,
    or None if the user quit."""
    selected = [False] * len(items)
    cursor = 0

    def render() -> None:
        clear_screen()
        print()
        render_intro_box()
        print()
        print(
            f" {C.BOLD}SELECT OPTION(S){C.RESET} "
            f"{C.GRAY}(\u2191/\u2193 move \u00b7 space toggle \u00b7 enter run \u00b7 q quit):{C.RESET}"
        )
        print()
        for i, label in enumerate(items):
            checked = f"{C.GREEN}[x]{C.RESET}" if selected[i] else f"{C.GRAY}[ ]{C.RESET}"
            if i == cursor:
                pointer = f"{C.PURPLE}{ICON_PROMPT}{C.RESET}"
                label_out = f"{C.BOLD}{label}{C.RESET}"
            else:
                pointer = "  "
                label_out = label
            print(f"  {pointer} {checked} {label_out}")
        print()

    with raw_input_mode():
        if _TTY:
            sys.stdout.write("\033[?25l")  # hide cursor
            sys.stdout.flush()
        try:
            while True:
                render()
                try:
                    key = read_key()
                except KeyboardInterrupt:
                    return None
                if key == "UP":
                    cursor = (cursor - 1) % len(items)
                elif key == "DOWN":
                    cursor = (cursor + 1) % len(items)
                elif key == "SPACE":
                    selected[cursor] = not selected[cursor]
                elif key == "ENTER":
                    chosen = [i for i, s in enumerate(selected) if s]
                    if chosen:
                        return chosen
                    # Nothing checked — run just the highlighted item.
                    return [cursor]
                elif key == "QUIT":
                    return None
        finally:
            if _TTY:
                sys.stdout.write("\033[?25h")  # restore cursor
                sys.stdout.flush()


# ------------------------------------------------------------------------------
# INTERACTIVE MENU & MAIN ENTRY POINT
# ------------------------------------------------------------------------------
MENU_ITEMS: list[tuple[str, Callable[[], None]]] = [
    ("Download VS Code Profile", download_vscode_profile),
    ("Configure OpenJDK", configure_openjdk),
    ("Configure Node.js & npm", configure_nodejs),
]
if IS_WINDOWS:
    MENU_ITEMS.append(("Configure GCC", configure_gcc))


def interactive_menu() -> bool:
    """Shows the menu, runs the selection, and returns False on quit."""
    labels = [label for label, _ in MENU_ITEMS]
    chosen = checkbox_menu(labels)

    clear_screen()
    print()
    render_intro_box()
    print()

    if chosen is None:
        log_info("Exiting AVA. Have a productive day!")
        print()
        return False

    for i in chosen:
        _, handler = MENU_ITEMS[i]
        handler()
        print()

    try:
        input(f" {C.GRAY}Press Enter to return to the menu...{C.RESET}")
    except (EOFError, KeyboardInterrupt):
        print()
        return False

    return True


def main() -> None:
    if not sys.stdin.isatty():
        log_error("Interactive terminal required.")
        sys.exit(1)

    if FORCE_WINDOWS:
        log_warn(
            f"--force-windows active: simulating Windows detection on {platform.system()}. "
            "Windows-only commands (winget/choco/powershell) will fail cleanly since "
            f"they don't actually exist here {EM_DASH} this only exercises the menu/branching logic."
        )
        input(f" {C.GRAY}Press Enter to continue...{C.RESET}")

    try:
        while interactive_menu():
            pass
    except KeyboardInterrupt:
        print()
        log_info("Interrupted. Exiting AVA.")
        sys.exit(0)


if __name__ == "__main__":
    main()
