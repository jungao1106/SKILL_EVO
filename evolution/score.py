import json
import statistics
from pathlib import Path
from typing import Any


REWARD_KEYS = ("reward", "resolved", "success", "pass")


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(errors="replace"))


def first_reward_value(rewards: dict[str, Any] | None) -> float | None:
    if not rewards:
        return None
    for key in REWARD_KEYS:
        if key in rewards:
            try:
                return float(rewards[key])
            except (TypeError, ValueError):
                return None
    try:
        return float(next(iter(rewards.values())))
    except (StopIteration, TypeError, ValueError):
        return None


def trial_result_paths(job_dir: Path) -> list[Path]:
    result_path = job_dir / "result.json"
    return sorted(
        path
        for path in job_dir.glob("*/result.json")
        if path.resolve() != result_path.resolve()
    )


def summarize_job(job_dir: Path) -> dict[str, Any]:
    job_dir = job_dir.expanduser().resolve()
    job_result_path = job_dir / "result.json"
    if not job_result_path.exists():
        raise FileNotFoundError(f"Missing job result: {job_result_path}")

    task_rows: list[dict[str, Any]] = []
    rewards: list[float] = []
    errors = 0
    for path in trial_result_paths(job_dir):
        result = load_json(path)
        verifier_rewards = (
            result.get("verifier_result", {}).get("rewards")
            if result.get("verifier_result")
            else None
        )
        reward = first_reward_value(verifier_rewards)
        if reward is not None:
            rewards.append(reward)
        if result.get("exception_info"):
            errors += 1
        task_rows.append(
            {
                "task_name": result.get("task_name"),
                "trial_name": result.get("trial_name") or path.parent.name,
                "reward": reward,
                "exception_type": (
                    (result.get("exception_info") or {}).get("exception_type")
                    if result.get("exception_info")
                    else None
                ),
                "result_path": str(path),
            }
        )

    n_trials = len(task_rows)
    resolved = sum(1 for value in rewards if value >= 1.0)
    return {
        "job_dir": str(job_dir),
        "job_name": job_dir.name,
        "n_trials": n_trials,
        "n_errors": errors,
        "resolved": resolved,
        "mean_reward": statistics.fmean(rewards) if rewards else None,
        "tasks": task_rows,
    }


def compare_jobs(baseline_job_dir: Path, eval_job_dir: Path) -> dict[str, Any]:
    baseline = summarize_job(baseline_job_dir)
    evaluation = summarize_job(eval_job_dir)

    baseline_by_task = {row["task_name"]: row for row in baseline["tasks"]}
    eval_by_task = {row["task_name"]: row for row in evaluation["tasks"]}
    task_names = sorted(set(baseline_by_task) | set(eval_by_task))
    deltas: list[dict[str, Any]] = []
    for task_name in task_names:
        before = baseline_by_task.get(task_name, {})
        after = eval_by_task.get(task_name, {})
        before_reward = before.get("reward")
        after_reward = after.get("reward")
        delta = None
        if before_reward is not None and after_reward is not None:
            delta = float(after_reward) - float(before_reward)
        deltas.append(
            {
                "task_name": task_name,
                "baseline_reward": before_reward,
                "eval_reward": after_reward,
                "delta": delta,
                "baseline_exception": before.get("exception_type"),
                "eval_exception": after.get("exception_type"),
            }
        )

    before_mean = baseline.get("mean_reward")
    after_mean = evaluation.get("mean_reward")
    mean_delta = None
    if before_mean is not None and after_mean is not None:
        mean_delta = float(after_mean) - float(before_mean)

    return {
        "baseline": baseline,
        "evaluation": evaluation,
        "mean_delta": mean_delta,
        "resolved_delta": evaluation["resolved"] - baseline["resolved"],
        "tasks": deltas,
    }


def write_report(report: dict[str, Any], out_json: Path, out_md: Path) -> None:
    out_json.parent.mkdir(parents=True, exist_ok=True)
    out_json.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")

    baseline = report["baseline"]
    evaluation = report["evaluation"]
    lines = [
        "# Skill Evolution Score Report",
        "",
        "## Summary",
        "",
        "| Phase | Job | Trials | Errors | Resolved | Mean Reward |",
        "| --- | --- | ---: | ---: | ---: | ---: |",
        "| baseline | {job} | {trials} | {errors} | {resolved} | {mean} |".format(
            job=baseline["job_name"],
            trials=baseline["n_trials"],
            errors=baseline["n_errors"],
            resolved=baseline["resolved"],
            mean=baseline["mean_reward"],
        ),
        "| eval | {job} | {trials} | {errors} | {resolved} | {mean} |".format(
            job=evaluation["job_name"],
            trials=evaluation["n_trials"],
            errors=evaluation["n_errors"],
            resolved=evaluation["resolved"],
            mean=evaluation["mean_reward"],
        ),
        "",
        f"- Mean reward delta: `{report.get('mean_delta')}`",
        f"- Resolved delta: `{report.get('resolved_delta')}`",
        "",
        "## Per Task",
        "",
        "| Task | Baseline | Eval | Delta | Baseline Exception | Eval Exception |",
        "| --- | ---: | ---: | ---: | --- | --- |",
    ]
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
    out_md.write_text("\n".join(lines) + "\n")

