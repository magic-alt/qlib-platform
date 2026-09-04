[CmdletBinding()]
param(
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]]$Arguments
)

$ErrorActionPreference = 'Stop'
$RepoRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..')).Path
$RepoPython = Join-Path $RepoRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $RepoPython)) {
    throw "Repository-local interpreter is missing: $RepoPython"
}
Set-Location -LiteralPath $RepoRoot
& $RepoPython -m qlib_platform.research.workflow.quickstart @Arguments
exit $LASTEXITCODE
