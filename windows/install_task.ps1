<#
.SYNOPSIS
    Registers the AI Coach & Keyholder bot as a Windows Scheduled Task.

.DESCRIPTION
    Runs run_bot.ps1 (in this same directory) whenever the current
    user logs on. Deliberately parametrized -- no hardcoded path to
    any specific user's profile, no token, no absolute path baked in
    beyond what is derived from this script's own location, and the
    real Python interpreter path this script detects at install time
    (see below) -- both computed fresh on whatever machine this script
    actually runs on, never assumed from any one person's setup.

    Default mode: AtLogOn, as the current interactive user, with no
    stored password. See windows/README.md's "AtLogOn vs. independent
    of logon" section for the full tradeoff -- summarized: this is a
    personal bot on a personal machine, so requiring the owner to
    actually be logged on is a reasonable, safer default than the
    alternative (which needs a stored, protected credential and is
    the appropriate choice for a shared/server machine, not this one).

    **Interpreter detection (found necessary under real-world use, not
    theorized):** a real Windows install was found where neither `py`
    nor `python` resolve to a working interpreter at all -- Windows
    App Execution Alias stubs intercepted both, opening a "choose an
    app"/Store prompt instead of running Python, and the Scheduled
    Task silently failed (`LastTaskResult = 1`) with no useful log
    output beyond "Starting bot via py -m bot.discord_bot" -- the
    launch line, never reached. This script now detects a real,
    working interpreter itself (Find-PythonInterpreter, common.ps1)
    and bakes its absolute path directly into the Task's own Action,
    so the Task never depends on `py`/`python` resolving correctly at
    all, on any machine, at run time.

    **Must be run from an elevated (Administrator) PowerShell session
    (found necessary under real-world use, not assumed):**
    `Register-ScheduledTask` was found, on a real Windows install, to
    require elevation even for a task that only ever runs as the
    current user with no stored credential. This script checks for
    elevation itself, at the very start, and fails with a clear
    message rather than letting `Register-ScheduledTask` fail deeper
    in with a less obvious error.

.PARAMETER TaskName
    The Scheduled Task's name. Defaults to "AICoachKeyholderBot".

.EXAMPLE
    .\install_task.ps1
    .\install_task.ps1 -TaskName "MyBot"
#>

param(
    [string]$TaskName = "AICoachKeyholderBot"
)

$ErrorActionPreference = "Stop"

$CurrentPrincipal = New-Object Security.Principal.WindowsPrincipal([Security.Principal.WindowsIdentity]::GetCurrent())
if (-not $CurrentPrincipal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    throw (
        "This script needs an elevated (Administrator) PowerShell session -- " +
        "Register-ScheduledTask has been found to require it even for a task that only " +
        "ever runs as your own user account with no stored password. " +
        "Right-click PowerShell (or Windows Terminal) and choose 'Run as Administrator', " +
        "then run this script again from the elevated window."
    )
}

$ScriptDir = $PSScriptRoot
$ProjectRoot = Split-Path -Parent $ScriptDir
$RunScript = Join-Path $ScriptDir "run_bot.ps1"

if (-not (Test-Path $RunScript)) {
    throw "run_bot.ps1 not found next to this script ($RunScript) -- install_task.ps1 must stay in the same windows\ directory as run_bot.ps1."
}

. (Join-Path $ScriptDir "common.ps1")

$PythonPath = Find-PythonInterpreter -ProjectRoot $ProjectRoot
if ($null -eq $PythonPath) {
    throw (
        "Could not find a working Python interpreter (checked $ProjectRoot\.venv\Scripts\python.exe, " +
        "then py/python/python3 on PATH, skipping any WindowsApps execution-alias stub). " +
        "Install Python (from python.org, with 'Add to PATH' checked) or create a .venv in the " +
        "project root, then run this script again."
    )
}

Write-Host "Using Python:"
Write-Host "  $PythonPath"

$Action = New-ScheduledTaskAction -Execute "powershell.exe" `
    -Argument "-NoProfile -ExecutionPolicy Bypass -File `"$RunScript`" -PythonPath `"$PythonPath`""

# AtLogOn, current user, no stored password -- see this script's own
# .DESCRIPTION for why this is the chosen default.
$Trigger = New-ScheduledTaskTrigger -AtLogOn

# ExecutionTimeLimit = unlimited -- this is a long-running bot, not a
# batch job; Task Scheduler's own default (3 days) would silently kill
# it otherwise.
$Settings = New-ScheduledTaskSettingsSet `
    -ExecutionTimeLimit ([TimeSpan]::Zero) `
    -RestartCount 3 `
    -RestartInterval (New-TimeSpan -Minutes 1) `
    -StartWhenAvailable

$Principal = New-ScheduledTaskPrincipal -UserId "$env:USERDOMAIN\$env:USERNAME" -LogonType Interactive

Register-ScheduledTask -TaskName $TaskName -Action $Action -Trigger $Trigger `
    -Settings $Settings -Principal $Principal -Force | Out-Null

Write-Host "Scheduled Task '$TaskName' registered -- will start automatically the next time you log on."
Write-Host "To start it right now without logging off/on again: Start-ScheduledTask -TaskName '$TaskName'"
Write-Host "Logs: $(Join-Path $ProjectRoot 'logs\bot.log')"
