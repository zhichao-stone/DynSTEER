from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Any

from dynsteer.adapter.agentcompass.components import import_agentcompass_component


_OPENHANDS_INSTALL_ROOT = "/tmp/agentcompass/openhands"
_SKILLSBENCH_ROOT = "/tmp/agentcompass/skillsbench"
_SKILLSBENCH_WORKSPACE = f"{_SKILLSBENCH_ROOT}/workspace"
_SKILLSBENCH_SKILL_DIR = f"{_SKILLSBENCH_WORKSPACE}/.agentcompass/skills"
_SKILL_STAGING_DIR = "/tmp/agentcompass_skills_staging/skillsbench"
_PATCHED_BENCHMARKS: set[str] = set()
logger = logging.getLogger(__name__)


def _skip_platform_bootstrap_commands() -> list[str]:
    """host-process 不要求 root 系统包管理器，由运行时工具检查替代。"""
    return []


def _openhands_download_file_command() -> list[str]:
    """返回带网络超时的 OpenHands 依赖下载函数，避免弱网下无限等待。"""
    return [
        "download_file() {",
        "  url=\"$1\"",
        "  dst=\"$2\"",
        "  for attempt in 1 2 3 4 5; do",
        "    if command -v wget >/dev/null 2>&1; then",
        "      if wget --timeout=30 --tries=1 -O \"$dst\" \"$url\"; then",
        "        return 0",
        "      fi",
        "    elif command -v curl >/dev/null 2>&1; then",
        "      if curl -fL --connect-timeout 30 --max-time 600 \"$url\" -o \"$dst\"; then",
        "        return 0",
        "      fi",
        "    else",
        "      echo 'curl or wget is required to install OpenHands runtime' >&2",
        "      return 1",
        "    fi",
        "    rm -f \"$dst\"",
        "    sleep $((attempt * 2))",
        "  done",
        "  echo \"failed to download $url\" >&2",
        "  return 1",
        "}",
    ]


def _patch_openhands_runtime(remote_runner: Any, openhands_module: Any) -> None:
    """为 OpenHands 初始化补充进度日志，并复用已可导入的 runtime。"""
    original_prepare = remote_runner.prepare_openhands_session
    if getattr(original_prepare, "_dynsteer_observability", False):
        return

    async def prepare_with_observability(env: Any, req: Any, plan: Any) -> Any:
        """记录 OpenHands runtime 初始化耗时，便于区分正常安装与网络阻塞。"""
        logger.info("OpenHands runtime 初始化开始 | install_root=%s", remote_runner.RUNTIME_INSTALL_ROOT)
        started_at = time.monotonic()
        try:
            session = await original_prepare(env, req, plan)
        except Exception:
            logger.error("OpenHands runtime 初始化失败 | elapsed_seconds=%.1f", time.monotonic() - started_at)
            raise
        logger.info(
            "OpenHands runtime 初始化完成 | elapsed_seconds=%.1f | runtime_python=%s",
            time.monotonic() - started_at,
            session.get("runtime_python", ""),
        )
        return session

    prepare_with_observability._dynsteer_observability = True
    remote_runner.prepare_openhands_session = prepare_with_observability
    openhands_module.prepare_openhands_session = prepare_with_observability
    remote_runner._download_file_command = _openhands_download_file_command
    remote_runner._install_command = _install_command_with_reuse(remote_runner._install_command)


