"""Run BU Bench V2 with the weighted findings judge by default.

uv run python run_eval.py --tasks 5
uv run python run_eval.py --browser local_headless
uv run python run_eval.py --benchmark BU_Bench_V1  # legacy binary judge
"""

import argparse
import asyncio
import base64
import hashlib
import json
import logging
import os
import traceback
from datetime import UTC, datetime
from importlib.metadata import version
from pathlib import Path

import certifi

os.environ.setdefault("SSL_CERT_FILE", certifi.where())
os.environ["BROWSER_USE_SETUP_LOGGING"] = "false"  # Before importing browser_use
logging.basicConfig(level=logging.CRITICAL)

# 仍然导入 browser-use 的 Judge 和配置模块，但不再实例化其原生 Agent
from dotenv import load_dotenv

from browsers import PROVIDERS, get_provider
from evaluation import (
    BENCHMARKS,
    DEFAULT_BENCHMARK,
    create_judge,
    judge_config,
    judge_trace,
    load_tasks,
    validate_findings_task,
)

load_dotenv()

MAX_CONCURRENT = 3
TASK_TIMEOUT = 3600
AGENT_FRAMEWORK_NAME = "AsideCLI"
AGENT_FRAMEWORK_VERSION = "1.0.0"
MODEL_NAME = "bu-2-0"


def select_shard(
    tasks: list[dict], shard_index: int | None, shard_count: int | None
) -> list[dict]:
    """Select one contiguous shard while preserving the dataset's task order."""
    if shard_index is None or shard_count is None:
        return tasks
    start = len(tasks) * shard_index // shard_count
    end = len(tasks) * (shard_index + 1) // shard_count
    return tasks[start:end]


def select_tasks(
    tasks: list[dict], task_ids: list[str] | None = None, limit: int | None = None
):
    if task_ids:
        by_id = {task["task_id"]: task for task in tasks}
        unknown = set(task_ids) - by_id.keys()
        if unknown or len(task_ids) != len(set(task_ids)):
            raise ValueError("Task IDs must be unique and present in the dataset")
        tasks = [by_id[task_id] for task_id in task_ids]
    return tasks[:limit] if limit is not None else tasks


async def run_aside_cli(task_prompt: str, timeout: int = TASK_TIMEOUT) -> tuple[str, int, float]:
    """
    通过子进程调用本地安装的 aside cli 执行单条 benchmark 任务。
    返回: (final_result_text, steps_count, duration_seconds)
    """
    start_time = asyncio.get_event_loop().time()
    
    # 构造 aside 命令行参数（可根据你的 aside cli 实际支持的参数进行微调）
    cmd = [
        "aside",
        "run",
        task_prompt,
    ]

    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )

    try:
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
        duration = asyncio.get_event_loop().time() - start_time
        return "Error: Aside CLI execution timed out.", 0, duration

    duration = asyncio.get_event_loop().time() - start_time
    output = stdout.decode("utf-8", errors="ignore").strip()
    
    if proc.returncode != 0:
        err_msg = stderr.decode("utf-8", errors="ignore").strip()
        print(f"[Aside CLI Error] {err_msg}")
        return output or f"Error: {err_msg}", 1, duration

    return output or "Aside CLI finished without output.", 1, duration


def save_task(run_data_dir: Path | None, task_id: str, payload: dict) -> None:
    if run_data_dir is not None:
        run_data_dir.mkdir(parents=True, exist_ok=True)
        (run_data_dir / f"{task_id}.json").write_text(json.dumps(payload, indent=2))


