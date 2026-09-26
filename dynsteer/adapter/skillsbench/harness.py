from __future__ import annotations

import json
import hashlib
import os
from pathlib import Path

from dynsteer.adapter.base import BaseBenchmarkHarness, BenchmarkDefaultResult
from dynsteer.adapter.skillsbench.adapter import SkillsBenchAdapter
from dynsteer.adapter.skillsbench.runtime import SkillsBenchRuntime
from dynsteer.adapter.utils import resolve_source_root
from dynsteer.agent import (
    OpenAIClientConfig,
    ToolAgentResult,
    ToolExecutionResult,
    agent_trajectory_steps,
    run_tool_agent,
    usage_metrics,
)
from dynsteer.harness.model import HarnessAdvanceResult, HarnessRunConfig
from dynsteer.model import Actor, EventType, JsonObject, SkillsBenchSession, TrajectoryStep
from dynsteer.runtime.profile import require_native_execution_profile
from dynsteer.runtime.profile import UnsupportedBenchmarkProfile
from dynsteer.utils import json_safe


class SkillsBenchHarness(BaseBenchmarkHarness):
    benchmark = "skillsbench"

    def __init__(self) -> None:
        self.adapter = SkillsBenchAdapter()

    def list_case_ids(self, config: HarnessRunConfig) -> list[str]:
        return self.adapter.list_case_ids(config)

    def start_case(self, config: HarnessRunConfig, case_id: str, raw_output_dir: Path) -> SkillsBenchSession:
        if require_native_execution_profile() != "docker":
            raise UnsupportedBenchmarkProfile("SkillsBench host 模式只支持适配、replay 与离线汇总")
        self.prepare_config(config)
        task = self.adapter.load_task(config, case_id)
        runtime: SkillsBenchRuntime | None = None
        start_state: JsonObject = {}
        try:
            runtime = SkillsBenchRuntime(
                task=task,
                source_task_dir=(
                    resolve_source_root(config.data_root, Path(__file__).resolve().parents[3], "skillsbench")
                    / "tasks"
                    / case_id
                ),
                mirror_dir=raw_output_dir / "task-mirror",
                condition=str(config.metadata.get("condition")),
                command_timeout_seconds=int(config.metadata["command_timeout_seconds"]),
                platform=os.environ.get("DYNSTEER_DOCKER_PLATFORM") or None,
            )
            runtime.start()
            start_state = runtime.start_state
            (raw_output_dir / "container_metadata.json").write_text(
                json.dumps(runtime.metadata(), ensure_ascii=False, indent=4),
                encoding="utf-8",
            )
        except Exception as exc:
            start_state = dict(runtime.start_state) if runtime is not None else {}
            start_state.setdefault("status", "infrastructure_failure")
            start_state.setdefault("error", str(exc))
            start_state["error_type"] = type(exc).__name__
            if runtime is not None:
                runtime.close()
            runtime = None
        return SkillsBenchSession(
            case_id=case_id,
            task_id=f"skillsbench::{case_id}",
            task=task,
            client_config=OpenAIClientConfig.from_mapping(config.metadata.get("client"), "SkillsBench client"),
            config=config,
            raw_output_dir=raw_output_dir,
            start_state=start_state,
            runtime=runtime,
            agent_result=None,
            verifier_result=None,
            reward=None,
            stop_reason=None,
        )

    def advance_case(self, session: object) -> HarnessAdvanceResult:
        current = _session(session)
        if current.finished:
            return HarnessAdvanceResult(steps=[], snapshots=[], continue_running=False)
        if current.runtime is None:
            error = _error_step(str(current.start_state.get("error") or current.start_state.get("status")))
            current.agent_result = ToolAgentResult(
                steps=[error], stop_reason="infrastructure_failure", prompt_tokens=None,
                completion_tokens=None, total_tokens=None, error_type="InfrastructureFailure",
                error_message=str(current.start_state),
            )
            current.stop_reason = "infrastructure_failure"
            current.finished = True
            return HarnessAdvanceResult(steps=[error], snapshots=[], continue_running=False)
        current.agent_result = run_tool_agent(
            task_prompt=current.runtime.task_prompt(),
            system_prompt=current.runtime.system_prompt(),
            client_config=current.client_config,
            executor=current.runtime,
            max_tool_calls=int(current.config.metadata["max_tool_calls"]),
            temperature=float(current.config.metadata["model_temperature"]),
        )
        _write_agent_artifacts(current)
        current.verifier_result = current.runtime.run_verifier()
        current.reward = current.runtime.reward()
        current.stop_reason = current.agent_result.stop_reason
        current.finished = True
        return HarnessAdvanceResult(
            steps=agent_trajectory_steps(task_id=current.task_id, agent_result=current.agent_result),
            snapshots=[],
            continue_running=False,
        )

    def metrics_from_session(self, session: object) -> JsonObject:
        current = _session(session)
        if current.agent_result is None:
            return {}
        return {"agent_usage": usage_metrics(current.agent_result)}

    def initial_state_from_session(self, session: object) -> JsonObject | None:
        return _session(session).start_state

    def final_state_from_session(self, session: object) -> JsonObject | None:
        current = _session(session)
        if current.runtime is None:
            return None
        return current.runtime.metadata() | {
            "verifier": _verifier_summary(current.verifier_result, current.reward) if current.verifier_result is not None else {},
            "agent_stop_reason": current.stop_reason,
        }

    def raw_summary_from_session(self, session: object) -> JsonObject:
        current = _session(session)
        return {
            "case_id": current.case_id,
            "start_state": current.start_state,
            "runtime": current.runtime.metadata() if current.runtime is not None else {},
            "agent_stop_reason": current.stop_reason,
            "verifier": _verifier_summary(current.verifier_result, current.reward) if current.verifier_result is not None else {},
        }

    def default_result_from_session(self, session: object) -> BenchmarkDefaultResult:
        current = _session(session)
        available = current.verifier_result is not None and current.reward is not None
        failure_type = _infrastructure_failure_type(current.start_state) if current.runtime is None else None
        return BenchmarkDefaultResult(
            score=current.reward,
            raw={
                "score_source": "source_direct_skillsbench",
                "reward": current.reward,
                "native_evaluation_available": available,
                **({"failure_type": failure_type, "error": current.start_state.get("error")} if failure_type else {}),
            },
            metrics={"verifier_exit_code": current.verifier_result.exit_code if current.verifier_result else None},
        )

    def teardown_case(self, session: object) -> None:
        current = _session(session)
        if current.runtime is not None:
            current.runtime.close()