def _install_command_with_reuse(original_install_command: Any) -> Any:
    """包装安装脚本，已安装且可导入的 OpenHands runtime 不重复重建。"""

    def install_command_with_reuse(plan: Any) -> str:
        command = original_install_command(plan)
        reuse_check = (
            "if [ -x \"$runtime_root/bin/python\" ] && "
            "OPENHANDS_SUPPRESS_BANNER=1 \"$runtime_root/bin/python\" -c "
            "'import openhands.sdk, openhands.tools' >/dev/null 2>&1; then",
            "  echo 'OpenHands runtime is already installed'",
            "  exit 0",
            "fi",
        )
        lines = command.splitlines()
        install_start = next(index for index, line in enumerate(lines) if line.startswith("rm -rf \"$runtime_root\""))
        runtime_absent_commands = [*reuse_check, *lines[install_start:]]
        create_dirs_index = next(
            index for index, line in enumerate(runtime_absent_commands)
            if line.startswith("mkdir -p \"$install_root/bin\"")
        )
        local_assets = [
            "wheelhouse=\"$install_root/wheelhouse\"",
            "if [ -d \"$wheelhouse\" ]; then",
            "  export PIP_NO_INDEX=1",
            "fi",
            "export PIP_FIND_LINKS=\"$wheelhouse\"",
            "if [ ! -x \"$micromamba\" ] && [ -x \"$install_root/micromamba-linux-64\" ]; then",
            "  cp \"$install_root/micromamba-linux-64\" \"$micromamba\"",
            "fi",
        ]
        micromamba_download = "download_file \"$micromamba_url\" \"$micromamba\""
        runtime_absent_commands = [
            line.replace(micromamba_download,
                         f"if [ ! -x \"$micromamba\" ]; then\n  {micromamba_download}\nfi")
            for line in runtime_absent_commands
        ]
        return "\n".join([
            *runtime_absent_commands[:create_dirs_index + 1],
            *local_assets,
            *runtime_absent_commands[create_dirs_index + 1:],
        ])

    install_command_with_reuse._dynsteer_observability = True
    return install_command_with_reuse


def apply_host_process_compat(benchmark: str) -> None:
    """适配 AgentCompass 固定版本中按 root 容器布局硬编码的 host-process 路径。"""
    if benchmark in _PATCHED_BENCHMARKS:
        return
    if benchmark == "skillsbench":
        # 第三方可选依赖边界：只有 AgentCompass 已安装后才允许导入并修补其组件。
        skillsbench_module = import_agentcompass_component("agentcompass.benchmarks.skillsbench.benchmark")
        host_process_module = import_agentcompass_component("agentcompass.environments.host_process")
        remote_runner = import_agentcompass_component("agentcompass.harnesses.openhands.remote_runner")
        openhands_module = import_agentcompass_component("agentcompass.harnesses.openhands.harness")
        HostProcessSession = host_process_module.HostProcessSession
        OpenHandsHarness = openhands_module.OpenHandsHarness

        remote_runner.RUNTIME_INSTALL_ROOT = _OPENHANDS_INSTALL_ROOT
        remote_runner.platform_bootstrap_commands = _skip_platform_bootstrap_commands
        _patch_openhands_runtime(remote_runner, openhands_module)
        _patch_skillsbench(skillsbench_module.SkillsBenchBenchmark, HostProcessSession)
        _patch_openhands_skills(OpenHandsHarness)
        _PATCHED_BENCHMARKS.add(benchmark)
    elif benchmark == "swebench_pro":
        _PATCHED_BENCHMARKS.add(benchmark)
    else:
        raise ValueError(f"AgentCompass host-process benchmark 不受支持: {benchmark}")