async def run_task(
    task: dict,
    semaphore: asyncio.Semaphore,
    browser_provider=None,
    llm=None,
    run_data_dir: Path | None = None,
    *,
    benchmark: str = DEFAULT_BENCHMARK,
    judge_llm=None,
    task_timeout: int = TASK_TIMEOUT,
    max_steps: int = 100,
) -> dict:
    """Execute and judge one task via Aside CLI."""
    async with semaphore:
        task_id = task.get("task_id", "unknown")
        phase = "setup"
        result = {
            "task_id": task_id,
            "score": None,
            "raw_score": None,
            "steps": 0,
            "duration": 0,
            "cost": 0,
        }
        artifact = {"benchmark": benchmark}
        try:
            if benchmark == DEFAULT_BENCHMARK:
                validate_findings_task(task)
            judge_llm = judge_llm or create_judge(benchmark)
            artifact["judge_config"] = judge_config(benchmark, judge_llm)
            print(f"Running task via Aside CLI: {task_id}")

            phase = "execution"
            task_prompt = task["confirmed_task"]
            
            # 调用 Aside CLI 替代原生的 browser-use Agent
            final_text, steps_count, duration_sec = await run_aside_cli(
                task_prompt, timeout=task_timeout
            )

            phase = "evidence"
            result.update(
                steps=steps_count,
                duration=duration_sec,
                cost=0,  # CLI 模式下暂不统计 token 成本
            )
            
            evidence_notes = []
            trace = {
                "agent_task": task_prompt,
                "final_result": final_text,
                "agent_steps": [],
                "ground_truth": task.get("answer"),
                "screenshots_b64": [],
                "output_files_text": "",
                "evidence_notes": evidence_notes,
            }
            artifact.update(
                agent_trace=trace,
                metrics={key: result[key] for key in ("steps", "duration", "cost")},
                task=task,
            )
            result["status"] = "awaiting_judge"
            save_task(run_data_dir, task_id, {**artifact, **result})
            
            # 调用原生的 Judge 模型进行自动打分
            phase = "judge"
            result.update(await judge_trace(task, trace, judge_llm, benchmark))
            
            if result.get("evidence_clipped_sections"):
                sections = ", ".join(result["evidence_clipped_sections"])
                print(f"Task {task_id}: warning: clipped {sections}")
            if result.get("evidence_missing_items"):
                print(f"Task {task_id}: warning: missing evidence for {result['evidence_missing_items']}; item credit follows the rubric")
                
            result["status"] = "judged"
            print("Task", task_id, "raw=", result["raw_score"], "final=", result["score"])
        except Exception as exc:
            result.update(
                status=f"{phase}_error",
                error=f"{type(exc).__name__}: {exc}",
                traceback=traceback.format_exc(),
            )
            print(f"Task {task_id}: {result['status']}: {result['error']}")
        finally:
            save_task(run_data_dir, task_id, {**artifact, **result})
        return result


def summarize_results(results: list[dict]) -> dict:
    scored = [r for r in results if r["score"] is not None]
    complete = len(scored) == len(results)
    mean = sum(r["score"] for r in scored) / len(scored) if scored else None
    raw_mean = sum(r["raw_score"] for r in scored) / len(scored) if scored else None
    return {
        "tasks_completed": len(results),
        "tasks_scored": len(scored),
        "tasks_unscored": len(results) - len(scored),
        "tasks_successful": sum(r["score"] == 1 for r in scored),
        "judge_errors": sum(r["status"] == "judge_error" for r in results),
        "evidence_incomplete": sum(
            r["status"] == "evidence_incomplete" for r in results
        ),
        "tasks_with_clipped_evidence": sum(
            bool(r.get("evidence_clipped_sections")) for r in results
        ),
        "tasks_with_missing_evidence": sum(
            bool(r.get("evidence_missing_items")) for r in results
        ),
        "execution_errors": sum(
            r["status"] == "execution_error" or "execution_error" in r for r in results
        ),
        "mean_score": mean if complete else None,
        "mean_raw_score": raw_mean if complete else None,
        "mean_score_scored_tasks": mean,
        "mean_raw_score_scored_tasks": raw_mean,
        "rh_zeroed": sum(bool(r.get("rh_zeroed")) for r in results),
        "total_steps": sum(r["steps"] for r in results),
        "total_duration": sum(r["duration"] for r in results),
        "total_cost": sum(r["cost"] for r in results),
    }


