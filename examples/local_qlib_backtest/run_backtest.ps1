[CmdletBinding(DefaultParameterSetName = 'NamedModel')]
param(
    [Parameter(ParameterSetName = 'NamedModel')]
    [ValidateSet('lightgbm', 'ridge', 'custom_ridge')]
    [string]$Model = 'lightgbm',

    [Parameter(Mandatory = $true, ParameterSetName = 'Workflow')]
    [string]$Workflow,

    [string]$DatasetRef = 'research-current',
    [string]$ExperimentName = '',
    [string]$RecorderUri = 'mlruns/examples_local_backtest'
)

$ErrorActionPreference = 'Stop'
$RepoRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..\..')).Path
Set-Location -LiteralPath $RepoRoot

$RepoPython = Join-Path $RepoRoot '.venv\Scripts\python.exe'
$Qrun = Join-Path $RepoRoot '.venv\Scripts\qrun.exe'
if (-not (Test-Path -LiteralPath $RepoPython)) {
    throw 'Repository-local interpreter is missing: .\.venv\Scripts\python.exe'
}
if (-not (Test-Path -LiteralPath $Qrun)) {
    throw 'Repository-local qrun is missing: .\.venv\Scripts\qrun.exe'
}

$WorkflowByModel = @{
    lightgbm    = 'examples\local_qlib_backtest\workflow_lightgbm.yaml'
    ridge       = 'examples\local_qlib_backtest\workflow_ridge.yaml'
    custom_ridge = 'examples\local_qlib_backtest\workflow_custom_ridge.yaml'
}

if ($PSCmdlet.ParameterSetName -eq 'NamedModel') {
    $Workflow = $WorkflowByModel[$Model]
}
$WorkflowPath = (Resolve-Path -LiteralPath $Workflow).Path

if ([string]::IsNullOrWhiteSpace($ExperimentName)) {
    $ExperimentName = 'local_alpha158_' + [IO.Path]::GetFileNameWithoutExtension($WorkflowPath)
}

# These known, non-secret local placeholders only allow the development config to load.
# dataset-resolve returns the immutable path that is bound to qrun below.
$env:QLIB_REPO = '.'
$env:QLIB_DATA_URI = 'data/qlib'
$ResolveOutput = & $RepoPython -m tushare_qlib `
    --config configs\pipeline_tushare_dev.yaml dataset-resolve $DatasetRef
if ($LASTEXITCODE -ne 0) {
    throw "dataset-resolve failed for reference '$DatasetRef'"
}
$Resolved = $ResolveOutput | ConvertFrom-Json
if (-not $Resolved.versionId -or -not $Resolved.path) {
    throw 'dataset-resolve returned an incomplete identity'
}

& $RepoPython -m tushare_qlib `
    --config configs\pipeline_tushare_dev.yaml dataset-verify $DatasetRef
if ($LASTEXITCODE -ne 0) {
    throw "dataset-verify failed for reference '$DatasetRef'"
}

$ImmutableDataPath = (Resolve-Path -LiteralPath $Resolved.path).Path
$env:QLIB_DATA_URI = $ImmutableDataPath
$env:MLFLOW_ALLOW_FILE_STORE = 'true'

& $RepoPython -m tushare_qlib `
    --config configs\pipeline_tushare_dev.yaml `
    validate-qrun-contract --workflow $WorkflowPath
if ($LASTEXITCODE -ne 0) {
    throw 'validate-qrun-contract failed'
}

Write-Host "Dataset version: $($Resolved.versionId)"
Write-Host "Workflow: $WorkflowPath"
Write-Host "Experiment: $ExperimentName"

& $Qrun $WorkflowPath -e $ExperimentName -u $RecorderUri
if ($LASTEXITCODE -ne 0) {
    throw "qrun failed with exit code $LASTEXITCODE"
}
