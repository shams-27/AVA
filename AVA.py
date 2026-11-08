#!/usr/bin/env python3
# ==============================================================================
# AVA — Terminal Assistant (Linux)
# Interactive dev-environment setup tool. Standard library only.
#
#   python3 ava.py
# ==============================================================================

from __future__ import annotations

import contextlib
import json
import os
import platform
import re
import select
import shutil
import subprocess
import sys
import tempfile
import termios
import textwrap
import threading
import time
import tty
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Callable

AVA_VERSION = "1.2.0"

if platform.system() != "Linux":
    sys.stderr.write("AVA supports Linux only.\n")
    sys.exit(1)


# ------------------------------------------------------------------------------
# Terminal capability and color setup
# ------------------------------------------------------------------------------
_TTY = sys.stdout.isatty()
_COLOR_OK = _TTY


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
# Falls back to plain ASCII when the terminal's encoding isn't UTF-8.
# ------------------------------------------------------------------------------
def _detect_unicode_support() -> bool:
    return "utf" in (sys.stdout.encoding or "").lower()


UNICODE_OK = _detect_unicode_support()

if UNICODE_OK:
    ICON_SUCCESS = "\u2713"
    ICON_WARN = "\u2022"
    ICON_ERROR = "\u2715"
    ICON_BOLT = "\u25c6"
    ICON_PROMPT = "\u276f\u276f"
    EM_DASH = "\u2014"
    ARROW_UP = "\u2191"
    ARROW_DOWN = "\u2193"
    DOT = "\u00b7"
    BOLT_IS_WIDE = False  # ◆ is a normal single-width glyph in most terminals
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
    ARROW_UP = "^"
    ARROW_DOWN = "v"
    DOT = "-"
    BOLT_IS_WIDE = False
    BOX_TL, BOX_TR, BOX_BL, BOX_BR = "+", "+", "+", "+"
    BOX_ML, BOX_MR = "+", "+"
    BOX_H, BOX_V = "-", "|"


# ------------------------------------------------------------------------------
# Logging helpers
# ------------------------------------------------------------------------------
def log_info(msg: str) -> None:
    print(f"{ui_margin()} {C.CYAN}i{C.RESET}  {msg}")


def log_success(msg: str) -> None:
    print(f"{ui_margin()} {C.GREEN}{ICON_SUCCESS}{C.RESET}  {msg}")


def log_warn(msg: str) -> None:
    print(f"{ui_margin()} {C.YELLOW}{ICON_WARN}{C.RESET}  {msg}", file=sys.stderr)


def log_error(msg: str) -> None:
    print(f"{ui_margin()} {C.RED}{ICON_ERROR}{C.RESET}  {msg}", file=sys.stderr)


def clear_screen() -> None:
    """Clears the terminal."""
    if not _TTY:
        return
    os.system("clear")


# ------------------------------------------------------------------------------
# Horizontal centering. Every box/line in the UI is drawn against a nominal
# 64-column width; this computes the left margin needed to center that width
# in the current terminal, recomputed on every call so it tracks resizes.
# ------------------------------------------------------------------------------
def _term_width() -> int:
    return shutil.get_terminal_size(fallback=(80, 24)).columns


