# ==============================================================================
# AVA bootstrap (Windows)
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

# --- 1. Locate an existing Python interpreter --------------------------------
# The "py" launcher is the most reliable signal on Windows; fall back to
# "python" on PATH.
function Find-Python {
    if (Get-Command py -ErrorAction SilentlyContinue) {
        return "py"
    }
    $pythonCmd = Get-Command python -ErrorAction SilentlyContinue
    if ($pythonCmd) {
        # Suppress errors locally: Windows ships a fake "python" alias that
        # just opens the Microsoft Store when no real interpreter exists,
        # and its stderr output would otherwise abort the script.
        $prevPref = $ErrorActionPreference
        $ErrorActionPreference = "SilentlyContinue"
        try {
            $verOutput = & python --version 2>&1
            if ($LASTEXITCODE -eq 0 -and $verOutput -match "Python 3") {
                return "python"
            }
        }
        catch {
            # Stub throwing counts as "not usable" — fall through.
        }
        finally {
            $ErrorActionPreference = $prevPref
        }
    }
    return $null
}

$PythonBin = Find-Python

# --- 2. Install Python via winget, falling back to choco --------------------
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

    # Refresh PATH so the newly installed interpreter is visible this session
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

# --- 3. Download AVA.py ------------------------------------------------------
Info "Downloading AVA..."
try {
    Invoke-WebRequest -Uri $AvaRawUrl -OutFile $AvaPath -UseBasicParsing
}
catch {
    Err "Failed to download AVA.py: $_"
    Remove-Item -Recurse -Force $TmpDir -ErrorAction SilentlyContinue
    exit 1
}

# --- 4. Launch AVA, then clean up the temp directory -------------------------
try {
    & $PythonBin $AvaPath @args
}
finally {
    Remove-Item -Recurse -Force $TmpDir -ErrorAction SilentlyContinue
}
