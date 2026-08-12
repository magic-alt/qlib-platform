[CmdletBinding()]
param(
    [string]$TaskName = "QmtReadOnlyBrokerGateway",
    [string]$PythonExe = ".\.venv\python.exe",
    [int]$Port = 8765,
    [switch]$Uninstall
)

$ErrorActionPreference = "Stop"

if ($Uninstall) {
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false -ErrorAction SilentlyContinue
    exit 0
}

$resolvedPython = (Resolve-Path -LiteralPath $PythonExe).Path
$workdir = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot "..")).Path
$arguments = "-m tushare_qlib.qmt_gateway serve --host 127.0.0.1 --port $Port"
$action = New-ScheduledTaskAction -Execute $resolvedPython -Argument $arguments -WorkingDirectory $workdir
$trigger = New-ScheduledTaskTrigger -AtLogOn
$settings = New-ScheduledTaskSettingsSet -MultipleInstances IgnoreNew -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1)

Register-ScheduledTask `
    -TaskName $TaskName `
    -Action $action `
    -Trigger $trigger `
    -Settings $settings `
    -Description "Local GET-only QMT Broker Gateway" `
    -Force | Out-Null

Write-Output "Installed scheduled task: $TaskName"
