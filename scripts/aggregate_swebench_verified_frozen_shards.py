#!/usr/bin/env python
from __future__ import annotations

import argparse
import json
import statistics
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from evolution.score import summarize_job, trial_result_paths


DEFAULT_SHARD_ROOT = ROOT / "run_logs" / "swebench_verified_frozen_shards"


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(errors="replace"))


def write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True) + "\n")


def job_status(job_dir: Path, *, expected_trials: int) -> dict[str, Any]:
    root_result = job_dir / "result.json"
    trial_paths = trial_result_paths(job_dir) if job_dir.exists() else []
    root_stats: dict[str, Any] = {}
    finished_at = None
    if root_result.exists():
        try:
            root = load_json(root_result)
            finished_at = root.get("finished_at")
            root_stats = root.get("stats") or {}
        except Exception as exc:  # pragma: no cover - diagnostic path
            root_stats = {"load_error": str(exc)}
    return {
        "job_dir": str(job_dir),
        "exists": job_dir.exists(),
        "root_result_exists": root_result.exists(),
        "finished_at": finished_at,
        "expected_trials": expected_trials,
        "trial_result_files": len(trial_paths),
        "root_n_trials": root_stats.get("n_trials"),
        "root_n_errors": root_stats.get("n_errors"),
        "complete": bool(root_result.exists() and finished_at and len(trial_paths) >= expected_trials),
    }


def empty_summary(job_dir: Path) -> dict[str, Any]:
    return {
        "job_dir": str(job_dir),
        "job_name": job_dir.name,
        "n_trials": 0,
        "n_errors": 0,
        "resolved": 0,
        "mean_reward": None,
        "tasks": [],
    }


def summarize_if_present(job_dir: Path) -> dict[str, Any]:
    if (job_dir / "result.json").exists():
        return summarize_job(job_dir)
    return empty_summary(job_dir)


def combine_summaries(*, label: str, summaries: list[dict[str, Any]]) -> dict[str, Any]:
    tasks: list[dict[str, Any]] = []
    for summary in summaries:
        for row in summary.get("tasks") or []:
            tasks.append({**row, "source_job": summary.get("job_name")})
    rewards = [
        float(row["reward"])
        for row in tasks
        if row.get("reward") is not None
    ]
    return {
        "job_dir": None,
        "job_name": label,
        "n_trials": len(tasks),
        "n_errors": sum(1 for row in tasks if row.get("exception_type")),
        "resolved": sum(1 for reward in rewards if reward >= 1.0),
        "mean_reward": statistics.fmean(rewards) if rewards else None,
        "tasks": tasks,
    }


def compare_combined(
    baseline: dict[str, Any],
    evaluation: dict[str, Any],
) -> dict[str, Any]:
    baseline_by_task = {row["task_name"]: row for row in baseline["tasks"]}
    eval_by_task = {row["task_name"]: row for row in evaluation["tasks"]}
    task_names = sorted(set(baseline_by_task) | set(eval_by_task))
    rows: list[dict[str, Any]] = []
    for task_name in task_names:
        before = baseline_by_task.get(task_name, {})
        after = eval_by_task.get(task_name, {})
        before_reward = before.get("reward")
        after_reward = after.get("reward")
        delta = None
        if before_reward is not None and after_reward is not None:
            delta = float(after_reward) - float(before_reward)
        rows.append(
            {
                "task_name": task_name,
                "baseline_reward": before_reward,
                "eval_reward": after_reward,
                "delta": delta,
                "baseline_exception": before.get("exception_type"),
                "eval_exception": after.get("exception_type"),
                "baseline_source_job": before.get("source_job"),
                "eval_source_job": after.get("source_job"),
            }
        )
    mean_delta = None
    if baseline.get("mean_reward") is not None and evaluation.get("mean_reward") is not None:
        mean_delta = float(evaluation["mean_reward"]) - float(baseline["mean_reward"])
    return {
        "baseline": baseline,
        "evaluation": evaluation,
        "mean_delta": mean_delta,
        "resolved_delta": evaluation["resolved"] - baseline["resolved"],
        "tasks": rows,
    }


