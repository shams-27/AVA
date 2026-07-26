# AVA

A terminal assistant that automates developer environment setup.

## Quick start (single command, nothing installed permanently)

### Linux / macOS

```bash
curl -fsSL https://raw.githubusercontent.com/shams-27/AVA/main/install.sh | bash
```

### Windows (PowerShell)

```powershell
irm https://raw.githubusercontent.com/shams-27/AVA/main/install.ps1 | iex
```

Each script:

1. Checks for a Python 3 interpreter.
2. Installs one if missing — via `apt`/`dnf`/`pacman`/`zypper`/`apk`/`brew` on
   Linux/macOS, or `winget`/`choco` on Windows.
3. Downloads `AVA.py` into a temp directory.
4. Runs it.
5. Deletes the temp directory afterward — nothing is left on disk.

## Repo structure

```
.
├── AVA.py          # main application
├── install.sh      # Linux/macOS bootstrap (fetch + run, one-liner via curl)
├── install.ps1      # Windows bootstrap (fetch + run, one-liner via irm)
└── README.md
```

## Note on security

Piping a remote script into `bash`/`iex` runs code with your current user's
privileges (this is the same pattern used by nvm, rustup, and Homebrew's
installer). Anyone using the one-liner is trusting `shams-27/AVA`'s `main`
branch. Once the project is stable, it's worth pointing the URL at a specific
commit or tag instead of always `main`, so the command's behavior can't
change silently later.
