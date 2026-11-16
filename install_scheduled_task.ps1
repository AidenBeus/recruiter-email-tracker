param(
    [string]$TaskName = "RecruiterEmailTrackerHourly"
)

$ErrorActionPreference = "Stop"
$projectPath = Split-Path -Parent $PSCommandPath
$runnerPath = Join-Path $projectPath "run_scheduled_import.ps1"
$powershellPath = Join-Path $env:SystemRoot "System32\WindowsPowerShell\v1.0\powershell.exe"

# Start soon, then repeat hourly. The task runs as the Windows user registering it.
$firstRun = (Get-Date).AddMinutes(5)
$action = New-ScheduledTaskAction -Execute $powershellPath -Argument (
    "-NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File `"$runnerPath`""
)
$trigger = New-ScheduledTaskTrigger -Once -At $firstRun `
    -RepetitionInterval (New-TimeSpan -Hours 1) `
    -RepetitionDuration (New-TimeSpan -Days 3650)
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -MultipleInstances IgnoreNew `
    -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries

Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger `
    -Settings $settings -Description "Imports recent Gmail metadata for the recruiter email tracker." `
    -Force | Out-Null

Get-ScheduledTask -TaskName $TaskName | Select-Object TaskName, State
Get-ScheduledTaskInfo -TaskName $TaskName | Select-Object LastRunTime, NextRunTime, LastTaskResult
