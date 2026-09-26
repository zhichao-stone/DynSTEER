# SkillsBench 镜像压缩缓存方案

## 1. 背景

SkillsBench 任务镜像 tag 由任务源码 digest 和 case ID 派生。Docker 本地缓存被清理后，preflight 和 runtime 都会重新构建镜像，导致同一任务在实验过程中反复执行网络下载和长耗时构建。SWE-bench Pro 已有按 case 保存 `.tar.gz` 的机制，但保存、载入逻辑位于 preflight 私有函数中，不能直接复用。

## 2. 修改方案

1. 将 Docker 镜像压缩备份路径、流式保存和载入校验提升为 `dynsteer.utils` 的共享工具。
2. SWE-bench Pro 继续使用 `../docker_images`，SkillsBench 使用 `../skillsbench_docker_images`。
3. SkillsBench 备份名采用 `{safe-tag}-{image-sha256前12位}.tar.gz`，task source digest 变化会生成新备份。
4. `build_task_image()` 按以下顺序执行：本地镜像命中、压缩包载入、Docker 构建；构建成功或本地镜像命中但备份缺失时自动原子保存压缩包。
5. 载入压缩包后校验目标 tag；保存先写临时文件，完成后替换目标文件。

## 3. 验证方案

- 新增共享压缩保存、压缩载入和路径派生的 pytest。
- 运行 pytest、`git diff --check` 和编译检查。
- 当前环境无 Docker daemon，压缩链路的服务器端端到端验证留待真实构建时确认。

## 附录A. 项目中没有把握实现的模块部分

1. Docker daemon 的实际镜像导出、压缩、磁盘空间和载入行为无法在当前无 Docker 的会话中验证。
2. 超大镜像保存可能受备份盘剩余空间限制；失败会直接暴露，不会静默跳过备份。
