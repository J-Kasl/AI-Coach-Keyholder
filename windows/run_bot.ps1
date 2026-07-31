<#
.SYNOPSIS
    Launches the AI Coach & Keyholder Discord bot.

.DESCRIPTION
    Derives the project root from this script's own location
    ($PSScriptRoot's parent) -- contains no hardcoded path to any
    specific user's home directory, and no token. Prefers the
    project's own .venv if one exists; falls back to the `py` launcher
    otherwise. Appends timestamped stdout/stderr to logs\bot.log
    (relative to the project root, git-ignored -- see .gitignore).

    Intended to be called directly for manual runs during development,
    and by install_task.ps1's registered Scheduled Task for unattended
    runs. Behaves identically either way.
#>

$ErrorActionPreference = "Stop"

$ProjectRoot = Split-Path -Parent $PSScriptRoot
$LogDir = Join-Path $ProjectRoot "logs"
$LogFile = Join-Path $LogDir "bot.log"

if (-not (Test-Path $LogDir)) {
    New-Item -ItemType Directory -Path $LogDir | Out-Null
}

Set-Location $ProjectRoot

$VenvPython = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
if (Test-Path $VenvPython) {
    $PythonExe = $VenvPython
} else {
    # `py` launcher -- more reliable than a bare `python` on a machine
    # with multiple Python installations (see README.md's own
    # Installation section for why this project prefers it on Windows).
    $PythonExe = "py"
}

$Timestamp = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
Add-Content -Path $LogFile -Value "[$Timestamp] Starting bot via $PythonExe -m bot.discord_bot (cwd=$ProjectRoot)"

# Both stdout and stderr appended to the same log file -- Python's own
# logging (bot/discord_bot.py's logging.basicConfig) already writes to
# stderr by default, so this captures it without any extra
# configuration on the Python side.
& $PythonExe -m bot.discord_bot 2>&1 | Add-Content -Path $LogFile

$ExitCode = $LASTEXITCODE
$Timestamp = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
Add-Content -Path $LogFile -Value "[$Timestamp] Bot process exited with code $ExitCode"
exit $ExitCode
