# AVA — Terminal Assistant

AVA is an interactive terminal assistant for Linux that automates
common developer environment setup tasks. It's a single Python file with
no dependencies beyond the standard library.

## Quick start

Open a Linux terminal, type in the line given below and press Enter.
```bash
curl -fsSL https://raw.githubusercontent.com/shams-27/AVA/main/install.sh | bash
```

## What it does

AVA presents a menu of setup tasks and runs whichever ones you pick:

**Languages & Runtimes**
- **Node.js & npm**, **OpenJDK**, **Go**, **Rust** —
  detects your platform's package manager and installs each one if it
  isn't already present.
- **GCC** — installs a C/C++ toolchain through your package manager.

**Developer Tools**
- **Git**, **GitHub CLI**, **CMake**, **Docker** — same install flow.
  On Linux, start the Docker service after installing it
  (`sudo systemctl enable --now docker`) and add yourself to the `docker`
  group to use it without `sudo`.

**Assistant**
- **Ask AVA (AI)** — describe a task in plain English and AVA suggests
  one shell command, shows it, and runs it only after you confirm.

For each task, AVA checks whether the tool is already installed before
doing anything, so re-running it is always safe.

## Ask AVA (AI)

Pick **Ask AVA (AI)** in the menu, type what you want (for example,
`find files over 100MB in my home folder`), and AVA shows one suggested
command. Nothing runs until you answer `y`.

- Dangerous commands (wiping the root or home directory, formatting a disk,
  fork bombs and similar) are refused, whatever you answer.
- Risky ones (recursive deletes, `curl | sh`, force-pushes, reboots and
  similar) need you to type the full word `yes`.
- Everything runs on your machine. Nothing is sent over the internet, and
  no account or API key is needed.

Ask AVA always uses the `qwen2.5-coder:3b` model through
[Ollama](https://ollama.com). There is nothing to configure: when AVA
starts, it checks that Ollama and the model are in place and, if not,
installs Ollama, starts it and downloads the model (about 2 GB) under a
"Setting things up" progress box with a percentage, before the menu
appears. If everything is already set up, nothing is shown. On Linux
distributions other than Arch, Ollama is installed with its official
installer from ollama.com. If the setup fails (for example, you're offline),
AVA continues to the menu and Ask AVA offers to try again.

To try a different Ollama model, set `AVA_OLLAMA_MODEL` (for example
`export AVA_OLLAMA_MODEL=qwen2.5-coder:7b`). No extra Python packages are
needed.

## How the menu works

- **↑ / ↓** — move the highlighted item
- **Space** — check/uncheck a task
- **Enter** — run everything checked (or, if nothing's checked, run just
  the highlighted item)
- **q** or **Esc** — quit without running anything

You can select and queue up multiple tasks at once before running them.

Tasks are grouped under headings (Languages & Runtimes, Developer Tools,
Assistant). Each row shows its status on the right: a green check with the
installed version, or `not installed`. The footer shows how many tasks you
have selected.

## Platform support

AVA runs on Linux only and exits immediately on any other OS.

- Package manager detection: `apt`, `dnf`, `pacman`, `zypper`, `apk`.
- Elevated privileges are requested via `sudo`.
- Output adapts to the terminal it's running in — full color and Unicode
  glyphs on UTF-8 terminals, a plain ASCII fallback otherwise.

## Testing

The unit tests cover the command risk classifier, AI reply parsing, command
post-processing, package-manager detection and the tool install specs. They
use only the standard library and run on every push through GitHub Actions
(`.github/workflows/tests.yml`).

```bash
python3 -m unittest discover -s tests -v
```

## Requirements

- Python 3.9+
- An interactive terminal (AVA needs a real TTY to run)

## Versioning

The current version is printed in AVA's header box on launch.
