---
status: ACTIVE
owner: operations
applies_to_commit: 8692afefe1f6cc82ab1f276fca788888f9f30f3e
last_verified: 2026-08-28
---

# Windows 上的 LightGBM GPU

Windows 不要求迁移到 Linux 才能使用 LightGBM 加速，但后端名称必须区分清楚：

- `device_type=cuda` 是 LightGBM 的 CUDA 后端，只支持 Linux 构建。
- `device_type=gpu` 是 OpenCL 后端，可在 Windows 上使用 NVIDIA、AMD 或 Intel 的 OpenCL 运行时。
- 官方 PyPI wheel 通常不含 GPU 构建选项；安装成功不代表 `gpu` 后端可用。本项目用一次真实的一棵树训练来探测，而不是只检查显卡或包版本。

## 前置条件

安装 Visual Studio 2022 C++ Build Tools、CMake、与 MSVC 版本匹配的 64 位 Boost binaries，以及显卡厂商提供的 OpenCL headers/library/runtime。NVIDIA 的运行时随驱动提供，但源码编译通常仍需 CUDA Toolkit 中的 `include/CL` 与 `OpenCL.lib`。可先运行 `clinfo` 确认平台和设备编号。

## 构建与验证

在 Visual Studio x64 Developer PowerShell 中执行。项目命令使用仓库本地解释器：`$RepoPython = '.\.venv\Scripts\python.exe'`。

```powershell
powershell -ExecutionPolicy Bypass -File scripts/build_lightgbm_opencl_windows.ps1 `
  -BoostRoot C:\local\boost_1_87_0 `
  -BoostLibraryDir C:\local\boost_1_87_0\lib64-msvc-14.3 `
  -OpenCLIncludeDir "C:\Program Files\NVIDIA GPU Computing Toolkit\CUDA\v13.0\include" `
  -OpenCLLibrary "C:\Program Files\NVIDIA GPU Computing Toolkit\CUDA\v13.0\lib\x64\OpenCL.lib"
```

路径仅为示例，应替换成机器上的实际版本。脚本先验证 Boost 路径，再从源码重装项目 pin 的
LightGBM 4.6.0、启用 `USE_GPU=ON`，随后调用：

```powershell
& $RepoPython -m qlib_platform --config configs/pipeline.standalone.yaml runtime-probe `
  --model-profile configs/model_profiles/lightgbm_gpu_windows.yaml
```

只有输出中的 `resolvedDevice` 为 `gpu:0` 才算成功。`gpu_platform_id` 与 `device_index` 来自 `clinfo`；多 OpenCL 平台机器需要修改 profile。

## 使用方式

```powershell
& $RepoPython -m qlib_platform --config configs/pipeline.standalone.yaml train-select `
  --model-profile configs/model_profiles/lightgbm_gpu_windows.yaml
```

Windows 自动 profile 会优先探测 OpenCL，失败后回落 CPU；显式 `device: gpu` profile 则失败即停止，避免误以为训练正在使用显卡。Linux 自动 profile 仍优先探测 CUDA。

GPU 只加速 LightGBM boosting。Alpha158 表达式、Pandas processors、回测和 artifact IO 仍主要运行在 CPU，因此应同时启用 PIT universe、`qlib_kernels` 和 Feature Store。以 `train_seconds / orchestrationWallSeconds` 判断 GPU 的实际收益；训练占比低于约 20% 时，继续优化数据路径通常更划算。

官方构建说明：<https://lightgbm.readthedocs.io/en/latest/Installation-Guide.html>
