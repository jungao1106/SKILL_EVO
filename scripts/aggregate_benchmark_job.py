#!/usr/bin/env python
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from evolution.score import summarize_job, trial_result_paths  # noqa: E402
from scripts.run_benchmark import _deepswe_result_infra_reason  # noqa: E402


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(errors="replace"))


def write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True) + "\n")


def reconcile_root_job_stats(
    job_dir: Path,
    root_result_path: Path,
    root: dict[str, Any],
) -> dict[str, Any]:
    """Rebuild Harbor's mutable job summary from authoritative trial results."""
    from harbor.models.job.result import JobStats
    from harbor.models.trial.result import TrialResult

    trial_results = [
        TrialResult.model_validate_json(path.read_text(errors="replace"))
        for path in trial_result_paths(job_dir)
    ]
    stats = JobStats.from_trial_results(trial_results)
    for eval_stats in stats.evals.values():
        reward_counts = eval_stats.reward_stats.get("reward") or {}
        denominator = sum(len(trial_names) for trial_names in reward_counts.values())
        if not denominator:
            continue
        total = sum(
            float(reward) * len(trial_names)
            for reward, trial_names in reward_counts.items()
        )
        eval_stats.metrics = [{"mean": total / denominator}]

    reconciled = dict(root)
    reconciled["stats"] = stats.model_dump(mode="json")
    write_json(root_result_path, reconciled)
    return reconciled


def infra_invalid_trials(job_dir: Path) -> list[dict[str, str]]:
    invalid: list[dict[str, str]] = []
    if not job_dir.is_dir():
        return invalid
    for trial_dir in sorted(path for path in job_dir.iterdir() if path.is_dir()):
        config_path = trial_dir / "config.json"
        result_path = trial_dir / "result.json"
        if not config_path.is_file() or not result_path.is_file():
            reason = "missing-config" if not config_path.is_file() else "missing-result"
            invalid.append({"trial_name": trial_dir.name, "reason": reason})
            continue
        try:
            from harbor.models.trial.config import TrialConfig
            from harbor.models.trial.result import TrialResult

            TrialConfig.model_validate_json(config_path.read_text(errors="replace"))
            result = json.loads(result_path.read_text(errors="replace"))
            TrialResult.model_validate(result)
        except Exception as exc:
            invalid.append(
                {
                    "trial_name": trial_dir.name,
                    "reason": f"invalid-trial-metadata:{type(exc).__name__}",
                }
            )
            continue
        reason = _deepswe_result_infra_reason(result)
        if reason is not None:
            invalid.append({"trial_name": trial_dir.name, "reason": reason})
    return invalid


def job_identity_issues(job_dir: Path) -> tuple[list[dict[str, str]], int | None]:
    config_path = job_dir / "config.json"
    if not config_path.is_file():
        return ([{"trial_name": "<job>", "reason": "missing-job-config"}], None)
    try:
        from harbor.job import Job
        from harbor.models.job.config import JobConfig

        config = JobConfig.model_validate_json(config_path.read_text(errors="replace"))
        task_configs = asyncio.run(Job._resolve_task_configs(config))
    except Exception as exc:
        return (
            [
                {
                    "trial_name": "<job>",
                    "reason": f"invalid-job-config:{type(exc).__name__}",
                }
            ],
            None,
        )

    from harbor.models.task.task import Task

    def result_task_name(task_config: Any) -> str:
        if task_config.path is not None:
            return Task(task_dir=task_config.path).name
        return task_config.get_task_id().get_name()

    expected_names = [
        result_task_name(task)
        for _ in range(config.n_attempts)
        for task in task_configs
        for _agent in config.agents
    ]
    observed_names: list[str] = []
    for result_path in trial_result_paths(job_dir):
        try:
            result = read_json(result_path)
        except (OSError, json.JSONDecodeError):
            continue
        name = result.get("task_name")
        if isinstance(name, str) and name:
            observed_names.append(name)

    expected_counts = Counter(expected_names)
    observed_counts = Counter(observed_names)
    issues: list[dict[str, str]] = []
    for task_name in sorted(set(expected_counts) | set(observed_counts)):
        expected_count = expected_counts[task_name]
        observed_count = observed_counts[task_name]
        if expected_count != observed_count:
            issues.append(
                {
                    "trial_name": task_name,
                    "reason": (
                        "task-count-mismatch:"
                        f"expected={expected_count},observed={observed_count}"
                    ),
                }
            )
    return issues, len(expected_names)


