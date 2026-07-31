<#
.SYNOPSIS
    Registers the AI Coach & Keyholder bot as a Windows Scheduled Task.

.DESCRIPTION
    Runs run_bot.ps1 (in this same directory) whenever the current
    user logs on. Deliberately parametrized -- no hardcoded path to
    any specific user's profile, no token, no absolute path baked in
    beyond what is derived from this script's own location at install
    time.

    Default mode: AtLogOn, as the current interactive user, with no
    stored password. See windows/README.md's "AtLogOn vs. independent
    of logon" section for the full tradeoff -- summarized: this is a
    personal bot on a personal machine, so requiring the owner to
    actually be logged on is a reasonable, safer default than the
    alternative (which needs a stored, protected credential and is
    the appropriate choice for a shared/server machine, not this one).

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

$ScriptDir = $PSScriptRoot
$RunScript = Join-Path $ScriptDir "run_bot.ps1"

if (-not (Test-Path $RunScript)) {
    throw "run_bot.ps1 not found next to this script ($RunScript) -- install_task.ps1 must stay in the same windows\ directory as run_bot.ps1."
}

$Action = New-ScheduledTaskAction -Execute "powershell.exe" `
    -Argument "-NoProfile -ExecutionPolicy Bypass -File `"$RunScript`""

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
Write-Host "Logs: $(Join-Path (Split-Path -Parent $ScriptDir) 'logs\bot.log')"
