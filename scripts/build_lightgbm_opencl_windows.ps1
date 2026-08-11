param(
    [string]$LightGBMVersion = "4.7.0",
    [string]$PipelineConfig = "configs/pipeline.yaml",
    [string]$ModelProfile = "configs/model_profiles/lightgbm_gpu_windows.yaml",
    [Parameter(Mandatory = $true)]
    [string]$BoostRoot,
    [Parameter(Mandatory = $true)]
    [string]$BoostLibraryDir,
    [string]$OpenCLIncludeDir = "",
    [string]$OpenCLLibrary = ""
)

$ErrorActionPreference = "Stop"

foreach ($tool in @("cmake", "python")) {
    if (-not (Get-Command $tool -ErrorAction SilentlyContinue)) {
        throw "$tool is required. Install CMake and run this script from a Visual Studio x64 developer shell."
    }
}
foreach ($path in @($BoostRoot, $BoostLibraryDir)) {
    if (-not (Test-Path -LiteralPath $path)) {
        throw "Required build path does not exist: $path"
    }
}

Write-Host "Building LightGBM $LightGBMVersion with the native Windows OpenCL GPU backend..."
$pipArgs = @(
    "-m", "pip", "install", "--no-deps", "--force-reinstall", "--no-binary", "lightgbm",
    "--config-settings=cmake.define.USE_GPU=ON",
    "--config-settings=cmake.define.BOOST_ROOT=$BoostRoot",
    "--config-settings=cmake.define.BOOST_LIBRARYDIR=$BoostLibraryDir",
    "lightgbm==$LightGBMVersion"
)
if ($OpenCLIncludeDir) {
    $pipArgs = $pipArgs[0..($pipArgs.Length - 2)] + `
        "--config-settings=cmake.define.OpenCL_INCLUDE_DIR=$OpenCLIncludeDir" + $pipArgs[-1]
}
if ($OpenCLLibrary) {
    $pipArgs = $pipArgs[0..($pipArgs.Length - 2)] + `
        "--config-settings=cmake.define.OpenCL_LIBRARY=$OpenCLLibrary" + $pipArgs[-1]
}
python @pipArgs
if ($LASTEXITCODE -ne 0) {
    throw "LightGBM OpenCL build failed. Verify Visual Studio C++ Build Tools and an OpenCL SDK/runtime."
}

python -m tushare_qlib --config $PipelineConfig runtime-probe --model-profile $ModelProfile
if ($LASTEXITCODE -ne 0) {
    throw "LightGBM installed, but the one-tree OpenCL runtime probe failed."
}
