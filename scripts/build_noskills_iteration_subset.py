#!/usr/bin/env python
import argparse
import json
import re
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_BASELINE_GLOB = "jobs/skill_evo_verified_glm51_full_v0001_*_baseline_noskills"
DEFAULT_OUT_DIR = ROOT / "run_logs" / "skill_evo_shards"
REWARD_KEYS = ("reward", "resolved", "success", "pass")


def reward_value(result: dict[str, Any]) -> float | None:
    rewards = (result.get("verifier_result") or {}).get("rewards")
    if not isinstance(rewards, dict):
        return None
    for key in REWARD_KEYS:
        if key in rewards:
            try:
                return float(rewards[key])
            except (TypeError, ValueError):
                return None
    return None


def task_name(result: dict[str, Any], result_path: Path) -> str:
    value = result.get("task_name")
    if isinstance(value, str) and value:
        return value
    task_id = result.get("task_id")
    if isinstance(task_id, dict):
        org = task_id.get("org")
        name = task_id.get("name")
        if org and name:
            return f"{org}/{name}"
        if name:
            return str(name)
    return result_path.parent.name.rsplit("__", 1)[0]


def shard_label(path: Path, fallback_index: int) -> str:
    match = re.search(r"(?<!\d)(\d{3})_(\d{3})(?!\d)", path.name)
    if match:
        return f"{match.group(1)}_{match.group(2)}"
    return f"shard{fallback_index:02d}"


def include_result(result: dict[str, Any], predicate: str) -> bool:
    reward = reward_value(result)
    if predicate == "reward_ne_1":
        return reward != 1.0
    if predicate == "reward_eq_0":
        return reward == 0.0
    if predicate == "missing_reward":
        return reward is None
    raise ValueError(f"Unknown predicate: {predicate}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Build task-name shards from no-skills baseline results for targeted skill evolution."
    )
    parser.add_argument("--baseline-job-glob", default=DEFAULT_BASELINE_GLOB)
    parser.add_argument("--predicate", choices=["reward_ne_1", "reward_eq_0", "missing_reward"], default="reward_ne_1")
    parser.add_argument("--name", default="noskills_reward_ne_1")
    parser.add_argument("--date-tag", default="")
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUT_DIR)
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    import glob

    baseline_jobs = sorted(Path(path).resolve() for path in glob.glob(args.baseline_job_glob))
    if not baseline_jobs:
        raise SystemExit(f"No baseline jobs matched {args.baseline_job_glob}")
    tag = args.date_tag or datetime.now(timezone.utc).strftime("%Y%m%d_%H%M")
    out_dir = args.out_dir.expanduser().resolve()
    rows: list[dict[str, Any]] = []
    all_tasks: list[str] = []
    shard_files: list[str] = []
    shard_counts: dict[str, int] = {}
    reward_counts: Counter[str] = Counter()
    exception_counts: Counter[str] = Counter()

    for index, job in enumerate(baseline_jobs, start=1):
        selected: list[str] = []
        label = shard_label(job, index)
        for result_path in sorted(job.glob("*/result.json")):
            if result_path.parent == job:
                continue
            try:
                result = json.loads(result_path.read_text(errors="replace"))
            except (OSError, ValueError):
                continue
            reward = reward_value(result)
            exception = (result.get("exception_info") or {}).get("exception_type")
            reward_counts[str(reward)] += 1
            if exception:
                exception_counts[str(exception)] += 1
            if not include_result(result, args.predicate):
                continue
            name = task_name(result, result_path)
            selected.append(name)
            rows.append(
                {
                    "task_name": name,
                    "baseline_job": str(job),
                    "baseline_result": str(result_path),
                    "reward": reward,
                    "exception_type": exception,
                    "shard_label": label,
                }
            )
        output = out_dir / f"{args.name}_{label}_{tag}.txt"
        shard_counts[label] = len(selected)
        shard_files.append(str(output))
        all_tasks.extend(selected)
        if not args.dry_run:
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_text("\n".join(selected) + ("\n" if selected else ""))

    all_file = out_dir / f"{args.name}_all_tasks_{tag}.txt"
    rows_file = out_dir / f"{args.name}_{tag}.jsonl"
    manifest_file = out_dir / f"{args.name}_{tag}_manifest.json"
    manifest = {
        "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "baseline_job_glob": args.baseline_job_glob,
        "baseline_jobs": [str(path) for path in baseline_jobs],
        "predicate": args.predicate,
        "name": args.name,
        "date_tag": tag,
        "total_selected": len(all_tasks),
        "shard_counts": shard_counts,
        "shard_files": shard_files,
        "all_tasks_file": str(all_file),
        "rows_file": str(rows_file),
        "baseline_reward_counts": dict(reward_counts),
        "baseline_exception_counts": dict(exception_counts),
    }
    if not args.dry_run:
        all_file.write_text("\n".join(all_tasks) + ("\n" if all_tasks else ""))
        rows_file.write_text(
            "\n".join(json.dumps(row, ensure_ascii=False, sort_keys=True) for row in rows)
            + ("\n" if rows else "")
        )
        manifest_file.write_text(json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
    print(json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