def render_markdown(report: dict[str, Any]) -> str:
    completeness = report["completeness"]
    baseline = report["baseline"]
    evaluation = report["evaluation"]
    lines = [
        "# SWE-bench Verified Frozen Skill Shard Report",
        "",
        "## Summary",
        "",
        f"- Run id: `{report['run_id']}`",
        f"- Complete: `{report['complete']}`",
        f"- Dataset: `{report.get('dataset')}`",
        f"- Expected trials: `{completeness['expected_trials']}`",
        f"- Baseline completed trials: `{baseline['n_trials']}`",
        f"- Eval completed trials: `{evaluation['n_trials']}`",
        f"- Mean reward delta: `{report.get('mean_delta')}`",
        f"- Resolved delta: `{report.get('resolved_delta')}`",
        "",
        "| Phase | Trials | Errors | Resolved | Mean Reward |",
        "| --- | ---: | ---: | ---: | ---: |",
        f"| baseline | {baseline['n_trials']} | {baseline['n_errors']} | {baseline['resolved']} | {baseline['mean_reward']} |",
        f"| eval | {evaluation['n_trials']} | {evaluation['n_errors']} | {evaluation['resolved']} | {evaluation['mean_reward']} |",
        "",
        "## Shards",
        "",
        "| Shard | Baseline Trials | Baseline Complete | Eval Trials | Eval Complete |",
        "| --- | ---: | --- | ---: | --- |",
    ]
    for row in completeness["shards"]:
        baseline_status = row["baseline"]
        eval_status = row["evaluation"]
        lines.append(
            "| {label} | {bt}/{expected} | {bc} | {et}/{expected} | {ec} |".format(
                label=row["label"],
                bt=baseline_status["trial_result_files"],
                expected=row["expected_trials"],
                bc=baseline_status["complete"],
                et=eval_status["trial_result_files"],
                ec=eval_status["complete"],
            )
        )
    lines.extend(
        [
            "",
            "## Per Task",
            "",
            "| Task | Baseline | Eval | Delta | Baseline Exception | Eval Exception |",
            "| --- | ---: | ---: | ---: | --- | --- |",
        ]
    )
    for row in report["tasks"]:
        lines.append(
            "| {task} | {before} | {after} | {delta} | {before_exc} | {after_exc} |".format(
                task=row.get("task_name") or "",
                before=row.get("baseline_reward"),
                after=row.get("eval_reward"),
                delta=row.get("delta"),
                before_exc=row.get("baseline_exception") or "",
                after_exc=row.get("eval_exception") or "",
            )
        )
    return "\n".join(lines) + "\n"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Aggregate 5-shard SWE-bench Verified frozen-library eval reports."
    )
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--manifest-path", type=Path, default=None)
    parser.add_argument("--out-dir", type=Path, default=None)
    parser.add_argument(
        "--allow-incomplete",
        action="store_true",
        help="Write a progress report even when some baseline/eval shards are still running.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    manifest_path = (
        args.manifest_path.expanduser().resolve()
        if args.manifest_path
        else DEFAULT_SHARD_ROOT / args.run_id / "manifest.json"
    )
    if not manifest_path.exists():
        raise SystemExit(f"Missing shard manifest: {manifest_path}")
    manifest = load_json(manifest_path)
    out_dir = (
        args.out_dir.expanduser().resolve()
        if args.out_dir
        else manifest_path.parent / "aggregate"
    )

    shard_rows: list[dict[str, Any]] = []
    baseline_summaries: list[dict[str, Any]] = []
    eval_summaries: list[dict[str, Any]] = []
    incomplete: list[str] = []
    expected_total = 0
    for row in manifest.get("shards") or []:
        expected = int(row.get("task_count") or 0)
        expected_total += expected
        baseline_dir = Path(row["baseline_job_dir"]).expanduser().resolve()
        eval_dir = Path(row["eval_job_dir"]).expanduser().resolve()
        baseline_status = job_status(baseline_dir, expected_trials=expected)
        eval_status = job_status(eval_dir, expected_trials=expected)
        shard_rows.append(
            {
                "label": row.get("label"),
                "expected_trials": expected,
                "baseline": baseline_status,
                "evaluation": eval_status,
            }
        )
        if not baseline_status["complete"]:
            incomplete.append(f"{row.get('label')}: baseline incomplete")
        if not eval_status["complete"]:
            incomplete.append(f"{row.get('label')}: eval incomplete")
        baseline_summaries.append(summarize_if_present(baseline_dir))
        eval_summaries.append(summarize_if_present(eval_dir))

    if incomplete and not args.allow_incomplete:
        lines = "\n".join(f"- {item}" for item in incomplete)
        raise SystemExit(f"Shard eval is incomplete; rerun with --allow-incomplete for progress.\n{lines}")

    baseline = combine_summaries(label=f"{args.run_id}_baseline_all_shards", summaries=baseline_summaries)
    evaluation = combine_summaries(label=f"{args.run_id}_eval_all_shards", summaries=eval_summaries)
    report = compare_combined(baseline, evaluation)
    report.update(
        {
            "schema_version": 1,
            "run_id": args.run_id,
            "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "manifest_path": str(manifest_path),
            "dataset": manifest.get("dataset"),
            "skill_version_id": manifest.get("skill_version_id"),
            "complete": not incomplete,
            "completeness": {
                "expected_trials": expected_total,
                "incomplete": incomplete,
                "shards": shard_rows,
            },
        }
    )
    out_json = out_dir / "score_report.json"
    out_md = out_dir / "score_report.md"
    write_json(out_json, report)
    out_md.write_text(render_markdown(report))
    print(json.dumps(
        {
            "complete": report["complete"],
            "baseline_trials": baseline["n_trials"],
            "eval_trials": evaluation["n_trials"],
            "baseline_resolved": baseline["resolved"],
            "eval_resolved": evaluation["resolved"],
            "mean_delta": report["mean_delta"],
            "resolved_delta": report["resolved_delta"],
            "out_json": str(out_json),
            "out_md": str(out_md),
        },
        ensure_ascii=False,
        indent=2,
        sort_keys=True,
    ))


if __name__ == "__main__":
    main()
