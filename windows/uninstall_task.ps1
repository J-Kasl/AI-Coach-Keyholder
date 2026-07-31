<#
.SYNOPSIS
    Unregisters the AI Coach & Keyholder bot's Scheduled Task.

.PARAMETER TaskName
    Must match whatever was passed to install_task.ps1. Defaults to
    "AICoachKeyholderBot", the same default install_task.ps1 uses.

.EXAMPLE
    .\uninstall_task.ps1
    .\uninstall_task.ps1 -TaskName "MyBot"
#>

param(
    [string]$TaskName = "AICoachKeyholderBot"
)

$ErrorActionPreference = "Stop"

$Existing = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
if ($null -eq $Existing) {
    Write-Host "No Scheduled Task named '$TaskName' is registered -- nothing to do."
    exit 0
}

# Stop it first if it happens to be running -- Unregister-ScheduledTask
# does not itself stop a currently-running instance.
Stop-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue

Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false

Write-Host "Scheduled Task '$TaskName' removed. The bot process itself, if still running, is not killed by this --"
Write-Host "close its window / use Task Manager if a run_bot.ps1-launched process is still active."
