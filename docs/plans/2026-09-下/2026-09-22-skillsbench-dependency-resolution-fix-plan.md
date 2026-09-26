# SkillsBench 依赖解析修复方案

## 1. 背景

`skillsbench_pilot`、`skillsbench_main` 和 `skillsbench_ablation` 共涉及 42 个去重后的 SkillsBench 任务。对任务镜像的 Python 安装命令和依赖组合检查后，确认以下任务存在依赖漂移或不可共同解析的问题：

1. `quantum-numerical-simulation`：多次独立安装导致 `matplotlib` 将 NumPy 升到 2.x，同时 QuTiP 4.7.6 要求 SciPy 低于 1.13。
2. `dynamic-object-aware-egomotion`：先固定 NumPy 1.26.4，再单独安装 OpenCV 4.12；OpenCV 4.12 要求 NumPy 2.x 且低于 2.3。
3. `video-silence-remover`：先安装 pytest 7.4.0，再安装 pytest 9.0.2，两个固定版本互相覆盖。
4. `sales-pivot-analysis`：镜像构建已在 `pip install` 阶段失败，同时缺少工具链升级和显式 NumPy 约束。

## 2. 修改方案

- 将存在依赖联动的 Python 包合并到同一个 `pip install` 命令，让解析器统一选择版本。
- `quantum-numerical-simulation` 使用 `numpy==1.26.4`、`scipy==1.12.0`、`matplotlib==3.9.4`、`qutip==4.7.6`。
- `dynamic-object-aware-egomotion` 使用 OpenCV 要求区间内的 `numpy==2.2.6`。
- `video-silence-remover` 只保留运行和验证一致的 `pytest==9.0.2`。
- `sales-pivot-analysis` 固定 `numpy==2.2.6` 与 `pytz==2025.2`，减少当前索引漂移带来的不确定性。
- 不修改任务语义、verifier 和 oracle 逻辑。

## 3. 验证方案

- 使用 `uv pip compile --python-platform x86_64-unknown-linux-gnu --python-version 3.12` 验证修改后的依赖组合可解析。
- 使用 `git diff --check` 检查补丁无空白错误。
- 因当前本地环境没有 Docker daemon，不执行任务镜像构建。

## 附录A. 项目中没有把握实现的模块部分

1. `sales-pivot-analysis` 的完整失败原因需要依赖服务器上的 `runs/skillsbench/preflight-images/skillsbench_pilot/sales-pivot-analysis/docker-build.log` 才能最终确认；本方案先消除依赖漂移和工具链版本不确定性。
2. `multilingual-video-dubbing` 存在多次独立安装，但组合解析结果显示各包最终仍会选择 NumPy 1.26.4，且 `pyopenjtalk` 必须依赖镜像内 CMake 环境构建，本次不做语义无关的大幅重排。
