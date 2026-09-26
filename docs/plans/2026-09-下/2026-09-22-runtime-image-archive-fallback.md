# SWE-bench Pro 运行时镜像压缩包回退方案

## 背景

Preflight 已将 SWE-bench Pro 镜像保存到项目上级 `docker_images` 目录，但 `LocalDockerExecutor` 启动时只检查本地镜像，缺失后直接远端拉取。远端 pull 失败或本地镜像被清理时，任务会误报为镜像不存在。

## 修改方案

1. `DockerRunSpec` 增加 `image_archive` 字段，表示可选的本地镜像压缩包。
2. `SWEBenchProRuntime` 按 preflight 相同规则生成交互镜像压缩包路径：
   - 压缩包根目录：`Path(__file__).resolve().parents[3].parent / "docker_images"`
   - 业务标签：`sample.instance_id`
   - 镜像引用：现有 `swe_image_uri()` 结果
3. `LocalDockerExecutor.start()` 每次启动都重新检查镜像：
   - 本地存在：直接创建容器。
   - 本地缺失且压缩包存在：调用 `load_image_archive()` 加载并校验目标 tag。
   - 本地缺失且压缩包不存在：使用底层 Docker API 拉取，遇到流式 `error` 事件立即抛出真实错误。
4. `set_working_dir()` 保留 `image_archive`，避免后续重建 spec 时丢失回退路径。

## SkillsBench 检查结论

SkillsBench 每次运行时都会调用 `build_task_image()`。该函数已经按相同顺序检查本地镜像、从 `../skillsbench_docker_images` 加载压缩包、必要时才构建，因此不需要重复把压缩包传给 `LocalDockerExecutor`。新增测试固定“压缩包存在时不重新构建”的行为。

## 验证

- 新增运行时测试覆盖“本地缺失时优先加载压缩包”。
- 新增测试验证压缩包路径与 preflight 生成的规则一致。
- 运行 `tests/test_pilot_runtime_failures.py` 与 `tests/test_docker_image_archives.py`。

## 附录A. 项目中没有把握实现的模块部分

本次没有不确定模块。唯一外部依赖是 Docker daemon 在 `load_image` 后能通过目标 tag 检索镜像；该行为已由既有 `load_image_archive()` 校验封装保证。
