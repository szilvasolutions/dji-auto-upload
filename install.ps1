# dji-auto-upload bootstrap for Windows.
#
#   irm https://raw.githubusercontent.com/szilvasolutions/dji-auto-upload/main/install.ps1 | iex
#
# Installs Python and rclone if missing (via winget), installs dji-auto-upload,
# then starts the interactive setup wizard, which ends by offering to install
# the plug-in auto-trigger. One paste, then answer questions.
#
# Runs in-memory, per-user, no admin required.

$ErrorActionPreference = 'Stop'

function Write-Step($msg) { Write-Host "==> $msg" -ForegroundColor Cyan }

function Update-SessionPath {
    # A winget install extends the *registry* PATH; pull it into this session.
    $env:Path = [Environment]::GetEnvironmentVariable('Path', 'Machine') + ';' +
                [Environment]::GetEnvironmentVariable('Path', 'User')
}

function Find-Python {
    # Prefer the py launcher; fall back to python. Reject anything below 3.10
    # and the Microsoft Store stub (which fails the version probe anyway).
    $candidates = @(
        @{ Exe = 'py';     Args = @('-3.13') },
        @{ Exe = 'py';     Args = @('-3.12') },
        @{ Exe = 'py';     Args = @('-3.11') },
        @{ Exe = 'py';     Args = @('-3.10') },
        @{ Exe = 'py';     Args = @('-3') },
        @{ Exe = 'python'; Args = @() }
    )
    foreach ($c in $candidates) {
        if (-not (Get-Command $c.Exe -ErrorAction SilentlyContinue)) { continue }
        try {
            $v = & $c.Exe $c.Args -c "import sys; print(sys.version_info[0]*100+sys.version_info[1])" 2>$null
            if ($LASTEXITCODE -eq 0 -and [int]$v -ge 310) { return $c }
        } catch { }
    }
    return $null
}

Write-Host ""
Write-Host "dji-auto-upload installer" -ForegroundColor Green
Write-Host "-------------------------"

# --- Python -------------------------------------------------------------------
$py = Find-Python
if (-not $py) {
    Write-Step "Python 3.10+ not found - installing via winget (this can take a minute)"
    winget install -e --id Python.Python.3.12 --source winget `
        --accept-package-agreements --accept-source-agreements
    Update-SessionPath
    $py = Find-Python
    if (-not $py) {
        Write-Host "Python was installed but is not callable yet. Close this window, open a new PowerShell, and re-run the install command." -ForegroundColor Yellow
        return
    }
} else {
    Write-Step "Python found"
}

# --- rclone -------------------------------------------------------------------
if (-not (Get-Command rclone -ErrorAction SilentlyContinue)) {
    Write-Step "rclone not found - installing via winget"
    winget install -e --id Rclone.Rclone --source winget `
        --accept-package-agreements --accept-source-agreements
    Update-SessionPath
} else {
    Write-Step "rclone found"
}

# --- dji-auto-upload ------------------------------------------------------------
Write-Step "Installing dji-auto-upload"
& $py.Exe $py.Args -m pip install --upgrade --quiet `
    "https://github.com/szilvasolutions/dji-auto-upload/archive/refs/heads/main.zip"
if ($LASTEXITCODE -ne 0) {
    Write-Host "pip install failed - scroll up for the error." -ForegroundColor Red
    return
}

# --- Setup wizard ---------------------------------------------------------------
Write-Step "Starting setup (the wizard also offers to install the plug-in trigger)"
Write-Host ""
& $py.Exe $py.Args -m dji_auto_upload setup