def parse_args(argv=None):
    parser = argparse.ArgumentParser(
        description="Run benchmark tasks using Aside CLI and browser-use judge"
    )
    parser.add_argument(
        "--output-dir", type=Path, default=Path(__file__).parent / "run_data"
    )
    parser.add_argument("--benchmark", default=DEFAULT_BENCHMARK, choices=BENCHMARKS)
    parser.add_argument(
        "--tasks", type=int, default=None, help="Number of tasks (default: all)"
    )
    parser.add_argument(
        "--judge-model",
        help="Override judge model",
    )
    parser.add_argument(
        "--judge-reasoning",
        default="xhigh",
        choices=["none", "minimal", "low", "medium", "high", "xhigh"],
        help="Reasoning effort for the OpenAI judge",
    )
    parser.add_argument(
        "--task-ids", nargs="+", help="Exact task IDs, in execution order"
    )
    parser.add_argument("--task-timeout", type=int, default=TASK_TIMEOUT)
    parser.add_argument("--concurrency", "--parallel", type=int, default=MAX_CONCURRENT)
    args = parser.parse_args(argv)
    return args


async def main(argv=None):
    args = parse_args(argv)
    tasks = select_tasks(load_tasks(args.benchmark), args.task_ids, args.tasks)
    
    # 初始化 Judge
    judge_llm = create_judge(args.benchmark, args.judge_model, args.judge_reasoning)
    config = judge_config(args.benchmark, judge_llm)
    
    run_start = datetime.now(UTC).strftime("%Y%m%d_%H%M%S_%f")
    run_key = f"{args.benchmark}_AsideCLI_{AGENT_FRAMEWORK_VERSION}"
    root = Path(__file__).parent
    run_data_dir = root / "run_data" / f"{run_key}_start_at_{run_start}"
    results_file = root / "results" / f"{run_key}.json"
    
    run_config = {
        "benchmark": args.benchmark,
        "runner_source_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "dataset_sha256": hashlib.sha256(
            (root / f"{args.benchmark}.enc").read_bytes()
        ).hexdigest(),
        "judge": config,
        "task_ids": [t["task_id"] for t in tasks],
        "task_timeout_seconds": args.task_timeout,
        "max_concurrency": args.concurrency,
    }
    
    run_data_dir.mkdir(parents=True)
    (run_data_dir / "config.json").write_text(json.dumps(run_config, indent=2))
    print(
        f"{args.benchmark}: {len(tasks)} tasks, judge={config['type']} / {config['model']} / {config['reasoning_effort']}"
    )
    
    sem = asyncio.Semaphore(args.concurrency)
    results = await asyncio.gather(
        *[
            run_task(
                t,
                sem,
                run_data_dir=run_data_dir,
                benchmark=args.benchmark,
                judge_llm=judge_llm,
                task_timeout=args.task_timeout,
            )
            for t in tasks
        ]
    )
    
    summary = summarize_results(results)
    results_file.parent.mkdir(parents=True, exist_ok=True)
    runs = json.loads(results_file.read_text()) if results_file.exists() else []
    runs.append(
        {
            "run_start": run_start,
            "config": run_config,
            **summary,
            "task_results": results,
        }
    )
    results_file.write_text(json.dumps(runs, indent=2))
    
    if summary["mean_score"] is not None:
        print(
            f"Mean weighted score: {summary['mean_score']:.2%} (raw: {summary['mean_raw_score']:.2%})"
        )
    else:
        print(
            f"Incomplete evaluation: {summary['tasks_unscored']} unscored tasks; see {run_data_dir}"
        )
    print(f"Results: {results_file}")
    if summary["tasks_unscored"]:
        raise SystemExit(1)


if __name__ == "__main__":
    asyncio.run(main())