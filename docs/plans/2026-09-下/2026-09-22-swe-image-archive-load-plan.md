# SWE-bench-Pro 镜像压缩缓存方案

## 目标

为 SWE-bench-Pro 的每个任务镜像维护独立压缩备份。preflight 准备镜像时优先使用本地 Docker 缓存；镜像被清理后优先从 `../docker_images` 的对应压缩包载入；只有本地缓存和压缩包都不存在时才从远端拉取。拉取成功后自动保存压缩包，避免同一镜像因外部清理而重复下载。

## 修改方案

### 1. preflight 自动缓存镜像

修改 `scripts/preflight_experiment.py` 的 `prepare_swe_images`：

1. 用 case ID 和镜像引用 SHA-256 前缀生成稳定的备份文件名，存放到 `project_root/../docker_images`。
2. Docker 本地镜像命中时，若对应压缩包缺失则通过 `image.save(named=True)` 原子写入 `.tar.gz`。
3. Docker 本地镜像缺失时，优先用 Docker API 从对应压缩包 `load_image`，载入后校验目标 tag。
4. 压缩包也不存在时才拉取远端镜像，拉取成功后自动保存压缩包。
5. 备份先写入临时文件，成功后替换目标文件，避免中断产生损坏的 `.tar.gz`。

启动脚本不再维护单独的大包预载流程；镜像恢复由 preflight 按 case 执行。

## 验证

1. 增加针对缓存命中补备份、压缩包恢复和远端拉取后自动备份的 pytest。
2. 运行 preflight 相关测试。
3. 服务器上清理一个已备份镜像后重跑实验，确认日志显示压缩包载入完成且不再访问远端。

## 附录A. 项目中没有把握实现的模块部分

Docker daemon 的镜像压缩、载入和磁盘 I/O 无法在当前无 Docker 的 Windows 会话中端到端验证；单元测试只覆盖保存、载入和回退流程。服务器首次运行时需要确认 `../docker_images` 有足够磁盘空间，并通过 `docker image inspect` 抽查镜像架构仍为 `linux/amd64`。
