<#
.SYNOPSIS
    Launches the AI Coach & Keyholder Discord bot.

.DESCRIPTION
    Derives the project root from this script's own location
    ($PSScriptRoot's parent) -- contains no hardcoded path to any
    specific user's home directory, and no token.

    Uses whatever real Python interpreter -PythonPath points to, if
    given (this is what install_task.ps1 bakes into the Scheduled
    Task's own Action, detected once at install time via
    Find-PythonInterpreter -- see common.ps1). If -PythonPath is not
    given (a manual, interactive run), detects one itself, the same
    robust way -- never a bare `python`/`py` call, since those are
    exactly what were found, on a real Windows install, to silently
    fail (Windows' own "App Execution Alias" stub behavior -- see
    common.ps1's own docstring for the full explanation).

    Appends timestamped stdout/stderr to logs\bot.log (relative to the
    project root, git-ignored -- see .gitignore).

.PARAMETER PythonPath
    Absolute path to the Python interpreter to use. Optional -- if
    omitted, detected via Find-PythonInterpreter.
#>

param(
    [string]$PythonPath
)

$ErrorActionPreference = "Stop"

$ProjectRoot = Split-Path -Parent $PSScriptRoot
$LogDir = Join-Path $ProjectRoot "logs"
$LogFile = Join-Path $LogDir "bot.log"

if (-not (Test-Path $LogDir)) {
    New-Item -ItemType Directory -Path $LogDir | Out-Null
}

Set-Location $ProjectRoot

. (Join-Path $PSScriptRoot "common.ps1")

if ([string]::IsNullOrWhiteSpace($PythonPath)) {
    $PythonPath = Find-PythonInterpreter -ProjectRoot $ProjectRoot
}

$Timestamp = Get-Date -Format "yyyy-MM-dd HH:mm:ss"

if ([string]::IsNullOrWhiteSpace($PythonPath) -or -not (Test-Path $PythonPath)) {
    $Message = "[$Timestamp] No working Python interpreter found (checked .venv, py, python, python3). " +
                "Install Python and/or create a .venv, or pass -PythonPath explicitly."
    Add-Content -Path $LogFile -Value $Message
    Write-Error $Message
    exit 1
}

Add-Content -Path $LogFile -Value "[$Timestamp] Starting bot via `"$PythonPath`" -m bot.discord_bot (cwd=$ProjectRoot)"

# Both stdout and stderr appended to the same log file -- Python's own
# logging (bot/discord_bot.py's logging.basicConfig) already writes to
# stderr by default, so this captures it without any extra
# configuration on the Python side.
& $PythonPath -m bot.discord_bot 2>&1 | Add-Content -Path $LogFile

$ExitCode = $LASTEXITCODE
$Timestamp = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
Add-Content -Path $LogFile -Value "[$Timestamp] Bot process exited with code $ExitCode"
exit $ExitCode
