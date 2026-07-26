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
    $pythonCmd = Get-Command python -ErrorAction SilentlyContinue
    if ($pythonCmd) {
        # Windows ships a fake "python" app execution alias that just opens
        # the Microsoft Store when no real interpreter is installed. Running
        # it prints to stderr, which — combined with $ErrorActionPreference
        # = "Stop" above — would otherwise crash this whole script before
        # we ever get a chance to install a real Python. Suppress that
        # locally and treat any failure here as "no usable python found".
        $prevPref = $ErrorActionPreference
        $ErrorActionPreference = "SilentlyContinue"
        try {
            $verOutput = & python --version 2>&1
            if ($LASTEXITCODE -eq 0 -and $verOutput -match "Python 3") {
                return "python"
            }
        }
        catch {
            # Fall through — the stub throwing counts as "not usable".
        }
        finally {
            $ErrorActionPreference = $prevPref
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
        try {
            winget install -e --id Python.Python.3.12 --silent `
                --accept-package-agreements --accept-source-agreements
        }
        catch {
            Err "winget reported an error, but it may have still installed Python. Checking..."
        }
    }
    elseif (Get-Command choco -ErrorAction SilentlyContinue) {
        try {
            choco install python -y
        }
        catch {
            Err "choco reported an error, but it may have still installed Python. Checking..."
        }
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