def _error_step(message: str) -> TrajectoryStep:
    return TrajectoryStep(
        step_id="error-0", index=0, event_type=EventType.ERROR, actor=Actor.AGENT,
        recipient=Actor.EVALUATOR, content=message, raw={"failure_type": "infrastructure_failure"},
    )


def _infrastructure_failure_type(start_state: JsonObject) -> str:
    text = " ".join(str(item) for item in (start_state.get("error"), start_state.get("error_type")) if item)
    if "BuildError" in text or "Docker镜像构建失败" in text or "build" in text.lower():
        return "docker_image_build_failed"
    return "infrastructure_failure"


def _verifier_summary(result: ToolExecutionResult, reward: float | None) -> JsonObject:
    return {
        "exit_code": result.exit_code,
        "reward": reward,
        "timed_out": result.timed_out,
        "stdout_sha256": hashlib.sha256(result.stdout.encode("utf-8", errors="replace")).hexdigest(),
        "stdout_length": len(result.stdout),
        "stderr_sha256": hashlib.sha256(result.stderr.encode("utf-8", errors="replace")).hexdigest(),
        "stderr_length": len(result.stderr),
    }


def _write_agent_artifacts(session: SkillsBenchSession) -> None:
    """写出共享 Agent 的原始轨迹与摘要，敏感长字符串统一脱敏。"""
    if session.agent_result is None:
        return
    raw_trajectory = session.raw_output_dir / "raw_trajectory.jsonl"
    raw_trajectory.write_text(
        "".join(json.dumps(json_safe(step.__dict__), ensure_ascii=False) + "\n" for step in session.agent_result.steps),
        encoding="utf-8",
    )
    (session.raw_output_dir / "agent_summary.json").write_text(
        json.dumps({
            "stop_reason": session.agent_result.stop_reason,
            "error_type": session.agent_result.error_type,
            "error_message": session.agent_result.error_message,
            "usage": usage_metrics(session.agent_result),
        }, ensure_ascii=False, indent=4),
        encoding="utf-8",
    )


def _session(value: object) -> SkillsBenchSession:
    if not isinstance(value, SkillsBenchSession):
        raise TypeError("session 必须是 SkillsBenchSession")
    return value