def ui_margin(width: int = 64) -> str:
    return " " * max(0, (_term_width() - width) // 2)


# ------------------------------------------------------------------------------
# Dynamic header box (mirrors render_intro_box from the bash version)
# ------------------------------------------------------------------------------
def render_intro_box() -> None:
    box_width = 64
    inner_width = box_width - 2
    margin = ui_margin(box_width)

    top = BOX_TL + BOX_H * inner_width + BOX_TR
    bot = BOX_BL + BOX_H * inner_width + BOX_BR

    line1_plain = f"  {ICON_BOLT} AVA {EM_DASH} Terminal Assistant (v{AVA_VERSION})"
    wide_adjust = 1 if BOLT_IS_WIDE else 0
    pad1 = " " * max(0, inner_width - len(line1_plain) - wide_adjust)

    print(f"{margin}{C.CYAN}{top}{C.RESET}")
    print(
        f"{margin}{C.CYAN}{BOX_V}{C.RESET}  {C.BOLD}{C.PURPLE}{ICON_BOLT} AVA{C.RESET} "
        f"{C.GRAY}{EM_DASH} Terminal Assistant{C.RESET} {C.DIM}(v{AVA_VERSION}){C.RESET}"
        f"{pad1}{C.CYAN}{BOX_V}{C.RESET}"
    )
    print(f"{margin}{C.CYAN}{bot}{C.RESET}")


# ------------------------------------------------------------------------------
# Tool-availability check — powers the "not installed" indicator drawn next
# to each list item in the menu box, so the user can see at a glance which
# tools are missing on this host before picking options.
#
# Derived automatically from MENU_ITEMS (defined further down) rather than a
# separate hand-maintained list: a new menu item only needs check_bin set on
# its MenuEntry, and it's covered here for free.
# ------------------------------------------------------------------------------
def _tool_availability() -> list[tuple[str, bool]]:
    return [
        (entry.name, shutil.which(entry.check_bin) is not None)
        for entry in MENU_ITEMS
        if entry.check_bin
    ]


# ------------------------------------------------------------------------------
# Combined title + option-list box — one continuous frame, used for the live
# interactive selection screen. Items are shown under group headings, each row
# has a checkbox on the left and a right-aligned status (installed version or
# "not installed"), and a footer shows the key hints plus a selection count.
# ------------------------------------------------------------------------------
def render_full_menu(items: list[str], selected: list[bool], cursor: int) -> None:
    box_width = 64
    inner_width = box_width - 2
    margin = ui_margin(box_width)

    top = BOX_TL + BOX_H * inner_width + BOX_TR
    mid = BOX_ML + BOX_H * inner_width + BOX_MR
    bot = BOX_BL + BOX_H * inner_width + BOX_BR
    edge = f"{C.CYAN}{BOX_V}{C.RESET}"

    def print_row(plain: str, colored: str) -> None:
        """Prints one boxed row; `plain` is the same text without color codes,
        used only to measure padding so the right-hand border lines up."""
        pad = " " * max(0, inner_width - len(plain))
        print(f"{margin}{edge}{colored}{pad}{edge}")

    # --- Title row ---
    title_plain = f"  {ICON_BOLT} AVA {EM_DASH} Terminal Assistant (v{AVA_VERSION})"
    wide_adjust = 1 if BOLT_IS_WIDE else 0
    title_colored = (
        f"  {C.BOLD}{C.PURPLE}{ICON_BOLT} AVA{C.RESET} "
        f"{C.GRAY}{EM_DASH} Terminal Assistant{C.RESET} {C.DIM}(v{AVA_VERSION}){C.RESET}"
    )
    print(f"{margin}{C.CYAN}{top}{C.RESET}")
    print_row(title_plain + " " * wide_adjust, title_colored)
    print(f"{margin}{C.CYAN}{mid}{C.RESET}")

    missing_tools = {name for name, ok in _tool_availability() if not ok}

    # --- Items, grouped under headings ---
    last_group: str | None = None
    for i, label in enumerate(items):
        entry = MENU_ITEMS[i]

        if entry.group != last_group:
            if last_group is not None:
                print_row("", "")
            if entry.group:
                heading = entry.group.upper()
                print_row(f"  {heading}", f"  {C.BOLD}{C.GRAY}{heading}{C.RESET}")
            last_group = entry.group

        is_cursor = i == cursor
        pointer_plain = ICON_PROMPT if is_cursor else "  "
        checked_plain = "●" if selected[i] else "○"

        # Right-aligned status: installed version (cached, so it's only
        # fetched once per menu session), "not installed", or nothing for
        # entries that aren't tied to a binary.
        is_missing = label in missing_tools
        is_installed = entry.check_bin is not None and not is_missing
        if is_missing:
            status_plain = "not installed"
        elif is_installed:
            version = _cached_version(entry)
            status_plain = f"{ICON_SUCCESS} {version}" if version else f"{ICON_SUCCESS} installed"
        else:
            status_plain = ""

        left_plain = f"    {pointer_plain} {checked_plain} {label}"
        right_len = len(status_plain) + 2 if status_plain else 0
        if len(left_plain) + right_len + 1 > inner_width:
            # Defensive: shorten the label (not the status) so the border
            # always lines up.
            keep = max(1, inner_width - right_len - 1 - len(left_plain) + len(label) - 3)
            label = label[:keep] + "..."
            left_plain = f"    {pointer_plain} {checked_plain} {label}"
        gap = " " * max(1, inner_width - len(left_plain) - right_len)

        pointer_colored = f"{C.PURPLE}{ICON_PROMPT}{C.RESET}" if is_cursor else "  "
        checked_colored = f"{C.GREEN}●{C.RESET}" if selected[i] else f"{C.GRAY}○{C.RESET}"
        label_colored = f"{C.BOLD}{label}{C.RESET}" if is_cursor else label
        if is_missing:
            status_colored = f"{C.YELLOW}{status_plain}{C.RESET}"
        elif status_plain:
            status_colored = f"{C.GREEN}{status_plain}{C.RESET}"
        else:
            status_colored = ""

        plain = left_plain + gap + (status_plain + "  " if status_plain else "")
        colored = (
            f"    {pointer_colored} {checked_colored} {label_colored}{gap}"
            + (status_colored + "  " if status_plain else "")
        )
        print_row(plain, colored)

    # --- Footer: key hints + selection count ---
    print(f"{margin}{C.CYAN}{mid}{C.RESET}")
    hint_plain = f"  {ARROW_UP}{ARROW_DOWN} move {DOT} space select {DOT} enter run {DOT} q quit"
    print_row(hint_plain, f"  {C.GRAY}{hint_plain.strip()}{C.RESET}")

    count = sum(selected)
    if count:
        noun = "task" if count == 1 else "tasks"
        note_plain = f"  {count} {noun} selected {DOT} enter runs them"
        note_colored = f"  {C.GREEN}{count} {noun} selected{C.RESET} {C.GRAY}{DOT} enter runs them{C.RESET}"
    else:
        note_plain = "  Nothing selected: enter runs the highlighted item"
        note_colored = f"  {C.DIM}{note_plain.strip()}{C.RESET}"
    print_row(note_plain, note_colored)
    print(f"{margin}{C.CYAN}{bot}{C.RESET}")


# ------------------------------------------------------------------------------
# Runs a command, tailing its last output line into a small live status box,
# and reports success/failure. Falls back to a plain line when not a tty.
# ------------------------------------------------------------------------------
def run_with_status(label: str, cmd: list[str] | str, shell: bool = False, capture: dict | None = None) -> bool:
    """capture: when given, the command's full output and exit code are stored in it
    (keys 'text' and 'code') and no error report is printed, so the caller can show them."""
    log_file = tempfile.TemporaryFile(mode="w+", encoding="utf-8", errors="replace")

    try:
        proc = subprocess.Popen(
            cmd,
            shell=shell,
            stdin=subprocess.DEVNULL if capture is not None else None,
            stdout=log_file,
            stderr=subprocess.STDOUT,
            text=True,
        )
    except FileNotFoundError as exc:
        log_error(f"{label} failed to start: {exc}")
        log_file.close()
        return False

    box_width = 64
    inner_width = box_width - 7
    margin = ui_margin(box_width)
    top_dashes = BOX_H * max(1, box_width - len(label) - 6)
    bot_dashes = BOX_H * (box_width - 3)

    first_render = True
    last_line = ""

    def read_last_line() -> str:
        log_file.seek(0)
        # Strip ANSI escape codes (e.g. from `ollama pull` progress output).
        lines = [re.sub(r"\x1b\[[0-9;?]*[ -/]*[@-~]", "", ln).strip() for ln in log_file.readlines()]
        lines = [ln for ln in lines if ln]
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

            sys.stdout.write(f"\r\033[K{margin} {C.CYAN}{BOX_TL}{BOX_H}{C.RESET} {C.BOLD}{C.PURPLE}{label}{C.RESET} {C.CYAN}{top_dashes}{BOX_TR}{C.RESET}\n")
            sys.stdout.write(f"\r\033[K{margin} {C.CYAN}{BOX_V}{C.RESET} {C.CYAN}{ICON_BOLT}{C.RESET} {status_line:<{inner_width}} {C.CYAN}{BOX_V}{C.RESET}\n")
            sys.stdout.write(f"\r\033[K{margin} {C.CYAN}{BOX_BL}{bot_dashes}{BOX_BR}{C.RESET}\n")
            sys.stdout.flush()
        time.sleep(0.08)

    proc.wait()
    exit_code = proc.returncode

    if _TTY and not first_render:
        sys.stdout.write("\033[3A\033[2K\n\033[2K\n\033[2K\033[2A\r")
        sys.stdout.flush()
    elif not _TTY:
        print(f"{margin} {label}...", end=" ")

    if capture is not None:
        log_file.seek(0)
        capture["text"] = log_file.read()
        capture["code"] = exit_code
        log_file.close()
        return exit_code == 0

    if exit_code != 0:
        log_error(f"{label} failed.")
        log_file.seek(0)
        tail = [ln for ln in log_file.readlines() if ln.strip()][-4:]
        if tail:
            print(f"{margin}    {C.GRAY}Error details:{C.RESET}")
            for ln in tail:
                print(f"{margin}    {ln.rstrip()}")
        log_file.close()
        return False

    log_file.close()
    return True


# ------------------------------------------------------------------------------
# Privilege handling — root check and sudo.
# ------------------------------------------------------------------------------
def is_admin() -> bool:
    return os.geteuid() == 0


def ensure_privileges() -> list[str] | None:
    """Returns the command prefix needed for elevated actions, or None if
    privileges could not be secured."""
    if is_admin():
        return []

    if shutil.which("sudo"):
        # If sudo already has a cached, unexpired auth timestamp, this
        # succeeds without showing a password prompt at all.
        already_cached = subprocess.run(["sudo", "-n", "-v"], capture_output=True).returncode == 0

        # Prime the sudo timestamp up front so later calls don't interrupt
        # a running status box with a password prompt.
        try:
            subprocess.run(["sudo", "-v"], check=True)
        except subprocess.CalledProcessError:
            log_error("Sudo authentication failed.")
            return None

        # sudo prints its own "[sudo] password for USER:" prompt straight
        # to the terminal; once authenticated, erase that line so it
        # doesn't linger in AVA's output.
        if _TTY and not already_cached:
            sys.stdout.write("\033[1A\033[2K")
            sys.stdout.flush()

        return ["sudo"]

    log_error("Root privileges or 'sudo' are required for this action.")
    return None


# ------------------------------------------------------------------------------
# Runs a plain Python callable in a thread with a spinner (used by Ask AVA).
# ------------------------------------------------------------------------------
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
            sys.stdout.write(f"\r{ui_margin()} {C.CYAN}{spin[i % len(spin)]}{C.RESET} {label}...")
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


def detect_package_manager() -> str | None:
    for pm in LINUX_MANAGERS:
        if shutil.which(pm):
            return pm
    return None


def _pm_display_name(pm: str) -> str:
    return {"apt-get": "apt"}.get(pm, pm)


# ------------------------------------------------------------------------------
# FEATURE: Generic CLI-tool installer, driven by TOOL_SPECS below.
#
# Every package-manager tool in the menu follows the same shape (check
# if installed -> print version -> elevate -> detect package manager -> run
# per-PM install steps). To add a new tool to the menu:
#   1. Add a ToolSpec entry to TOOL_SPECS.
#   2. Add one line to MENU_ITEMS: _spec_entry("Your Tool Name").
# The availability check, install flow, and menu wiring all follow from
# that — no new function and nothing else to keep in sync.
# ------------------------------------------------------------------------------
@dataclass(frozen=True)
class ToolSpec:
    name: str                                    # Display name, e.g. "OpenJDK"
    bin: str                                     # Binary checked for availability
    version_cmd: list[str]                       # Command that prints the version
    install_cmds: dict[str, list[list[str]]]     # Per-package-manager install steps (sudo prefix applied automatically)
    version_label: str = "Active Version"
    version_on_stderr: bool = False              # e.g. `java -version` prints to stderr
    secondary: tuple[str, str, list[str]] | None = None  # (bin, label, version_cmd) for a second binary installed alongside, e.g. npm/cargo


def install_tool(spec: ToolSpec) -> None:
    if shutil.which(spec.bin):
        log_info(f"{spec.name} is already installed on this system.")
        result = subprocess.run(spec.version_cmd, capture_output=True, text=True)
        lines = (result.stderr if spec.version_on_stderr else result.stdout).splitlines()

        label_w = len(spec.version_label)
        if spec.secondary:
            label_w = max(label_w, len(spec.secondary[1]))

        if lines:
            pad = " " * (label_w - len(spec.version_label) + 1)
            print(f"{ui_margin()}    {C.GRAY}{spec.version_label}:{C.RESET}{pad}{C.DIM}{lines[0].strip()}{C.RESET}")

        if spec.secondary:
            sec_bin, sec_label, sec_cmd = spec.secondary
            if shutil.which(sec_bin):
                sec_v = subprocess.run(sec_cmd, capture_output=True, text=True).stdout.strip()
                pad = " " * (label_w - len(sec_label) + 1)
                print(f"{ui_margin()}    {C.GRAY}{sec_label}:{C.RESET}{pad}{C.DIM}{sec_v}{C.RESET}")

        log_success("Skipping installation.")
        return

    prefix = ensure_privileges()
    if prefix is None:
        return

    pm = detect_package_manager()
    if pm is None:
        log_error("Could not detect a supported package manager.")
        return
    if pm not in spec.install_cmds:
        log_error(f"{spec.name} has no install steps defined for {_pm_display_name(pm)}.")
        return
    log_info(f"Package manager: {C.BOLD}{C.CYAN}{_pm_display_name(pm)}{C.RESET}")

    ok = True
    for step in spec.install_cmds[pm]:
        cmd = prefix + step
        if not run_with_status(f"Installing {spec.name}", cmd):
            ok = False
            break

    if ok:
        log_success(f"{spec.name} configured successfully!")
        log_info(f"Open a new terminal so PATH changes take effect if '{spec.bin}' isn't found yet.")


TOOL_SPECS: list[ToolSpec] = [
    ToolSpec(
        name="OpenJDK",
        bin="java",
        version_cmd=["java", "-version"],
        version_on_stderr=True,
        install_cmds={
            "apt-get": [["apt-get", "update", "-qq"], ["apt-get", "install", "-y", "default-jdk"]],
            "dnf": [["dnf", "install", "-y", "java-17-openjdk-devel"]],
            "pacman": [["pacman", "-S", "--noconfirm", "jdk-openjdk"]],
            "zypper": [["zypper", "install", "-y", "java-17-openjdk-devel"]],
            "apk": [["apk", "add", "openjdk17-jdk"]],
        },
    ),
    ToolSpec(
        name="Node.js & npm",
        bin="node",
        version_cmd=["node", "-v"],
        version_label="Node Version",
        secondary=("npm", "npm Version", ["npm", "-v"]),
        install_cmds={
            "apt-get": [["apt-get", "update", "-qq"], ["apt-get", "install", "-y", "nodejs", "npm"]],
            "dnf": [["dnf", "install", "-y", "nodejs", "npm"]],
            "pacman": [["pacman", "-S", "--noconfirm", "nodejs", "npm"]],
            "zypper": [["zypper", "install", "-y", "nodejs", "npm"]],
            "apk": [["apk", "add", "nodejs", "npm"]],
        },
    ),
    ToolSpec(
        name="Git",
        bin="git",
        version_cmd=["git", "--version"],
        version_label="Git Version",
        install_cmds={
            "apt-get": [["apt-get", "update", "-qq"], ["apt-get", "install", "-y", "git"]],
            "dnf": [["dnf", "install", "-y", "git"]],
            "pacman": [["pacman", "-S", "--noconfirm", "git"]],
            "zypper": [["zypper", "install", "-y", "git"]],
            "apk": [["apk", "add", "git"]],
        },
    ),
    ToolSpec(
        name="Rust",
        bin="rustc",
        version_cmd=["rustc", "--version"],
        version_label="Rust Version",
        secondary=("cargo", "Cargo Version", ["cargo", "--version"]),
        install_cmds={
            "apt-get": [["apt-get", "update", "-qq"], ["apt-get", "install", "-y", "rustc", "cargo"]],
            "dnf": [["dnf", "install", "-y", "rust", "cargo"]],
            "pacman": [["pacman", "-S", "--noconfirm", "rust"]],
            "zypper": [["zypper", "install", "-y", "rust", "cargo"]],
            "apk": [["apk", "add", "rust", "cargo"]],
        },
    ),
    ToolSpec(
        name="Go",
        bin="go",
        version_cmd=["go", "version"],
        version_label="Go Version",
        install_cmds={
            "apt-get": [["apt-get", "update", "-qq"], ["apt-get", "install", "-y", "golang-go"]],
            "dnf": [["dnf", "install", "-y", "golang"]],
            "pacman": [["pacman", "-S", "--noconfirm", "go"]],
            "zypper": [["zypper", "install", "-y", "go"]],
            "apk": [["apk", "add", "go"]],
        },
    ),
    ToolSpec(
        name="GitHub CLI",
        bin="gh",
        version_cmd=["gh", "--version"],
        version_label="gh Version",
        install_cmds={
            "apt-get": [["apt-get", "update", "-qq"], ["apt-get", "install", "-y", "gh"]],
            "dnf": [["dnf", "install", "-y", "gh"]],
            "pacman": [["pacman", "-S", "--noconfirm", "github-cli"]],
            "zypper": [["zypper", "install", "-y", "gh"]],
            "apk": [["apk", "add", "github-cli"]],
        },
    ),
    ToolSpec(
        name="CMake",
        bin="cmake",
        version_cmd=["cmake", "--version"],
        version_label="CMake Version",
        install_cmds={
            "apt-get": [["apt-get", "update", "-qq"], ["apt-get", "install", "-y", "cmake"]],
            "dnf": [["dnf", "install", "-y", "cmake"]],
            "pacman": [["pacman", "-S", "--noconfirm", "cmake"]],
            "zypper": [["zypper", "install", "-y", "cmake"]],
            "apk": [["apk", "add", "cmake"]],
        },
    ),
    ToolSpec(
        name="Docker",
        bin="docker",
        version_cmd=["docker", "--version"],
        version_label="Docker Version",
        install_cmds={
            "apt-get": [["apt-get", "update", "-qq"], ["apt-get", "install", "-y", "docker.io"]],
            "dnf": [["dnf", "install", "-y", "moby-engine"]],
            "pacman": [["pacman", "-S", "--noconfirm", "docker"]],
            "zypper": [["zypper", "install", "-y", "docker"]],
            "apk": [["apk", "add", "docker"]],
        },
    ),
]

TOOL_SPECS_BY_NAME: dict[str, ToolSpec] = {spec.name: spec for spec in TOOL_SPECS}


# ------------------------------------------------------------------------------
# FEATURE: Configure GCC
# Installs a C/C++ toolchain via the detected package manager.
# ------------------------------------------------------------------------------
def configure_gcc() -> None:
    if shutil.which("gcc"):
        log_info("GCC is already installed on this system.")
        version = subprocess.run(["gcc", "--version"], capture_output=True, text=True).stdout.splitlines()
        if version:
            print(f"{ui_margin()}    {C.GRAY}Active Version:{C.RESET} {C.DIM}{version[0]}{C.RESET}")
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
        "apt-get": [prefix + ["apt-get", "update", "-qq"], prefix + ["apt-get", "install", "-y", "build-essential"]],
        "dnf": [prefix + ["dnf", "groupinstall", "-y", "Development Tools"]],
        "pacman": [prefix + ["pacman", "-S", "--noconfirm", "base-devel"]],
        "zypper": [prefix + ["zypper", "install", "-y", "-t", "pattern", "devel_basis"]],
        "apk": [prefix + ["apk", "add", "build-base"]],
    }

    ok = True
    for cmd in install_cmds[pm]:
        if not run_with_status("Installing GCC", cmd):
            ok = False
            break

    if ok:
        log_success("GCC configured successfully!")
        log_info("Open a new terminal so PATH changes take effect if 'gcc' isn't found yet.")