def _patch_skillsbench(benchmark_class: type, host_session_class: type) -> None:
    """为 SkillsBench 补充 host-process 的 workspace、skills 和 verifier 路径。"""
    original_build_plan = benchmark_class.build_plan
    original_prepare_task = benchmark_class.prepare_task
    original_evaluate = benchmark_class.evaluate
    original_upload_skills = benchmark_class._upload_skills

    def build_plan(self: Any, task: Any, req: Any, environment: Any) -> Any:
        plan = original_build_plan(self, task, req, environment)
        if environment.id == "host_process":
            plan.workspace_dir = _SKILLSBENCH_WORKSPACE
            plan.eval_result_dir = f"{_SKILLSBENCH_ROOT}/logs/verifier/"
        return plan

    async def prepare_task(self: Any, task: Any, env: Any, req: Any, plan: Any) -> Any:
        prepared = await original_prepare_task(self, task, env, req, plan)
        if req.environment.id == "host_process":
            prepared.input.workspace = _SKILLSBENCH_WORKSPACE
        return prepared

    async def upload_skills(env: Any, skills_local_dir: str) -> None:
        if not isinstance(env, host_session_class):
            return await original_upload_skills(env, skills_local_dir)
        await env.upload_dir(src=skills_local_dir, dst=_SKILL_STAGING_DIR)
        await env.exec([
            "bash",
            "-c",
            (f"mkdir -p {_SKILLSBENCH_SKILL_DIR} && cp -r {_SKILL_STAGING_DIR}/. "
             f"{_SKILLSBENCH_SKILL_DIR}/ && rm -rf {_SKILL_STAGING_DIR}"),
        ])

    async def evaluate(self: Any, task: Any, prepared: Any, result: Any, req: Any, plan: Any, env: Any) -> Any:
        effective_env = env
        if req.environment.id == "host_process" and env is not None:
            effective_env = _HostVerifierSession(env)
        return await original_evaluate(self, task, prepared, result, req, plan, effective_env)

    benchmark_class.build_plan = build_plan
    benchmark_class.prepare_task = prepare_task
    benchmark_class._upload_skills = staticmethod(upload_skills)
    benchmark_class.evaluate = evaluate


def _patch_openhands_skills(harness_class: type) -> None:
    """让 OpenHands 在 host-process 下读取 SkillsBench 的可写 skill 目录。"""
    original_build_plan = harness_class.build_plan

    def build_plan(self: Any, req: Any, environment: Any) -> Any:
        plan = original_build_plan(self, req, environment)
        if req.environment.id == "host_process" and req.benchmark.id == "skillsbench":
            if _SKILLSBENCH_SKILL_DIR not in plan.skill_dirs:
                plan.skill_dirs.append(_SKILLSBENCH_SKILL_DIR)
        return plan

    harness_class.build_plan = build_plan


class _HostVerifierSession:
    """将 SkillsBench verifier 的容器路径重写到当前用户可写目录。"""

    def __init__(self, session: Any):
        self._session = session

    def _path(self, value: str) -> str:
        if value == "/verifier" or value.startswith("/verifier/"):
            return f"{_SKILLSBENCH_ROOT}/verifier{value[len('/verifier'):]}"
        if value.startswith("/usr/local/wrapper"):
            return f"{_SKILLSBENCH_ROOT}/wrapper{value[len('/usr/local/wrapper'):]}"
        if value.startswith("/logs/verifier"):
            return f"{_SKILLSBENCH_ROOT}/logs/verifier{value[len('/logs/verifier'):]}"
        return value

    async def exec(self, command: list[str] | str, **kwargs: Any) -> Any:
        if isinstance(command, str):
            command = self._path(command)
        else:
            command = [self._path(item) for item in command]
        return await self._session.exec(command, **kwargs)

    async def upload_dir(self, src: Path | str, dst: str) -> None:
        destination = self._path(dst)
        await self._session.upload_dir(src=src, dst=destination)
        await self._rewrite_verifier_paths(destination)

    async def write_text(self, path: str, content: str) -> None:
        await self._session.write_text(self._path(path), content)

    async def read_text(self, path: str) -> str:
        return await self._session.read_text(self._path(path))

    async def _rewrite_verifier_paths(self, verifier_dir: str) -> None:
        log_dir = f"{_SKILLSBENCH_ROOT}/logs/verifier"
        await self._session.exec([
            "bash",
            "-c",
            (f"find {verifier_dir} -type f -exec sed -i "
             f"-e 's#/logs/verifier#{log_dir}#g' "
             f"-e 's#/verifier#{verifier_dir}#g' {{}} +"),
        ])

    def __getattr__(self, name: str) -> Any:
        return getattr(self._session, name)
