from __future__ import annotations

import argparse
import json
from dataclasses import replace
from pathlib import Path
from typing import Optional

from dynsteer.adapter.generic import load_milestone_graph, load_task_case, load_trajectory
from dynsteer.adapter.toolsandbox import load_toolsandbox_experiment
from dynsteer.evaluate import DynSTEEREvaluator
from dynsteer.judges.llm import LLMJudge
from dynsteer.log import configure_logger, get_log_buffer


def _parse_args(argv: Optional[list[str]]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="运行 DynSTEER 阶段式动态轨迹评估实验")
    # benchmark 模式相关参数
    parser.add_argument("--benchmark", default=None, help="benchmark harness 名称，例如 toolsandbox")
    parser.add_argument("--data-root", default=None, help="benchmark 静态配置与 manifest 目录")
    # 离线评估模式相关参数
    parser.add_argument("--input", default=None, help="离线实验输入 JSON 文件路径")
    parser.add_argument("--input-format", default="generic", choices=["generic", "toolsandbox"])
    # 输出路径参数
    parser.add_argument("--runs-dir", default="runs", help="benchmark 中间产物与原生输出目录")
    parser.add_argument("--results-dir", default="results", help="最终 DynSTEER 评估结果目录")
    parser.add_argument("--run-name", default=None)
    parser.add_argument("--log-dir", default="logs")
    return parser.parse_args(argv)


def main(argv: Optional[list[str]] = None) -> int:
    """主实验入口：读取输入、执行轨迹评估、写出报告并返回状态码。

    Args:
        argv: 可选命令行参数，测试时可直接传入。

    Returns:
        0 表示成功，1 表示输入解析错误，2 表示评估执行错误。
    """
    args = _parse_args(argv)
    logger = configure_logger(args.log_dir)
    runs_dir = Path(args.runs_dir)
    results_dir = Path(args.results_dir)

    try:
        if args.benchmark is not None:
            if args.data_root is None:
                data_root: Path = Path(__file__).parent / "data" / args.benchmark
                if not data_root.exists():
                    raise ValueError("运行 benchmark harness 时 需要提供可靠的data-root，通过--data-root提供或者data/{benchmark}")
            else:
                data_root = Path(args.data_root)
            if not data_root.exists():
                raise ValueError("运行 benchmark harness 时 需要提供可靠的data-root，通过--data-root提供或者data/{benchmark}")
            
            from dynsteer.adapter.registry import get_harness
            from dynsteer.harness.config import load_harness_run_configs
            from dynsteer.harness.runner import run_harness_cases

            evaluator = DynSTEEREvaluator(llm_judge=LLMJudge.from_env())
            configs = load_harness_run_configs(
                benchmark=str(args.benchmark),
                data_root=data_root,
                runs_dir=runs_dir,
                results_dir=results_dir,
            )
            harness = get_harness(str(args.benchmark))
            outputs = []
            for config in configs:
                outputs.extend(run_harness_cases(config=config, harness=harness, evaluator=evaluator))
            for output in outputs:
                print(str(output.report_path))
            return 0

        if args.input is None:
            raise ValueError("离线评估模式必须提供 --input")
        input_path = Path(args.input)
        run_name = args.run_name or input_path.stem
        run_dir = results_dir / run_name
        if not input_path.exists():
            logger.error("实验输入文件不存在", extra={"input_path": str(input_path)})
            return 1
        data = json.loads(input_path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError("实验输入必须是 JSON 对象")
        if args.input_format == "generic":
            task_case = load_task_case(data["task"])
            trajectory = load_trajectory(data["trajectory"])
            if data.get("milestone_graph") is not None:
                task_case = replace(
                    task_case,
                    milestone_graph=load_milestone_graph(data["milestone_graph"]),
                )
        else:
            task_case, trajectory = load_toolsandbox_experiment(data)

        logger.info("开始执行轨迹评估", extra={"run_id": trajectory.run_id, "task_id": task_case.task_id})
        evaluator = DynSTEEREvaluator(llm_judge=LLMJudge.from_env())
        report = evaluator.evaluate_trajectory(task_case, trajectory)
        run_dir.mkdir(parents=True, exist_ok=True)
        (run_dir / "report.json").write_text(
            json.dumps(report.to_dict(), ensure_ascii=False, indent=4),
            encoding="utf-8",
        )
        (run_dir / "summary.json").write_text(
            json.dumps(report.to_summary_dict(), ensure_ascii=False, indent=4),
            encoding="utf-8",
        )
        logger.info("轨迹评估完成", extra={"report_path": str(run_dir / "report.json")})
        (run_dir / "logs.json").write_text(
            json.dumps(get_log_buffer(), ensure_ascii=False, indent=4),
            encoding="utf-8",
        )
        print(str(run_dir / "report.json"))
        return 0
    except (KeyError, ValueError, json.JSONDecodeError) as exc:
        logger.exception("实验输入解析失败", extra={"error": str(exc)})
        return 1
    except Exception as exc:
        logger.exception("实验执行失败", extra={"error": str(exc)})
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