# ------------------------------------------------------------------------------
# FEATURE: Ask AVA (AI)
#
# Describe a task in plain English; AVA asks a model for ONE command, shows it,
# and runs it only after you confirm. Standard library only.
#
# Runs entirely on your machine through Ollama with the Qwen model
# (DEFAULT_OLLAMA_MODEL); nothing is sent over the internet. If Ollama or the
# model isn't set up yet, AVA installs and downloads them at startup (see
# bootstrap_local_ai) and offers to retry from Ask AVA if that failed. Set AVA_OLLAMA_MODEL to try another model.
#
# The model only sees your request plus the OS name, shell and package manager.
# ------------------------------------------------------------------------------

OLLAMA_URL = "http://localhost:11434/api/chat"
OLLAMA_INSTALL_SCRIPT = "curl -fsSL https://ollama.com/install.sh | sh"
DEFAULT_OLLAMA_MODEL = "qwen2.5-coder:3b"
AI_TIMEOUT = 60


class _AIUnavailable(RuntimeError):
    """Ollama can't be reached, or the requested model isn't downloaded."""


def _model_name() -> str:
    return os.environ.get("AVA_OLLAMA_MODEL", "").strip() or DEFAULT_OLLAMA_MODEL

_I = re.IGNORECASE

# Refused outright, whatever the user answers.
_BLOCKED: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"\brm\b[^;|&\n]*\s(?:/|/\*|~/?|~/\*|\$HOME/?)(?=\s|$|[;&|])", _I),
     "it would delete your root or home directory"),
    (re.compile(r"\bmkfs(?:\.\w+)?\b", _I), "it would format a filesystem"),
    (re.compile(r"\bdd\b[^;|&\n]*\bof=/dev/", _I), "it would write directly to a disk device"),
    (re.compile(r">\s*/dev/(?:sd|nvme|hd|vd|mmcblk)", _I), "it would write directly to a disk device"),
    (re.compile(r":\(\)\s*\{\s*:\s*\|\s*:\s*&\s*\}\s*;\s*:"), "it is a fork bomb"),
    (re.compile(r"\b(?:chmod|chown)\b[^;|&\n]*\s-R\b[^;|&\n]*\s/(?=\s|$)", _I),
     "it would change permissions across the whole system"),
]

# Allowed, but only after the user types the full word "yes".
_CAUTION: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"\brm\b[^;|&\n]*\s(?:-[a-z]*[rf][a-z]*|--recursive|--force)\b", _I), "it deletes files"),
    (re.compile(r"\b(?:curl|wget)\b[^\n]*\|\s*(?:sudo\s+)?(?:ba|z|da)?sh\b", _I),
     "it runs a downloaded script"),
    (re.compile(r"\b(?:shutdown|reboot|poweroff|halt)\b", _I), "it stops or restarts the machine"),
    (re.compile(r"\b(?:chmod|chown)\b[^;|&\n]*\s-R\b", _I), "it changes permissions recursively"),
    (re.compile(r"\bgit\s+(?:push\b[^;|&\n]*(?:--force|\s-f\b)|reset\s+--hard|clean\s+-[a-z]*f)", _I),
     "it can discard git work"),
    (re.compile(r"\b(?:kill\s+-9|pkill|killall)\b", _I), "it force-stops processes"),
    (re.compile(r"\bsystemctl\s+(?:stop|disable|mask)\b", _I), "it stops a system service"),
    (re.compile(r"\b(?:iptables\s+-F|ufw\s+disable)\b", _I), "it turns off the firewall"),
    (re.compile(r"\bdrop\s+(?:database|table)\b", _I), "it deletes a database"),
]


def _risk(cmd: str) -> tuple[str, str] | None:
    """Returns ('blocked' | 'caution', reason), or None if nothing stands out."""
    for pattern, reason in _BLOCKED:
        if pattern.search(cmd):
            return "blocked", reason
    for pattern, reason in _CAUTION:
        if pattern.search(cmd):
            return "caution", reason
    if "\n" in cmd.strip():
        return "caution", "it contains several lines"
    return None


def _os_label() -> str:
    try:
        return platform.freedesktop_os_release().get("PRETTY_NAME", platform.system())  # type: ignore[attr-defined]
    except Exception:
        return f"{platform.system()} {platform.release()}".strip()


