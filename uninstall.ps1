# dji-auto-upload uninstaller for Windows.
#
#   irm https://raw.githubusercontent.com/szilvasolutions/dji-auto-upload/main/uninstall.ps1 | iex
#
# Stops the watcher, removes the Scheduled Task and generated scripts, deletes
# settings, and uninstalls the package from every Python that has it.
#
# Your footage is NOT deleted. rclone and its remotes are NOT touched. The
# script prints where the footage folder is so you can decide for yourself.

$ErrorActionPreference = 'Continue'
function Write-Step($m) { Write-Host "==> $m" -ForegroundColor Cyan }

Write-Host ""
Write-Host "dji-auto-upload uninstaller" -ForegroundColor Green
Write-Host "---------------------------"

# --- Let the tool clean up after itself first, while it still knows its paths --
$exe = Get-Command dji-auto-upload -ErrorAction SilentlyContinue
if ($exe) {
    Write-Step "Removing trigger and settings"
    & $exe.Source uninstall --yes
} else {
    Write-Step "dji-auto-upload not on PATH - cleaning up by hand"
}

# --- Stop anything still running ------------------------------------------------
Write-Step "Stopping any watcher still running"
Get-CimInstance Win32_Process -Filter "Name='powershell.exe' OR Name='wscript.exe'" -EA SilentlyContinue |
    Where-Object { $_.CommandLine -match 'dji-watcher|dji-run|dji-view|dji-auto-upload' } |
    ForEach-Object {
        Write-Host "    stopping PID $($_.ProcessId)"
        Stop-Process -Id $_.ProcessId -Force -EA SilentlyContinue
    }

# --- Scheduled Task -------------------------------------------------------------
Write-Step "Removing the Scheduled Task"
schtasks /end    /tn "DJI Auto Upload Watcher" 2>$null | Out-Null
schtasks /delete /tn "DJI Auto Upload Watcher" /f 2>$null | Out-Null

# --- The package, from every Python that has it ----------------------------------
Write-Step "Uninstalling the package"
$pythons = New-Object System.Collections.Generic.List[string]
foreach ($c in @(Get-Command python, python3, py -EA SilentlyContinue)) {
    if ($c.Source) { $pythons.Add($c.Source) }
}
# `py -0p` lists every registered interpreter, including ones not on PATH.
try {
    (& py -0p 2>$null) | ForEach-Object {
        if ($_ -match '([A-Za-z]:\\[^\s]+python\.exe)') { $pythons.Add($Matches[1]) }
    }
} catch { }
foreach ($p in @(
    "$env:LOCALAPPDATA\Programs\Python\Python313\python.exe",
    "$env:LOCALAPPDATA\Programs\Python\Python312\python.exe",
    "$env:LOCALAPPDATA\Programs\Python\Python311\python.exe",
    "$env:LOCALAPPDATA\Programs\Python\Python310\python.exe"
)) { if (Test-Path $p) { $pythons.Add($p) } }

foreach ($py in ($pythons | Select-Object -Unique)) {
    if ($py -notmatch 'py\.exe$' -and (Test-Path $py)) {
        & $py -m pip uninstall -y dji-auto-upload 2>$null | Out-Null
    }
}

# --- Leftover generated files ----------------------------------------------------
$scripts = "$env:LOCALAPPDATA\dji-auto-upload"
$footage = "$scripts\dji-auto-upload\stage"
$keep = Test-Path $footage
if ($keep) {
    Write-Step "Keeping your footage"
    Write-Host "    $footage" -ForegroundColor Yellow
    # Remove only the generated scripts, never the footage beside them.
    foreach ($f in @('dji-watcher.ps1','dji-run.ps1','dji-view.ps1',
                     'dji-watcher-launch.vbs','DjiAutoUploadTask.xml','watcher.log')) {
        Remove-Item -Force (Join-Path $scripts $f) -EA SilentlyContinue
    }
} else {
    Remove-Item -Recurse -Force $scripts -EA SilentlyContinue
}
Remove-Item -Recurse -Force "$env:APPDATA\dji-auto-upload" -EA SilentlyContinue

# --- Verify ----------------------------------------------------------------------
Write-Host ""
Write-Step "Checking"
$left = @()
if (Get-Command dji-auto-upload -EA SilentlyContinue) { $left += "the command is still on PATH" }
if (schtasks /query /tn "DJI Auto Upload Watcher" 2>$null) { $left += "the Scheduled Task still exists" }
if ($left.Count -eq 0) {
    Write-Host "Uninstalled." -ForegroundColor Green
} else {
    Write-Host ("Mostly done, but: " + ($left -join '; ')) -ForegroundColor Yellow
}
if ($keep) { Write-Host "Your footage is still at $footage" -ForegroundColor Cyan }
Write-Host "rclone and its remotes were left alone." -ForegroundColor DarkGray
