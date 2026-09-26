from __future__ import annotations

import logging
import os
from pathlib import Path

from dynsteer.adapter.base import BaseBenchmarkHarness, BenchmarkDefaultResult
from dynsteer.adapter.swebench_pro.adapter import SWEBenchProAdapter, source_root
from dynsteer.adapter.swebench_pro.evaluator import evaluate_patch
from dynsteer.adapter.swebench_pro.runtime import SWEBenchProRuntime, patch_summary, task_prompt
from dynsteer.agent import (
    OpenAIClientConfig,
    ToolAgentResult,
    agent_trajectory_steps,
    run_tool_agent,
    usage_metrics,
)
from dynsteer.harness.model import HarnessAdvanceResult, HarnessRunConfig
from dynsteer.model import Actor, EventType, JsonObject, SWEBenchProSession, TrajectoryStep
from dynsteer.runtime.profile import require_native_execution_profile
from dynsteer.runtime.profile import UnsupportedBenchmarkProfile

logger = logging.getLogger(__name__)


class SWEBenchProHarness(BaseBenchmarkHarness):
    benchmark = "swebench_pro"

    def __init__(self) -> None:
        self.adapter = SWEBenchProAdapter()

    def list_case_ids(self, config: HarnessRunConfig) -> list[str]:
        return self.adapter.list_case_ids(config)

    def start_case(self, config: HarnessRunConfig, case_id: str, raw_output_dir: Path) -> SWEBenchProSession:
        profile = require_native_execution_profile()
        if profile != "docker":
            raise UnsupportedBenchmarkProfile("SWE-bench Pro host 模式只支持适配、replay 与离线汇总")
        self.prepare_config(config)
        sample_data = self.adapter.load_sample(config, case_id)
        runtime: SWEBenchProRuntime | None = None
        probe: JsonObject = {}
        try:
            runtime = SWEBenchProRuntime(
                sample=sample_data,
                raw_output_dir=raw_output_dir,
                image_namespace=str(config.metadata["dockerhub_username"]),
                command_timeout_seconds=int(config.metadata["command_timeout_seconds"]),
                platform=os.environ.get("DYNSTEER_DOCKER_PLATFORM") or None,
            )
            runtime.start()
            probe = runtime.probe
        except Exception as exc:
            probe = dict(runtime.probe) if runtime is not None else {}
            probe.setdefault("status", "infrastructure_failure")
            probe.setdefault("error", str(exc))
            probe["error_type"] = type(exc).__name__
            if runtime is not None:
                runtime.close()
            runtime = None
            logger.exception(
                "swebench_pro_start_failed",
                extra={"事件": "SWE-bench Pro启动失败", "case_id": case_id},
            )
        return SWEBenchProSession(
            case_id=case_id,
            task_id=f"swebench_pro::{case_id}",
            sample=sample_data,
            client_config=OpenAIClientConfig.from_mapping(config.metadata.get("client"), "SWE-bench Pro client"),
            config=config,
            raw_output_dir=raw_output_dir,
            runtime=runtime,
            agent_result=None,
            probe=probe,
            patch=None,
            native_result=None,
            stop_reason=None,
        )

    def advance_case(self, session: object) -> HarnessAdvanceResult:
        session = _session(session)
        if session.finished:
            return HarnessAdvanceResult(steps=[], snapshots=[], continue_running=False)
        if session.runtime is None:
            step = _error_step(
                session.case_id,
                str(session.probe.get("error") or session.probe.get("status")),
                0,
            )
            session.agent_result = ToolAgentResult(
                steps=[step], stop_reason="infrastructure_failure", prompt_tokens=None,
                completion_tokens=None, total_tokens=None, error_type="InfrastructureFailure",
                error_message=str(session.probe),
            )
            session.stop_reason = session.agent_result.stop_reason
            session.finished = True
            return HarnessAdvanceResult(steps=[step], snapshots=[], continue_running=False)
        session.agent_result = run_tool_agent(
            task_prompt=task_prompt(session.sample),
            system_prompt=_system_prompt(),
            client_config=session.client_config,
            executor=session.runtime,
            max_tool_calls=int(session.config.metadata["max_tool_calls"]),
            temperature=float(session.config.metadata["model_temperature"]),
        )
        try:
            session.patch = session.runtime.collect_patch()
            (session.raw_output_dir / "agent.patch").write_text(session.patch, encoding="utf-8")
        except Exception as exc:
            logger.exception(
                "swebench_pro_patch_collection_failed",
                extra={"事件": "SWE-bench Pro patch收集失败", "case_id": session.case_id},
            )
            session.agent_result.steps.append(
                _error_step(session.case_id, str(exc), len(session.agent_result.steps))
            )
        session.stop_reason = session.agent_result.stop_reason
        session.finished = True
        return HarnessAdvanceResult(
            steps=agent_trajectory_steps(task_id=session.task_id, agent_result=session.agent_result),
            snapshots=[],
            continue_running=False,
        )

    def metrics_from_session(self, session: object) -> JsonObject:
        session = _session(session)
        if session.agent_result is None:
            return {}
        return {"agent_usage": usage_metrics(session.agent_result)}

    def initial_state_from_session(self, session: object) -> JsonObject | None:
        return _session(session).probe

    def final_state_from_session(self, session: object) -> JsonObject | None:
        session = _session(session)
        if session.patch is None:
            return None
        return {"patch": patch_summary(session.patch)}

    def raw_summary_from_session(self, session: object) -> JsonObject:
        session = _session(session)
        native = session.native_result.summary if session.native_result is not None else {}
        return {
            "case_id": session.case_id,
            "image_probe": session.probe,
            "patch": patch_summary(session.patch or ""),
            "agent_stop_reason": session.stop_reason,
            "native_evaluation": native,
        }

    def default_result_from_session(self, session: object) -> BenchmarkDefaultResult:
        session = _session(session)
        if session.runtime is None or session.patch is None:
            failure_type = _infrastructure_failure_type(session.probe)
            return BenchmarkDefaultResult(
                score=None,
                raw={
                    "native_evaluation_available": False,
                    "failure_type": failure_type,
                    "error": session.probe.get("error") or session.probe.get("status"),
                },
                metrics={},
            )
        session.native_result = evaluate_patch(
            source_root=source_root(session.config),
            data_root=session.config.data_root,
            instance_id=session.case_id,
            patch=session.patch,
            output_dir=session.raw_output_dir,
            image_namespace=str(session.config.metadata["dockerhub_username"]),
            execution_profile=require_native_execution_profile(),
            docker_platform=os.environ.get("DYNSTEER_DOCKER_PLATFORM"),
        )
        available = session.native_result.available
        return BenchmarkDefaultResult(
            score=session.native_result.score,
            raw={
                "score_source": "source_direct_swebench_pro",
                "resolved": session.native_result.resolved,
                "native_evaluation_available": available,
                "failure_type": session.native_result.failure_type,
            },
            metrics={"returncode": session.native_result.returncode},
        )

    def teardown_case(self, session: object) -> None:
        current = _session(session)
        if current.runtime is not None:
            current.runtime.close()


def _system_prompt() -> str:
    return (
        "You are a software engineering agent working in a persistent Linux repository. "
        "Call at most one bash command per turn. Command stdout, stderr, and exit code are returned verbatim. "
        "Do not corrupt the git baseline. When the task is complete, return a short summary without calling bash."
    )


def _error_step(case_id: str, message: str, index: int) -> TrajectoryStep:
    return TrajectoryStep(
        step_id=f"error-{case_id}",
        index=index,
        event_type=EventType.ERROR,
        actor=Actor.AGENT,
        recipient=Actor.EVALUATOR,
        content=message,
        raw={"failure_type": "infrastructure_failure"},
    )


def _infrastructure_failure_type(probe: JsonObject) -> str:
    text = " ".join(str(item) for item in (probe.get("error"), probe.get("error_type")) if item)
    if "本地镜像缓存缺失" in text or "本地镜像缓存不可用" in text or "Docker镜像拉取失败" in text or "ImageNotFound" in text or "pull" in text.lower():
        return "docker_image_pull_failed"
    return "infrastructure_failure"


def _session(value: object) -> SWEBenchProSession:
    if not isinstance(value, SWEBenchProSession):
        raise TypeError("session 必须是 SWEBenchProSession")
    return value