# Short, concrete guidance for a small model. Only the hints that match the request are included:
# a small model tends to copy any example it sees, so unrelated hints do more harm than good.
_BASE_RULES = (
    "Only use real, standard commands that exist on a normal system; never invent a program name. "
    "Never guess one specific program name when the user did not name it. "
    "Answer exactly what was asked and do not add extra commands."
)


def _installed_packages_hint() -> str:
    pm = detect_package_manager()
    cmd = {
        "pacman": "pacman -Qe",
        "apt-get": "apt list --installed 2>/dev/null",
        "dnf": "dnf list installed",
        "zypper": "zypper search --installed-only",
        "apk": "apk list --installed",
    }.get(pm or "", "")
    return cmd + ("; flatpak list --app" if cmd and shutil.which("flatpak") else "")


def _hints_for(request: str) -> str:
    topics: list[tuple[str, str]] = [
        (r"browser",
         "Installed browsers: grep -il '^Categories=.*WebBrowser' /usr/share/applications/*.desktop "
         "~/.local/share/applications/*.desktop /var/lib/flatpak/exports/share/applications/*.desktop 2>/dev/null "
         "| xargs -r grep -h -m1 '^Name='. Default browser: xdg-settings get default-web-browser."),
        (r"\b(apps?|applications?|packages?|software|programs?)\b.*\b(installed|have)\b|\binstalled\b.*\b(apps?|applications?|packages?|software|programs?)\b",
         f"Installed apps or packages: {_installed_packages_hint()}"),
        (r"laptop|computer|machine|model|\bos\b|operating system|kernel|distro|version of (?:my )?system",
         "Computer model, OS or kernel info: hostnamectl."),
        (r"\bfiles?\b|folders?|director", "List files: ls -la."),
        (r"\bcpu\b|processor", "CPU: lscpu."),
        (r"\b(ram|memory)\b", "Memory: free -h."),
        (r"disk|storage|space", "Disk space: df -h. Folder size: du -sh <folder>."),
        (r"\bgpu\b|graphics", "GPU: lspci | grep -i vga."),
        (r"battery", "Battery: upower -i $(upower -e | grep BAT)."),
        (r"\bip\b|network|address", "IP address: ip -brief addr."),
        (r"\bports?\b", "Listening ports: ss -tulpn."),
        (r"process|running", "Running processes: ps aux."),
        (r"\d+\s*(?:[%+*/-]|plus|minus|times)\s*\d+|even|odd|calculat",
         "Math: echo does NOT calculate. Use echo $((10 % 2)). "
         "Even or odd: [ $((N % 2)) -eq 0 ] && echo even || echo odd."),
        (r"update|upgrade", "Updates available without installing them: checkupdates (Arch) or apt list --upgradable."),
    ]
    picked = [text for pattern, text in topics if re.search(pattern, request, re.IGNORECASE)]
    return "\n".join([_BASE_RULES] + (["Hints: " + " ".join(picked)] if picked else []))


def _system_prompt(request: str = "") -> str:
    shell = "a POSIX shell (sh -c)"
    pm = detect_package_manager()
    return (
        "You turn a user's request into ONE command for their terminal.\n"
        f"Environment: OS {_os_label()}; shell {shell}; "
        f"package manager {_pm_display_name(pm) if pm else 'unknown'}.\n"
        'Reply with a single JSON object and nothing else: '
        '{"command": "<one command line>", "explanation": "<one short sentence>"}\n'
        "Rules: prefer tools that ship with the system; use the package manager above for installs "
        "(include sudo on Linux when it is needed); never install anything unless the user asks to install it: "
        "when they want to use, run or open a tool, just run that tool; never use a destructive command unless the user "
        'clearly asked for it; never use echo or printf to tell a joke, answer a question or chat '
        '(echo may only print something the shell computes); if the request is not a terminal task or '
        'cannot be done safely, set "command" to "" and say why in "explanation".\n'
        + _hints_for(request)
    )


def _http_json(url: str, payload: dict) -> dict:
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=AI_TIMEOUT) as resp:
            return json.load(resp)
    except urllib.error.HTTPError as exc:
        detail = ""
        try:
            err = json.load(exc).get("error")
            detail = err.get("message", "") if isinstance(err, dict) else str(err or "")
        except Exception:
            pass
        message = f"HTTP {exc.code}" + (f": {detail}" if detail else "")
        # Ollama answers 404 when the requested model hasn't been downloaded.
        raise (_AIUnavailable if exc.code == 404 else RuntimeError)(message) from None
    except urllib.error.URLError as exc:
        raise _AIUnavailable(f"could not reach the model ({exc.reason})") from None


def _ask_model(request: str) -> str:
    """Sends the request to the local Ollama model and returns the raw reply text."""
    data = _http_json(
        OLLAMA_URL,
        {
            "model": _model_name(),
            "stream": False,
            "format": "json",
            "messages": [
                {"role": "system", "content": _system_prompt(request)},
                {"role": "user", "content": request},
            ],
        },
    )
    return str(data.get("message", {}).get("content", ""))


def _parse_reply(text: str) -> tuple[str, str]:
    """Extracts (command, explanation) from the model's reply. Raises ValueError if unusable."""
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end <= start:
        raise ValueError("no JSON object in reply")
    obj = json.loads(text[start : end + 1])
    if not isinstance(obj, dict):
        raise ValueError("reply is not a JSON object")
    return str(obj.get("command", "")).strip(), str(obj.get("explanation", "")).strip()


def _shell_argv(cmd: str) -> list[str]:
    return ["/bin/sh", "-c", cmd]


# Shell keywords/builtins that never live on PATH, so they're never "missing".
_SHELL_BUILTINS = {
    "cd", "echo", "export", "source", ".", "alias", "unalias", "type", "test", "[", "[[", "if", "then",
    "else", "elif", "fi", "for", "do", "done", "while", "until", "case", "esac", "exit", "set", "unset",
    "read", "pwd", "ulimit", "umask", "wait", "kill", "true", "false", "history", "eval", "exec",
    "command", "builtin", "trap", "fg", "bg", "jobs", "pushd", "popd", "{", "}", "!", "return", "shift",
    "local", "declare", "typeset", "let", ":", "hash", "getopts",
}
_WRAPPERS = {"sudo", "doas", "env", "time", "nohup", "nice", "exec", "command", "xargs", "watch"}


def _missing_programs(cmd: str) -> list[str]:
    """Returns the programs a suggested command needs that aren't installed
    (empty list if everything it calls is available or can't be checked)."""
    import shlex

    missing: list[str] = []
    for segment in re.split(r"\|\||&&|[|;&\n]", cmd):
        segment = segment.strip()
        if not segment:
            continue
        try:
            tokens = shlex.split(segment)
        except ValueError:
            continue
        i = 0
        while i < len(tokens):
            tok = tokens[i]
            if re.match(r"^[A-Za-z_][A-Za-z0-9_]*=", tok):      # VAR=value prefix
                i += 1
            elif tok in _WRAPPERS:                               # sudo -u x prog ... -> look past it
                i += 1
                while i < len(tokens) and (tokens[i].startswith("-") or re.match(r"^\w+=", tokens[i])):
                    i += 1
            else:
                break
        if i >= len(tokens):
            continue
        prog = tokens[i].strip("()`'\"")
        if not prog or prog.lower() in _SHELL_BUILTINS or prog[0] in "$<>(`" or "$(" in prog:
            continue
        if "/" in prog:
            found = os.access(os.path.expanduser(prog), os.X_OK)
        else:
            found = shutil.which(prog) is not None
        if not found and prog not in missing:
            missing.append(prog)
    return missing


def _package_install_cmd(pkg: str) -> list[str] | None:
    """Install command for a package of this name on this system (None if no known package manager).
    The package name is assumed to match the program name, which holds for most CLI tools."""
    if not re.match(r"^[A-Za-z0-9][A-Za-z0-9+._-]*$", pkg):
        return None
    pm = detect_package_manager()
    sudo = [] if is_admin() else ["sudo"]
    table = {
        "apt-get": sudo + ["apt-get", "install", "-y", pkg],
        "dnf": sudo + ["dnf", "install", "-y", pkg],
        "pacman": sudo + ["pacman", "-S", "--noconfirm", pkg],
        "zypper": sudo + ["zypper", "--non-interactive", "install", pkg],
        "apk": sudo + ["apk", "add", pkg],
    }
    return table.get(pm or "")


