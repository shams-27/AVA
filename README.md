# AVA — Terminal Assistant

AVA is an interactive, cross-platform terminal assistant that automates
common developer environment setup tasks. It's a single Python file with
no dependencies beyond the standard library.

## Quick start

### Linux / macOS
Open Linux/Mac Terminal, type in the line given below and press Enter.
```bash
curl -fsSL https://raw.githubusercontent.com/shams-27/AVA/main/install.sh | bash
```
### Windows 

Open Windows Terminal, type in the line given below and press Enter.
```powershell
irm https://raw.githubusercontent.com/shams-27/AVA/main/install.ps1 | iex
```

## What it does

AVA presents a menu of setup tasks and runs whichever ones you pick:

- **Download VS Code Profile** — fetches a preconfigured VS Code settings
  profile to your Downloads folder.
- **Configure OpenJDK** — detects your platform's package manager and
  installs OpenJDK if it isn't already present.
- **Configure Node.js & npm** — same idea, for Node.js and npm.
- **Configure GCC** *(Windows only)* — hands off to an interactive
  PowerShell script that sets up a GCC toolchain.

For each task, AVA checks whether the tool is already installed before
doing anything, so re-running it is always safe.

## How the menu works

- **↑ / ↓** — move the highlighted item
- **Space** — check/uncheck a task
- **Enter** — run everything checked (or, if nothing's checked, run just
  the highlighted item)
- **q** or **Esc** — quit without running anything

You can select and queue up multiple tasks at once before running them.

## Platform support

AVA detects the OS it's running on (Linux, macOS, or Windows) and adjusts
its behavior accordingly:

- Package manager detection: `apt`, `dnf`, `pacman`, `zypper`, `apk` on
  Linux; `winget`, `choco` on Windows.
- Elevated privileges are requested via `sudo` on Linux/macOS; on Windows,
  AVA asks you to re-run it from an elevated terminal if needed.
- Output adapts to the terminal it's running in — full color and Unicode
  glyphs on modern terminals, a plain ASCII fallback otherwise.

## Requirements

- Python 3.9+
- An interactive terminal (AVA needs a real TTY to run)

## Versioning

The current version is printed in AVA's header box on launch.
