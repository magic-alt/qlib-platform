param(
    [string]$TaskName = "Qlib-Tushare-Daily-Sync",
    [Parameter(Mandatory = $true)]
    [string]$PythonExe,
    [Parameter(Mandatory = $true)]
    [string]$RepoRoot,
    [string]$ConfigPath = "configs\pipeline.yaml",
    [string]$At = "18:30",
    [switch]$WhatIf
)

$ErrorActionPreference = "Stop"
$resolvedRepo = (Resolve-Path -LiteralPath $RepoRoot).Path
$resolvedPython = (Resolve-Path -LiteralPath $PythonExe).Path
$resolvedConfig = if ([System.IO.Path]::IsPathRooted($ConfigPath)) {
    (Resolve-Path -LiteralPath $ConfigPath).Path
}
else {
    (Resolve-Path -LiteralPath (Join-Path $resolvedRepo $ConfigPath)).Path
}
$runner = (Resolve-Path -LiteralPath (Join-Path $resolvedRepo "scripts\run_tushare_daily_sync.ps1")).Path
$start = [datetime]::ParseExact($At, "HH:mm", [System.Globalization.CultureInfo]::InvariantCulture)

$quotedRunner = '"' + $runner + '"'
$quotedPython = '"' + $resolvedPython + '"'
$quotedRepo = '"' + $resolvedRepo + '"'
$quotedConfig = '"' + $resolvedConfig + '"'
$arguments = "-NoProfile -NonInteractive -WindowStyle Hidden -ExecutionPolicy Bypass " +
    "-File $quotedRunner -PythonExe $quotedPython -RepoRoot $quotedRepo -ConfigPath $quotedConfig"
$action = New-ScheduledTaskAction -Execute "powershell.exe" -Argument $arguments -WorkingDirectory $resolvedRepo
$trigger = New-ScheduledTaskTrigger -Daily -At $start
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -MultipleInstances IgnoreNew -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 30) -ExecutionTimeLimit (New-TimeSpan -Hours 4)
$identity = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name
$principal = New-ScheduledTaskPrincipal -UserId $identity -LogonType Interactive -RunLevel Limited

$register = @{
    TaskName = $TaskName
    Action = $action
    Trigger = $trigger
    Settings = $settings
    Principal = $principal
    Description = "Daily TuShare market-data revision check and atomic Qlib publication."
    Force = $true
}
Register-ScheduledTask @register -WhatIf:$WhatIf
