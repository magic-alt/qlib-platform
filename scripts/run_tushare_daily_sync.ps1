param(
    [Parameter(Mandatory = $true)]
    [string]$PythonExe,
    [Parameter(Mandatory = $true)]
    [string]$RepoRoot,
    [Parameter(Mandatory = $true)]
    [string]$ConfigPath
)

$ErrorActionPreference = "Stop"
$resolvedPython = (Resolve-Path -LiteralPath $PythonExe).Path
$resolvedRepo = (Resolve-Path -LiteralPath $RepoRoot).Path
$resolvedConfig = (Resolve-Path -LiteralPath $ConfigPath).Path
$logRoot = Join-Path $resolvedRepo "data\state\daily_sync\logs"
New-Item -ItemType Directory -Path $logRoot -Force | Out-Null
$stamp = Get-Date -Format "yyyyMMddTHHmmss"
$logPath = Join-Path $logRoot "daily-sync-$stamp.log"

Push-Location $resolvedRepo
try {
    & $resolvedPython -m tushare_qlib --config $resolvedConfig daily-sync *>> $logPath
    exit $LASTEXITCODE
}
finally {
    Pop-Location
}
