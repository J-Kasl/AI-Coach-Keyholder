<#
.SYNOPSIS
    Unregisters the AI Coach & Keyholder bot's Scheduled Task.

.DESCRIPTION
    Requires an elevated (Administrator) PowerShell session for the
    actual removal step -- the same requirement install_task.ps1's own
    Register-ScheduledTask call has (found necessary under real-world
    use); checked here too, before Unregister-ScheduledTask, rather
    than letting it fail with a less obvious error. The read-only
    "is anything even installed" check below does not require
    elevation and runs regardless.

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

$CurrentPrincipal = New-Object Security.Principal.WindowsPrincipal([Security.Principal.WindowsIdentity]::GetCurrent())
if (-not $CurrentPrincipal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    throw (
        "This script needs an elevated (Administrator) PowerShell session to remove the " +
        "Scheduled Task -- the same requirement install_task.ps1's registration step has. " +
        "Right-click PowerShell (or Windows Terminal) and choose 'Run as Administrator', " +
        "then run this script again from the elevated window."
    )
}

# Stop it first if it happens to be running -- Unregister-ScheduledTask
# does not itself stop a currently-running instance.
Stop-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue

Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false

Write-Host "Scheduled Task '$TaskName' removed. The bot process itself, if still running, is not killed by this --"
Write-Host "close its window / use Task Manager if a run_bot.ps1-launched process is still active."