def _offer_install_missing(absent: list[str]) -> bool:
    """Tells the user which programs are missing and offers to install each one (y/N).
    Returns True only if every missing program ends up installed."""
    for prog in absent:
        log_warn(f"The required package '{prog}' isn't installed. You need to install it first.")
        cmd = _package_install_cmd(prog)
        if cmd is None:
            log_error("AVA couldn't find a package manager to install it with. Install it manually, then ask again.")
            return False
        print()
        _print_boxed("Install command", [("", ""), (" ".join(cmd), CMD_STYLE), ("", "")], C.YELLOW)
        print()
        try:
            answer = input(f"{ui_margin()} {C.GRAY}Install it? [y/N]{C.RESET} ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            print()
            return False
        if answer not in ("y", "yes"):
            log_info("Skipped.")
            return False
        if cmd[0] == "sudo" and ensure_privileges() is None:
            return False
        if not run_with_status(f"Installing {prog}", cmd):
            log_error(f"Couldn't install '{prog}'. The package may not exist under that name, so check the spelling or install it manually.")
            return False
        if not _missing_programs(prog):
            log_success(f"{prog} installed.")
        else:
            log_warn(f"'{prog}' was installed but isn't on PATH yet. Open a new terminal and try again.")
            return False
    return True


def _print_boxed(title: str, rows: list[tuple[str, str]], color: str) -> None:
    """Draws a 64-column box. rows are (text, style) pairs; long text is wrapped."""
    box_width = 64
    inner = box_width - 4
    margin = ui_margin(box_width)
    top_dashes = BOX_H * max(1, box_width - len(title) - 5)
    print(f"{margin}{color}{BOX_TL}{BOX_H}{C.RESET} {C.BOLD}{title}{C.RESET} {color}{top_dashes}{BOX_TR}{C.RESET}")
    for text, style in rows:
        pieces = []
        for line in text.splitlines() or [""]:
            pieces += textwrap.wrap(line, inner, break_long_words=True, break_on_hyphens=False,
                                    replace_whitespace=False, drop_whitespace=False) or [""]
        for piece in pieces:
            print(f"{margin}{color}{BOX_V}{C.RESET} {style}{piece.ljust(inner)}{C.RESET} {color}{BOX_V}{C.RESET}")
    print(f"{margin}{color}{BOX_BL}{BOX_H * (box_width - 2)}{BOX_BR}{C.RESET}")


# ------------------------------------------------------------------------------
# FEATURE: Local AI setup (Ollama + model)
#
# Installs Ollama if it's missing, makes sure its server is running, and
# downloads the model (DEFAULT_OLLAMA_MODEL, or AVA_OLLAMA_MODEL). Safe to
# re-run: every step is skipped when it's already done.
# ------------------------------------------------------------------------------
def _ollama_base() -> str:
    return OLLAMA_URL.rsplit("/api/", 1)[0]


def _ollama_get(path: str, timeout: float = 3) -> dict | None:
    """GETs an Ollama API path. Returns the JSON, or None if the server isn't answering."""
    try:
        with urllib.request.urlopen(_ollama_base() + path, timeout=timeout) as resp:
            return json.load(resp)
    except Exception:  # noqa: BLE001
        return None


def _ollama_has_model(model: str) -> bool:
    data = _ollama_get("/api/tags") or {}
    names = {m.get("name", "") for m in data.get("models", []) if isinstance(m, dict)}
    return model in names or (":" not in model and f"{model}:latest" in names)


def _wait_for_ollama(seconds: float) -> bool:
    deadline = time.time() + seconds
    while time.time() < deadline:
        if _ollama_get("/api/tags", timeout=1) is not None:
            return True
        time.sleep(0.5)
    return False


def _install_ollama() -> bool:
    pm = detect_package_manager()

    prefix = ensure_privileges()
    if prefix is None:
        return False
    if pm == "pacman":
        return run_with_status("Installing Ollama", prefix + ["pacman", "-S", "--noconfirm", "ollama"])

    # Other distros don't package Ollama: use the official installer from ollama.com.
    if not shutil.which("curl"):
        log_error("'curl' is required to download the Ollama installer. Install it and try again.")
        return False
    log_info("Ollama isn't in your package manager's repositories.")
    log_info(f"AVA can run the official installer from ollama.com: {C.BOLD}{OLLAMA_INSTALL_SCRIPT}{C.RESET}")
    try:
        answer = input(f"{ui_margin()} {C.GRAY}Run it? [y/N]{C.RESET} ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        print()
        return False
    if answer not in ("y", "yes"):
        log_info("Skipped.")
        return False
    return run_with_status("Installing Ollama", OLLAMA_INSTALL_SCRIPT, shell=True)


def _start_ollama() -> bool:
    """Makes sure the Ollama server is answering, starting it if needed."""
    if _ollama_get("/api/tags") is not None:
        return True

    if shutil.which("systemctl"):
        prefix = ensure_privileges()
        if prefix is not None:
            run_with_status("Starting Ollama", prefix + ["systemctl", "enable", "--now", "ollama"])
            if _wait_for_ollama(15):
                return True

    # No service available (containers, minimal installs...): run the server in the background.
    log_info("Starting the Ollama server in the background...")
    extra = {"start_new_session": True}
    try:
        subprocess.Popen(
            ["ollama", "serve"],
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            **extra,
        )
    except OSError as exc:
        log_error(f"Could not start Ollama: {exc}")
        return False
    return _wait_for_ollama(20)


def _setup_local_ai() -> bool:
    model = _model_name()
    log_info(f"Local AI: Ollama + {C.BOLD}{C.CYAN}{model}{C.RESET}")

    if shutil.which("ollama"):
        log_info("Ollama is already installed.")
    else:
        if not _install_ollama():
            return False
        if not shutil.which("ollama"):
            log_warn("Ollama was installed but isn't on PATH yet. Open a new terminal and run AVA again.")
            return False

    if not _start_ollama():
        log_error("Ollama isn't responding. Try running 'ollama serve' in another terminal.")
        return False

    if _ollama_has_model(model):
        log_info(f"Model {model} is already downloaded.")
    elif not run_with_status(f"Downloading {model}", ["ollama", "pull", model]):
        return False

    log_success("Local AI is ready.")
    return True


def _offer_local_ai_setup() -> bool:
    """Called when the local model can't be reached. Returns True if setup ran and succeeded."""
    model = _model_name()
    log_info(f"The local model isn't ready. AVA can install Ollama and download {model} for you.")
    try:
        answer = input(f"{ui_margin()} {C.GRAY}Set it up? [y/N]{C.RESET} ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        print()
        return False
    if answer not in ("y", "yes"):
        return False
    return _setup_local_ai()


# ------------------------------------------------------------------------------
# Startup bootstrap: makes sure Ollama + the model are installed and running
# before the menu appears. Everything happens inside one "Setting things up"
# box with a percentage; if everything is already in place, nothing is shown.
# ------------------------------------------------------------------------------
_BAR_FULL, _BAR_EMPTY = ("\u2588", "\u2591") if UNICODE_OK else ("#", "-")


class _SetupBox:
    """Three-line live status box (same look as run_with_status) with a progress bar."""

    LABEL = "Setting things up"

    def __init__(self) -> None:
        self.box_width = 64
        self.inner_width = self.box_width - 7
        self.margin = ui_margin(self.box_width)
        self.first = True
        self.last_pct = -1
        self.pct = 0.0

    def draw(self, pct: float, text: str) -> None:
        self.pct = max(self.pct, min(100.0, pct))  # never move backwards
        shown = int(self.pct)
        if not _TTY:
            if shown != self.last_pct and shown % 10 == 0:
                print(f"{self.margin} {self.LABEL}... {shown}%")
            self.last_pct = shown
            return
        bar_len = 14
        filled = round(bar_len * self.pct / 100)
        bar = _BAR_FULL * filled + _BAR_EMPTY * (bar_len - filled)
        prefix = f"{shown:>3}% {bar} "
        room = self.inner_width - len(prefix)
        text = re.sub(r"\s+", " ", text)
        if len(text) > room:
            text = text[: room - 3] + "..."
        line = f"{prefix}{text}"
        top_dashes = BOX_H * max(1, self.box_width - len(self.LABEL) - 6)
        bot_dashes = BOX_H * (self.box_width - 3)
        w = sys.stdout.write
        if not self.first:
            w("\033[3A")
        self.first = False
        w(f"\r\033[K{self.margin} {C.CYAN}{BOX_TL}{BOX_H}{C.RESET} {C.BOLD}{C.PURPLE}{self.LABEL}{C.RESET} {C.CYAN}{top_dashes}{BOX_TR}{C.RESET}\n")
        w(f"\r\033[K{self.margin} {C.CYAN}{BOX_V}{C.RESET} {C.CYAN}{ICON_BOLT}{C.RESET} {line:<{self.inner_width}} {C.CYAN}{BOX_V}{C.RESET}\n")
        w(f"\r\033[K{self.margin} {C.CYAN}{BOX_BL}{bot_dashes}{BOX_BR}{C.RESET}\n")
        sys.stdout.flush()

    def close(self) -> None:
        if _TTY and not self.first:
            sys.stdout.write("\033[3A\033[2K\n\033[2K\n\033[2K\033[2A\r")
            sys.stdout.flush()
        self.first = True


def _fmt_bytes(n: float) -> str:
    return f"{n / 1e9:.1f} GB" if n >= 1e9 else f"{n / 1e6:.0f} MB"


def _ollama_install_plan() -> tuple[list[str] | str, bool] | None:
    """Works out (command, use_shell) to install Ollama non-interactively, securing
    any privileges up front so nothing prompts while the status box is showing.
    Returns None (after printing why) if it can't be done."""
    pm = detect_package_manager()

    prefix = ensure_privileges()
    if prefix is None:
        return None
    if pm == "pacman":
        return prefix + ["pacman", "-S", "--noconfirm", "ollama"], False
    if not shutil.which("curl"):
        log_error("'curl' is required to download Ollama. Install it and run AVA again.")
        return None
    log_info(f"Installing Ollama with its official installer: {C.BOLD}{OLLAMA_INSTALL_SCRIPT}{C.RESET}")
    return OLLAMA_INSTALL_SCRIPT, True


def _run_quiet(cmd: list[str] | str, shell: bool, tick: Callable[[float], None]) -> bool:
    """Runs a command with its output hidden, calling tick(elapsed_seconds) while it runs."""
    log_file = tempfile.TemporaryFile(mode="w+", encoding="utf-8", errors="replace")
    try:
        proc = subprocess.Popen(cmd, shell=shell, stdin=subprocess.DEVNULL,
                                stdout=log_file, stderr=subprocess.STDOUT, text=True)
    except OSError as exc:
        log_file.close()
        raise RuntimeError(f"could not start the installer ({exc})") from None
    started = time.time()
    while proc.poll() is None:
        tick(time.time() - started)
        time.sleep(0.1)
    ok = proc.returncode == 0
    if not ok:
        log_file.seek(0)
        tail = [ln.strip() for ln in log_file.readlines() if ln.strip()][-1:]
        log_file.close()
        raise RuntimeError(tail[0] if tail else f"installer exited with code {proc.returncode}")
    log_file.close()
    return True


def _start_ollama_quiet(sudo_prefix: list[str] | None, tick: Callable[[float], None]) -> bool:
    """Like _start_ollama, but silent: nothing is printed while the status box is showing."""
    def wait(seconds: float) -> bool:
        started = time.time()
        while time.time() - started < seconds:
            if _ollama_get("/api/tags", timeout=1) is not None:
                return True
            tick(time.time() - started)
            time.sleep(0.3)
        return False

    if _ollama_get("/api/tags") is not None:
        return True

    if shutil.which("systemctl") and sudo_prefix is not None:
        prefix = ["sudo", "-n"] if sudo_prefix == ["sudo"] else sudo_prefix  # never prompt here
        subprocess.run(prefix + ["systemctl", "enable", "--now", "ollama"],
                       stdin=subprocess.DEVNULL, capture_output=True)
        if wait(15):
            return True

    extra = {"start_new_session": True}
    try:
        subprocess.Popen(["ollama", "serve"], stdin=subprocess.DEVNULL,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, **extra)
    except OSError:
        return False
    return wait(20)


def _pull_model_quiet(model: str, on_progress: Callable[[float, str], None]) -> None:
    """Downloads the model through Ollama's API, reporting (fraction 0-1, text)."""
    req = urllib.request.Request(
        _ollama_base() + "/api/pull",
        data=json.dumps({"model": model, "name": model, "stream": True}).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    layers: dict[str, tuple[float, float]] = {}
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            for raw in resp:
                try:
                    msg = json.loads(raw)
                except ValueError:
                    continue
                if msg.get("error"):
                    raise RuntimeError(str(msg["error"]))
                digest = msg.get("digest")
                if digest and msg.get("total"):
                    layers[digest] = (float(msg.get("completed", 0)), float(msg["total"]))
                total = sum(t for _, t in layers.values())
                done = sum(c for c, _ in layers.values())
                if total:
                    on_progress(done / total, f"Downloading model {_fmt_bytes(done)} / {_fmt_bytes(total)}")
                else:
                    on_progress(0.0, str(msg.get("status", "Downloading")).capitalize())
    except urllib.error.URLError as exc:
        raise RuntimeError(f"download failed ({exc.reason})") from None


def bootstrap_local_ai() -> None:
    """Silently makes sure Ollama and the model are ready. Shows a 'Setting things up'
    box only when there is something to do; on failure Ask AVA offers to retry later."""
    model = _model_name()
    need_install = not shutil.which("ollama")
    if not need_install and _ollama_has_model(model):
        return

    # Work out the install command (and secure sudo) before the box appears.
    plan = None
    if need_install:
        plan = _ollama_install_plan()
        if plan is None:
            _bootstrap_failed("Ollama could not be installed.")
            return
    sudo_prefix = None
    if (need_install or _ollama_get("/api/tags") is None):
        sudo_prefix = [] if is_admin() else (["sudo"] if shutil.which("sudo") else None)

    # Each stage gets a share of the 0-100% bar.
    stages = [("install", 25 if need_install else 0), ("start", 5), ("pull", 70)]
    total_w = sum(w for _, w in stages)
    start_at, acc = {}, 0.0
    for name, w in stages:
        start_at[name] = acc * 100 / total_w
        acc += w
    span = {name: w * 100 / total_w for name, w in stages}

    box = _SetupBox()
    print()
    box.draw(0, "Preparing...")
    try:
        if need_install:
            assert plan is not None
            cmd, use_shell = plan

            def install_tick(elapsed: float) -> None:
                # No real progress from installers: ease towards the end of the stage.
                frac = 1 - 2.718281828 ** (-elapsed / 40)
                box.draw(start_at["install"] + span["install"] * 0.95 * frac, "Installing Ollama")

            _run_quiet(cmd, use_shell, install_tick)
            if not shutil.which("ollama"):
                box.close()
                _bootstrap_failed("Ollama was installed but isn't on PATH yet. Open a new terminal and run AVA again.")
                return

        def start_tick(elapsed: float) -> None:
            box.draw(start_at["start"] + span["start"] * min(0.95, elapsed / 20), "Starting Ollama")

        box.draw(start_at["start"], "Starting Ollama")
        if not _start_ollama_quiet(sudo_prefix, start_tick):
            raise RuntimeError("Ollama isn't responding.")

        box.draw(start_at["pull"], f"Checking {model}")
        if not _ollama_has_model(model):
            _pull_model_quiet(
                model,
                lambda frac, text: box.draw(start_at["pull"] + span["pull"] * frac, text),
            )
        box.draw(100, "Ready")
        time.sleep(0.4)
        box.close()
    except Exception as exc:  # noqa: BLE001
        box.close()
        _bootstrap_failed(str(exc))


def _bootstrap_failed(reason: str) -> None:
    log_warn(f"Local AI setup didn't finish: {reason}")
    log_info("Ask AVA will offer to try again when you use it.")
    try:
        input(f"{ui_margin()} {C.GRAY}Press Enter to continue...{C.RESET}")
    except (EOFError, KeyboardInterrupt):
        print()



_ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]")
_OSC_RE = re.compile(r"\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)")
_CTRL_RE = re.compile(r"[\x00-\x08\x0b-\x1f\x7f]")
_KV_RE = re.compile(r"^(\s*)([A-Za-z][^:]{0,30}?):(\s+)(\S.*)$")
_INTERACTIVE_RE = re.compile(
    r"(?:^|[|;&]\s*|\bsudo\s+(?:-\S+\s+)*)(?:top|htop|btop|vim?|nvim|nano|emacs|less|more|man|ssh|sftp|ftp|telnet|"
    r"watch|tmux|screen|fzf|nmtui|mysql|psql|sqlite3|python3?|node|irb|ipython)\b"
)
OUTPUT_MAX_LINES = 150
CMD_STYLE = C.BOLD + C.PURPLE   # suggested commands stand out from the cyan frame and gray notes


@contextlib.contextmanager
def _no_echo():
    """Keeps keystrokes typed while a boxed command runs from echoing onto the screen
    (which would push the live status box out of place), and discards them afterwards."""
    saved = None
    if _TTY:
        try:
            fd = sys.stdin.fileno()
            saved = (fd, termios.tcgetattr(fd))
            raw = termios.tcgetattr(fd)
            raw[3] &= ~termios.ECHO
            termios.tcsetattr(fd, termios.TCSADRAIN, raw)
        except Exception:  # noqa: BLE001
            saved = None
    try:
        yield
    finally:
        if saved:
            try:
                termios.tcflush(saved[0], termios.TCIFLUSH)
                termios.tcsetattr(saved[0], termios.TCSADRAIN, saved[1])
            except Exception:  # noqa: BLE001
                pass


_AUTO_YES_RE = re.compile(r"(?:^|\s)(?:-y|--yes|--assume-yes|--non-interactive|--noconfirm)(?:\s|$)")
_APT_LIKE_RE = re.compile(
    r"\b(?:apt|apt-get|dnf|yum|zypper)\s+(?:-\S+\s+)*"
    r"(?:install|remove|purge|upgrade|dist-upgrade|full-upgrade|autoremove|erase|reinstall|dup|in|rm)\b"
)


def _asks_for_confirmation(command: str) -> bool:
    """Package-manager commands that stop to ask questions ([Y/n], conflicts, ...) need the real
    terminal so the person can answer, unless they were given an 'assume yes' flag."""
    if _AUTO_YES_RE.search(command):
        return False
    if _APT_LIKE_RE.search(command):
        return True
    for m in re.finditer(r"\b(?:pacman|yay|paru)\b((?:\s+-\S+)+)", command):
        flags = "".join(re.findall(r"(?<!-)-([A-Za-z]+)", m.group(1)))
        if re.search(r"[RU]", flags) or ("S" in flags and not re.search(r"[silg]", flags)):
            return True
    return False


def _is_interactive(command: str) -> bool:
    """Programs that need the real terminal (full-screen UIs, prompts, REPLs) can't be boxed."""
    return bool(_INTERACTIVE_RE.search(command.strip())) or _asks_for_confirmation(command)


def _disp_width(text: str) -> int:
    import unicodedata
    return sum(
        0 if unicodedata.combining(ch) or ch in "\u200d\ufe0f" else
        2 if unicodedata.east_asian_width(ch) in ("W", "F") or ord(ch) >= 0x1F300 else 1
        for ch in text
    )


def _split_width(text: str, width: int) -> list[str]:
    """Cuts text into pieces that are at most `width` terminal columns wide."""
    pieces, cur, w = [], "", 0
    for ch in text:
        cw = _disp_width(ch)
        if w + cw > width:
            pieces.append(cur)
            cur, w = "", 0
        cur += ch
        w += cw
    pieces.append(cur)
    return pieces


def _print_output_box(text: str, ok: bool) -> None:
    """Shows a command's output in a box that matches the rest of AVA's UI."""
    color = C.CYAN if ok else C.RED
    title = "Output" if ok else "Output (command failed)"

    lines: list[str] = []
    cleaned = _OSC_RE.sub("", text)
    cleaned = _ANSI_RE.sub("", cleaned)
    for raw in cleaned.replace("\r\n", "\n").split("\n"):
        if "\r" in raw:                      # progress bars redraw one line with \r: keep the last frame
            frames = [f for f in raw.split("\r") if f.strip()]
            raw = frames[-1] if frames else ""
        raw = _CTRL_RE.sub("", raw.expandtabs(4))
        lines.append(raw.rstrip())
    while lines and not lines[0].strip():
        lines.pop(0)
    while lines and not lines[-1].strip():
        lines.pop()
    hidden = max(0, len(lines) - OUTPUT_MAX_LINES)
    if hidden:
        lines = lines[-OUTPUT_MAX_LINES:]

    # Grow the box to fit the widest line (never narrower than the standard 64 columns);
    # only lines wider than the terminal itself get wrapped.
    widest = max((_disp_width(ln) for ln in lines), default=0)
    box_width = min(max(64, widest + 4), max(64, _term_width() - 2))
    inner = box_width - 4
    margin = ui_margin(box_width)

    rows: list[tuple[str, str]] = []          # (plain text, colored text)
    if hidden:
        note = f"... {hidden} earlier lines hidden ..."
        rows.append((note, f"{C.GRAY}{note}{C.RESET}"))
    for line in lines or ["(no output)"]:
        style = C.GRAY if not lines else ""
        for n, piece in enumerate(_split_width(line, inner)):
            kv = _KV_RE.match(piece) if n == 0 and len(piece.split(":")[0].split()) <= 3 else None
            if kv:
                ind, label, gap, value = kv.groups()
                colored = f"{ind}{C.GRAY}{label}:{C.RESET}{gap}{value}"
            else:
                colored = f"{style}{piece}{C.RESET}" if style else piece
            rows.append((piece, colored))

    top_dashes = BOX_H * max(1, box_width - len(title) - 5)
    print(f"{margin}{color}{BOX_TL}{BOX_H}{C.RESET} {C.BOLD}{title}{C.RESET} {color}{top_dashes}{BOX_TR}{C.RESET}")
    # Icon fonts (Nerd Font glyphs, emoji) don't always occupy the number of cells we expect, so padding
    # with spaces can leave the right edge crooked. On a real terminal, jump straight to the border's
    # column instead; that keeps the edge straight whatever the glyphs measure.
    border_col = len(margin) + box_width
    for plain, colored in rows:
        if _TTY:
            print(f"{margin}{color}{BOX_V}{C.RESET} {colored}\033[{border_col}G{color}{BOX_V}{C.RESET}")
        else:
            pad = " " * max(0, inner - _disp_width(plain))
            print(f"{margin}{color}{BOX_V}{C.RESET} {colored}{pad} {color}{BOX_V}{C.RESET}")
    print(f"{margin}{color}{BOX_BL}{BOX_H * (box_width - 2)}{BOX_BR}{C.RESET}")



def _known_fix(command: str, err: str) -> tuple[str, str] | None:
    """Recognises common, well-understood failures. Returns (explanation, replacement command or '')."""
    low = err.lower()
    if "libkmod" in low or ("lspci" in low and "unable to" in low) or ("lsusb" in low and "unable to" in low):
        return ("This system (WSL, a container or a VM) doesn't expose hardware details to lspci/lsusb. "
                "hostnamectl shows what is available.", "hostnamectl")
    if re.search(r"permission denied|operation not permitted|are you root", low) and not re.search(r"\bsudo\b", command):
        return ("It needs administrator rights. Running it with sudo should work.", f"sudo {command}")
    if re.search(r"could not resolve host|temporary failure in name resolution|network is unreachable", low):
        return ("There's no working internet connection. Check your network, then try again.", "")
    if "externally-managed-environment" in low:
        return ("Python blocks system-wide pip installs. Use pipx for tools, or a virtual environment "
                "(python3 -m venv .venv).", "")
    return None


def _ask_fix(request: str, command: str, err: str) -> tuple[str, str]:
    """Asks the model what went wrong and for a different command. Returns (command, explanation)."""
    prompt = (
        _system_prompt(request)
        + "\nThe command you suggested FAILED. Look at the error and reply with the same JSON shape: "
        '"explanation" says in one short sentence what went wrong and what to do; "command" is a DIFFERENT '
        "command that achieves the user's goal in this environment, or \"\" if there is nothing safe to try."
    )
    data = _http_json(
        OLLAMA_URL,
        {
            "model": _model_name(),
            "stream": False,
            "format": "json",
            "messages": [
                {"role": "system", "content": prompt},
                {"role": "user", "content": f"Request: {request}\nFailed command: {command}\nError output:\n{err.strip()[-1200:]}"},
            ],
        },
    )
    return _parse_reply(str(data.get("message", {}).get("content", "")))


def _suggest_fix(request: str, command: str, err: str) -> tuple[str, str] | None:
    """After a failure: returns (new command, explanation) to offer, or None if nothing to offer.
    Explains the problem either way."""
    if err == _NO_MATCH:
        err = ("The command ran but nothing matched (no lines found). The name it searched for was probably a "
               "wrong guess. Suggest a broader command that answers the request without guessing one name.")
    known = _known_fix(command, err)
    if known:
        explanation, new_cmd = known
    elif err.strip():
        box: dict[str, tuple[str, str]] = {}

        def _work() -> None:
            box["r"] = _ask_fix(request, command, err)

        if not _run_python_task("Looking into the error", _work):
            return None
        new_cmd, explanation = box["r"][0], box["r"][1]
    else:
        return None

    if explanation:
        print()
        _print_boxed("What went wrong", [("", ""), (explanation, ""), ("", "")], C.YELLOW)
        print()
    if not new_cmd or new_cmd.strip() == command.strip():
        return None
    return new_cmd, explanation


_ARITH_ECHO_RE = re.compile(r"^echo\s+(-?\d+(?:\.\d+)?(?:\s*(?:\*\*|[%+\-*/])\s*-?\d+(?:\.\d+)?)+)\s*$")


def _fix_arithmetic(command: str) -> str:
    """'echo 10 % 2' prints the text, not the answer. Turns plain arithmetic into a real calculation."""
    m = _ARITH_ECHO_RE.match(command.strip())
    if not m:
        return command
    expr = m.group(1)
    if "." not in expr and "**" not in expr:
        return f"echo $(({expr}))"
    return f"awk 'BEGIN {{ print {expr.replace('**', '^')} }}'"


def _install_targets(command: str) -> list[str] | None:
    """If the command is just 'install these packages' with a system package manager,
    returns the package names. Returns None for anything else (chains, upgrades, other commands)."""
    import shlex

    try:
        tokens = shlex.split(command.strip())
    except ValueError:
        return None
    if re.search(r"[|;&<>`$]", command):
        return None
    while tokens and tokens[0] in ("sudo", "doas"):
        tokens.pop(0)
        while tokens and tokens[0].startswith("-"):
            tokens.pop(0)
    if len(tokens) < 3:
        return None
    pm, rest = tokens[0], tokens[1:]
    if pm == "pacman":
        flags = rest[0]
        if not re.match(r"^-S[a-z]*$", flags) or re.search(r"[uy]", flags):   # -Syu / -Su = upgrade
            return None
        names = rest[1:]
    elif pm in ("apt", "apt-get", "dnf", "yum", "zypper") and rest[0] in ("install", "in"):
        names = rest[1:]
    elif pm == "apk" and rest[0] == "add":
        names = rest[1:]
    else:
        return None
    names = [n for n in names if not n.startswith("-")]
    return names if names and all(re.match(r"^[A-Za-z0-9][A-Za-z0-9+._-]*$", n) for n in names) else None


_NO_MATCH = "__AVA_NO_MATCH__"
_SEARCH_PROGS = {"grep", "egrep", "fgrep", "rg", "ag", "pgrep", "diff", "cmp", "which", "test", "["}


def _is_search_command(command: str) -> bool:
    """True when the command ends in a search/compare tool, whose exit code 1 means
    'nothing found' rather than 'something broke'."""
    last = re.split(r"\|\||&&|[|;]", command.strip())[-1].strip()
    tokens = last.split()
    while tokens and tokens[0] in ("sudo", "doas", "env", "time", "command"):
        tokens.pop(0)
    return bool(tokens) and tokens[0] in _SEARCH_PROGS


def _review_and_run(command: str, explanation: str, expect_output: bool = False) -> tuple[str, int, str]:
    """Checks, confirms and runs one suggested command.
    Returns (status, exit_code, stderr) with status 'done', 'failed', 'skipped' or 'quit'."""
    risk = _risk(command)
    if risk and risk[0] == "blocked":
        print()
        _print_boxed("Blocked", [("", ""), (command, CMD_STYLE), ("", "")], C.RED)
        print()
        log_error(f"Refusing to run this: {risk[1]}.")
        return "skipped", 0, ""

    absent = _missing_programs(command)
    if absent:
        log_info(f"To run {C.BOLD}{command}{C.RESET} AVA needs a program that isn't installed.")
        if not _offer_install_missing(absent):
            return "skipped", 0, ""
        log_info("Now the command you asked for:")

    rows = [("", ""), (command, CMD_STYLE), ("", "")]
    if explanation:
        rows += [(explanation, C.GRAY), ("", "")]
    if risk:
        rows += [(f"Careful: {risk[1]}.", C.YELLOW), ("", "")]
    print()
    _print_boxed("Suggested command", rows, C.YELLOW if risk else C.CYAN)
    print()

    prompt = "Type 'yes' to run it:" if risk else "Run it? [y/N]"
    try:
        answer = input(f"{ui_margin()} {C.GRAY}{prompt}{C.RESET} ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        print()
        return "quit", 0, ""
    if answer not in (("yes",) if risk else ("y", "yes")):
        log_info("Skipped.")
        return "skipped", 0, ""

    if re.search(r"\bsudo\b", command) and ensure_privileges() is None:
        return "skipped", 0, ""

    print()
    try:
        if _is_interactive(command):
            if _asks_for_confirmation(command):
                log_info("This command asks for confirmation, so it runs directly in your terminal.")
                print()
            code, err = subprocess.run(_shell_argv(command)).returncode, ""
        else:
            cap: dict = {}
            with _no_echo():
                run_with_status("Running", _shell_argv(command), capture=cap)
            code, err = cap.get("code", 1), cap.get("text", "")
            if "code" in cap and _ANSI_RE.sub("", err).strip():
                _print_output_box(err, code == 0)   # silent commands (cp, mv, mkdir...) get no empty box
    except KeyboardInterrupt:
        print()
        log_warn("Interrupted.")
        return "skipped", 0, ""
    except OSError as exc:
        log_error(f"Could not start the shell: {exc}")
        return "skipped", 0, ""
    print()
    if code == 0:
        quiet = not _ANSI_RE.sub("", err).strip() and not _is_interactive(command)
        if quiet and expect_output:
            # A question ("check…", "which…") that printed nothing means nothing was found.
            log_warn("Nothing was printed, so nothing was found.")
            return "failed", 0, _NO_MATCH
        log_success("Command finished successfully." if quiet else "Command finished.")
        return "done", 0, ""
    if code in (127, 9009):
        log_error("A program this command needs isn't installed. Install it first, then try again.")
        return "skipped", code, err
    if code == 1 and not _ANSI_RE.sub("", err).strip() and _is_search_command(command):
        log_warn("Nothing matched. The command ran fine but found no results.")
        return "failed", code, _NO_MATCH
    log_error(f"Command exited with code {code}.")
    return "failed", code, err


def ask_ava() -> None:
    log_info("Describe a task and AVA will suggest one command. Press Enter on a blank line to go back.")
    while True:
        try:
            request = input(f"{ui_margin()} {C.PURPLE}{ICON_PROMPT}{C.RESET} ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return
        if not request:
            return

        # Start each question on a clean screen: drop the previous answer, keep the header and the question.
        if _TTY:
            clear_screen()
            print()
            render_intro_box()
            print()
            print(f"{ui_margin()} {C.PURPLE}{ICON_PROMPT}{C.RESET} {request}")

        reply: dict[str, str] = {}

        def _fetch() -> None:
            try:
                reply["text"] = _ask_model(request)
            except _AIUnavailable:
                reply["unavailable"] = "1"
                raise

        if not _run_python_task("Thinking", _fetch):
            # Local model not running / not downloaded: offer to set it up, then retry once.
            if not (reply.get("unavailable") and _offer_local_ai_setup()):
                continue
            reply.clear()
            if not _run_python_task("Thinking", _fetch):
                continue
        try:
            command, explanation = _parse_reply(reply["text"])
        except ValueError:
            log_error("The model's reply wasn't usable. Try rephrasing your request.")
            continue
        if not command:
            print()
            _print_boxed("No command suggested",
                         [("", ""), (explanation or "No command suggested for that request.", ""), ("", "")],
                         C.YELLOW)
            print()
            continue

        command = _fix_arithmetic(command)

        # Don't suggest installing something that's already installed.
        pkgs = _install_targets(command)
        if pkgs and all(shutil.which(p) for p in pkgs):
            names = ", ".join(pkgs)
            if re.search(r"\b(install|reinstall|update|upgrade|add|setup|set up)\b", request, re.IGNORECASE):
                print()
                _print_boxed("Already installed",
                             [("", ""), (f"{names} {'is' if len(pkgs) == 1 else 'are'} already installed on this "
                                         "computer, so there's nothing to install.", ""), ("", "")],
                             C.CYAN)
                print()
                continue
            if len(pkgs) == 1:
                log_info(f"{names} is already installed, so AVA will run it instead of installing it.")
                command, explanation = pkgs[0], f"Runs {pkgs[0]}."

        asks_question = bool(re.match(
            r"^\s*(?:check|show|list|find|which|what|whats|what's|is|are|do|does|get|display|see|tell|look)\b",
            request, re.IGNORECASE))

        # Offer the command; if it fails, explain why and offer a fix (up to 2 times).
        for attempt in range(3):
            status, _code, err = _review_and_run(command, explanation, expect_output=asks_question)
            if status == "quit":
                return
            if status != "failed" or attempt == 2:
                break
            fix = _suggest_fix(request, command, err)
            if fix is None:
                break
            command, explanation = fix


# ------------------------------------------------------------------------------
# Raw single-keypress reading (arrow keys, space, enter, quit).
# cbreak mode via termios/tty.
# ------------------------------------------------------------------------------
@contextlib.contextmanager
def raw_input_mode():
    """Enables immediate single-keypress reads with no Enter or echo."""
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


def read_key() -> str:
    """Blocks for one logical keypress. Returns one of:
    'UP', 'DOWN', 'SPACE', 'ENTER', 'QUIT', or 'OTHER' for anything else."""
    return _read_key_unix()


def checkbox_menu(items: list[str]) -> list[int] | None:
    """Arrow-key + space multi-select menu. Returns the selected indices,
    or None if the user quit."""
    selected = [False] * len(items)
    cursor = 0

    def render() -> None:
        clear_screen()
        print()
        render_full_menu(items, selected, cursor)
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
#
# To add a new menu item:
#   - CLI tool installable via a package manager -> add a ToolSpec to
#     TOOL_SPECS above, then add _spec_entry("Your Tool Name", GROUP_...) below.
#   - Keep items of the same group next to each other; a group heading is
#     drawn whenever the group changes.
#   - Anything else (custom download, external installer, etc.) -> write a
#     handler function and add a MenuEntry directly, same as GCC below. Set check_bin if there's a binary to flag as missing.
# Nothing else needs to change: the checkbox menu, the "not installed"
# indicator, and dispatch on selection all read from this one list.
# ------------------------------------------------------------------------------
@dataclass(frozen=True)
class MenuEntry:
    name: str
    handler: Callable[[], None]
    check_bin: str | None = None            # binary checked for the "not installed" indicator; None to skip
    version_cmd: list[str] | None = None    # command to show inline once installed; None to skip
    version_on_stderr: bool = False         # e.g. `java -version` prints to stderr
    group: str = ""                         # heading shown above the item; keep items of one group together


GROUP_LANGUAGES = "Languages & Runtimes"
GROUP_TOOLS = "Developer Tools"
GROUP_ASSISTANT = "Assistant"


def _spec_entry(name: str, group: str = "") -> MenuEntry:
    spec = TOOL_SPECS_BY_NAME[name]
    return MenuEntry(
        spec.name, lambda: install_tool(spec), check_bin=spec.bin,
        version_cmd=spec.version_cmd, version_on_stderr=spec.version_on_stderr,
        group=group,
    )


MENU_ITEMS: list[MenuEntry] = [
    _spec_entry("Node.js & npm", GROUP_LANGUAGES),
    _spec_entry("OpenJDK", GROUP_LANGUAGES),
    _spec_entry("Go", GROUP_LANGUAGES),
    _spec_entry("Rust", GROUP_LANGUAGES),
    MenuEntry("GCC", configure_gcc, check_bin="gcc", version_cmd=["gcc", "--version"], group=GROUP_LANGUAGES),
    _spec_entry("Git", GROUP_TOOLS),
    _spec_entry("GitHub CLI", GROUP_TOOLS),
    _spec_entry("CMake", GROUP_TOOLS),
    _spec_entry("Docker", GROUP_TOOLS),
    MenuEntry("Ask AVA (AI)", ask_ava, group=GROUP_ASSISTANT),
]

# Caches each installed tool's version string for the life of the menu
# session, so re-rendering on every arrow-key press doesn't re-spawn a
# subprocess per keystroke. Cleared each time interactive_menu() runs, so a
# tool just installed shows its version on the next visit to the menu.
_VERSION_CACHE: dict[str, str | None] = {}


_VERSION_NUMBER_RE = re.compile(r"\d+(?:\.\d+){1,2}")


def _cached_version(entry: MenuEntry) -> str | None:
    if entry.version_cmd is None:
        return None
    if entry.name not in _VERSION_CACHE:
        try:
            result = subprocess.run(entry.version_cmd, capture_output=True, text=True)
            lines = (result.stderr if entry.version_on_stderr else result.stdout).splitlines()
            raw = lines[0].strip() if lines else ""
            # Pull out just the version number (e.g. "2.43.0") instead of
            # the full raw string (e.g. "git version 2.43.0") to keep the
            # menu row compact.
            match = _VERSION_NUMBER_RE.search(raw)
            _VERSION_CACHE[entry.name] = f"v{match.group()}" if match else (raw or None)
        except Exception:
            _VERSION_CACHE[entry.name] = None
    return _VERSION_CACHE[entry.name]


def interactive_menu() -> bool:
    """Shows the menu, runs the selection, and returns False on quit."""
    _VERSION_CACHE.clear()
    labels = [entry.name for entry in MENU_ITEMS]
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
        MENU_ITEMS[i].handler()
        print()

    try:
        input(f"{ui_margin()} {C.GRAY}Press Enter to return to the menu...{C.RESET}")
    except (EOFError, KeyboardInterrupt):
        print()
        return False

    return True


def main() -> None:
    if not sys.stdin.isatty():
        log_error("Interactive terminal required.")
        sys.exit(1)

    try:
        bootstrap_local_ai()
        while interactive_menu():
            pass
    except KeyboardInterrupt:
        print()
        log_info("Interrupted. Exiting AVA.")
        sys.exit(0)


if __name__ == "__main__":
    main()