def job_provenance(job_dir: Path, summary: dict[str, Any]) -> dict[str, Any]:
    config_path = job_dir / "config.json"
    if not config_path.is_file():
        return {
            "benchmark_name": None,
            "job_config_sha256": None,
            "task_set_sha256": None,
            "resume_contract": None,
        }
    config_bytes = config_path.read_bytes()
    config = json.loads(config_bytes)
    agents = config.get("agents") or []
    benchmark_names = sorted(
        {
            str((agent.get("kwargs") or {}).get("benchmark_name"))
            for agent in agents
            if (agent.get("kwargs") or {}).get("benchmark_name")
        }
    )
    contracts = [
        (agent.get("kwargs") or {}).get("resume_contract")
        for agent in agents
        if isinstance((agent.get("kwargs") or {}).get("resume_contract"), dict)
    ]
    task_names = sorted(str(row.get("task_name") or "") for row in summary["tasks"])
    return {
        "benchmark_name": benchmark_names[0] if len(benchmark_names) == 1 else None,
        "job_config_sha256": hashlib.sha256(config_bytes).hexdigest(),
        "task_set_sha256": hashlib.sha256(
            ("\n".join(task_names) + "\n").encode("utf-8")
        ).hexdigest(),
        "resume_contract": contracts[0]
        if contracts and all(contract == contracts[0] for contract in contracts)
        else None,
    }


def render_markdown(report: dict[str, Any]) -> str:
    evaluation = report["evaluation"]
    lines = [
        "# Benchmark Job Report",
        "",
        "## Summary",
        "",
        f"- Run id: `{report['run_id']}`",
        f"- Complete: `{report['complete']}`",
        f"- Job dir: `{evaluation['job_dir']}`",
        f"- Trials: `{evaluation['n_trials']}`",
        f"- Errors: `{evaluation['n_errors']}`",
        f"- Resolved: `{evaluation['resolved']}`",
        f"- Mean reward: `{evaluation['mean_reward']}`",
        f"- Infra-invalid trials: `{len(report.get('infra_invalid_trials') or [])}`",
        "",
        "## Per Task",
        "",
        "| Task | Reward | Exception |",
        "| --- | ---: | --- |",
    ]
    for row in evaluation.get("tasks") or []:
        lines.append(
            "| {task} | {reward} | {exception} |".format(
                task=row.get("task_name") or "",
                reward=row.get("reward"),
                exception=row.get("exception_type") or "",
            )
        )
    return "\n".join(lines) + "\n"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Aggregate a single Harbor benchmark job.")
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--job-dir", type=Path, required=True)
    parser.add_argument("--out-dir", type=Path, required=True)
    parser.add_argument("--expected-trials", type=int, default=None)
    parser.add_argument("--allow-incomplete", action="store_true")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    job_dir = args.job_dir.expanduser().resolve()
    out_dir = args.out_dir.expanduser().resolve()
    root_result = job_dir / "result.json"
    if not root_result.exists():
        if not args.allow_incomplete:
            raise SystemExit(f"Missing job result: {root_result}")
        summary = {
            "job_dir": str(job_dir),
            "job_name": job_dir.name,
            "n_trials": len(trial_result_paths(job_dir)) if job_dir.exists() else 0,
            "n_errors": 0,
            "resolved": 0,
            "mean_reward": None,
            "tasks": [],
        }
        root = {}
    else:
        root = read_json(root_result)
        root = reconcile_root_job_stats(job_dir, root_result, root)
        summary = summarize_job(job_dir)

    expected = args.expected_trials
    if expected is None:
        expected = int(root.get("n_total_trials") or summary["n_trials"] or 0)
    finished_at = root.get("finished_at")
    invalid_trials = infra_invalid_trials(job_dir)
    identity_issues, configured_trials = job_identity_issues(job_dir)
    invalid_trials.extend(identity_issues)
    if configured_trials is not None and configured_trials != expected:
        invalid_trials.append(
            {
                "trial_name": "<job>",
                "reason": (
                    "expected-trial-count-mismatch:"
                    f"cli={expected},config={configured_trials}"
                ),
            }
        )
    complete = bool(
        finished_at
        and summary["n_trials"] == expected
        and not invalid_trials
    )
    if not complete and not args.allow_incomplete:
        raise SystemExit(
            f"Job incomplete or infra-invalid: {job_dir} trials={summary['n_trials']} "
            f"expected={expected} finished_at={finished_at} "
            f"infra_invalid={len(invalid_trials)}"
        )

    provenance = job_provenance(job_dir, summary)
    report = {
        "schema_version": 1,
        "run_id": args.run_id,
        "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "complete": complete,
        "benchmark_name": provenance["benchmark_name"],
        "provenance": provenance,
        "infra_invalid_trials": invalid_trials,
        "evaluation": summary,
        "tasks": summary.get("tasks") or [],
        "completeness": {
            "expected_trials": expected,
            "trial_result_files": summary["n_trials"],
            "finished_at": finished_at,
            "infra_invalid_trials": len(invalid_trials),
        },
    }
    out_json = out_dir / "score_report.json"
    out_md = out_dir / "score_report.md"
    write_json(out_json, report)
    out_md.write_text(render_markdown(report))
    print(
        json.dumps(
            {
                "complete": complete,
                "eval_trials": summary["n_trials"],
                "eval_resolved": summary["resolved"],
                "eval_errors": summary["n_errors"],
                "eval_mean_reward": summary["mean_reward"],
                "infra_invalid_trials": len(invalid_trials),
                "out_json": str(out_json),
                "out_md": str(out_md),
            },
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
