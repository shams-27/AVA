# ==============================================================================
# AVA bootstrap (Windows)
# Ensures Python is present, installing it if necessary, then downloads
# AVA.py into a temp directory and runs it. Nothing is left behind.
#
# Usage:
#   irm https://raw.githubusercontent.com/shams-27/AVA/main/install.ps1 | iex
# ==============================================================================

$ErrorActionPreference = "Stop"

$AvaRawUrl = "https://raw.githubusercontent.com/shams-27/AVA/main/AVA.py"

$TmpDir = Join-Path ([System.IO.Path]::GetTempPath()) ("ava-" + [System.Guid]::NewGuid())
New-Item -ItemType Directory -Path $TmpDir | Out-Null
$AvaPath = Join-Path $TmpDir "AVA.py"

function Info($msg)  { Write-Host "  i  $msg" -ForegroundColor Cyan }
function Ok($msg)    { Write-Host "  +  $msg" -ForegroundColor Green }
function Err($msg)   { Write-Host "  x  $msg" -ForegroundColor Red }

# ------------------------------------------------------------------------------
# 1. Locate an existing Python interpreter (the "py" launcher is the most
#    reliable signal on Windows; fall back to "python" on PATH).
# ------------------------------------------------------------------------------
function Find-Python {
    if (Get-Command py -ErrorAction SilentlyContinue) {
        return "py"
    }
    if (Get-Command python -ErrorAction SilentlyContinue) {
        # Windows ships a fake "python" stub that opens the Store if no
        # real interpreter is installed — filter that out.
        $verOutput = & python --version 2>&1
        if ($LASTEXITCODE -eq 0 -and $verOutput -match "Python 3") {
            return "python"
        }
    }
    return $null
}

$PythonBin = Find-Python

# ------------------------------------------------------------------------------
# 2. If missing, install it via winget (falls back to choco if available)
# ------------------------------------------------------------------------------
if (-not $PythonBin) {
    Info "Python not found. Attempting to install it..."

    if (Get-Command winget -ErrorAction SilentlyContinue) {
        winget install -e --id Python.Python.3.12 --silent `
            --accept-package-agreements --accept-source-agreements
    }
    elseif (Get-Command choco -ErrorAction SilentlyContinue) {
        choco install python -y
    }
    else {
        Err "Neither winget nor choco is available. Please install Python manually from https://python.org and re-run this script."
        exit 1
    }

    # Refresh PATH for this session so the newly installed interpreter is visible
    $env:Path = [System.Environment]::GetEnvironmentVariable("Path", "Machine") + ";" +
                [System.Environment]::GetEnvironmentVariable("Path", "User")

    $PythonBin = Find-Python
    if (-not $PythonBin) {
        Err "Python installation finished, but the interpreter still isn't on PATH."
        Err "Please open a new terminal and re-run this script."
        exit 1
    }
    Ok "Python installed successfully."
}
else {
    $version = & $PythonBin --version
    Info "Python found: $version"
}

# ------------------------------------------------------------------------------
# 3. Download AVA.py
# ------------------------------------------------------------------------------
Info "Downloading AVA..."
try {
    Invoke-WebRequest -Uri $AvaRawUrl -OutFile $AvaPath -UseBasicParsing
}
catch {
    Err "Failed to download AVA.py: $_"
    Remove-Item -Recurse -Force $TmpDir -ErrorAction SilentlyContinue
    exit 1
}

# ------------------------------------------------------------------------------
# 4. Launch AVA, then clean up the temp directory
# ------------------------------------------------------------------------------
try {
    & $PythonBin $AvaPath @args
}
finally {
    Remove-Item -Recurse -Force $TmpDir -ErrorAction SilentlyContinue
}