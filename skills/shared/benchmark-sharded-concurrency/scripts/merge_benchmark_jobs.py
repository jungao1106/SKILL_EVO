#!/usr/bin/env python
"""Merge sharded Harbor benchmark job directories into one job directory.

The script copies trial directories from multiple completed jobs, optionally
deduplicates by task name, rewrites the copied trial result metadata for the new
job directory, and regenerates the merged job-level result.json.
"""

from __future__ import annotations

import argparse
import glob
import json
import os
import shutil
import sys
from collections import Counter, OrderedDict, defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any
from uuid import uuid4


def default_repo_root() -> Path:
    env_root = os.environ.get("BENCHMARK_REPO_ROOT") or os.environ.get("REPO_ROOT")
    if env_root:
        return Path(env_root).expanduser().resolve()

    candidates = [
        Path.cwd().resolve(),
        *Path.cwd().resolve().parents,
        *Path(__file__).resolve().parents,
    ]
    seen: set[Path] = set()
    for candidate in candidates:
        if candidate in seen:
            continue
        seen.add(candidate)
        if (candidate / "jobs").is_dir() and (candidate / "skills").is_dir():
            return candidate
    return Path.cwd().resolve()


ROOT = default_repo_root()
JOBS_DIR = ROOT / "jobs"


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(errors="replace"))


def write_json(path: Path, data: dict[str, Any]) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n")


def expand_job_args(values: list[str]) -> list[Path]:
    jobs: list[Path] = []
    seen: set[Path] = set()
    for value in values:
        matches = glob.glob(value)
        if not matches:
            value_path = Path(value)
            if not value_path.is_absolute():
                if value_path.parts and value_path.parts[0] == "jobs":
                    matches = glob.glob(str(ROOT / value_path))
                else:
                    matches = glob.glob(str(JOBS_DIR / value_path))
        candidates = matches if matches else [value]
        for candidate in candidates:
            path = Path(candidate)
            if not path.is_absolute():
                if path.parts and path.parts[0] == "jobs":
                    path = ROOT / path
                else:
                    path = JOBS_DIR / path
            path = path.resolve()
            if path in seen:
                continue
            seen.add(path)
            jobs.append(path)
    return jobs


def resolve_output(value: str) -> Path:
    path = Path(value)
    if path.is_absolute():
        return path
    if path.parts and path.parts[0] == "jobs":
        return (ROOT / path).resolve()
    return (JOBS_DIR / path).resolve()


def trial_task_name(trial_dir: Path) -> str:
    result_path = trial_dir / "result.json"
    if not result_path.exists():
        raise ValueError(f"trial missing result.json: {trial_dir}")
    data = load_json(result_path)
    task_name = data.get("task_name")
    if not task_name:
        raise ValueError(f"trial result missing task_name: {result_path}")
    return str(task_name)


def iter_trial_dirs(job_dir: Path) -> list[Path]:
    return sorted(path.parent for path in job_dir.glob("*/result.json"))


def copy_function_for(mode: str):
    if mode == "copy":
        return shutil.copy2

    if mode == "hardlink":
        def hardlink_or_copy(src: str, dst: str) -> None:
            try:
                os.link(src, dst)
            except OSError:
                shutil.copy2(src, dst)

        return hardlink_or_copy

    raise ValueError(f"unsupported copy mode: {mode}")


def rewrite_trial_result(
    *,
    result_path: Path,
    source_job: Path,
    source_trial: Path,
    output_job: Path,
    output_trial: Path,
    output_trial_name: str,
) -> None:
    data = load_json(result_path)

    replacements = [
        (str(source_trial), str(output_trial)),
        (source_trial.as_uri(), output_trial.as_uri()),
        (str(source_job), str(output_job)),
        (source_job.as_uri(), output_job.as_uri()),
        (source_trial.name, output_trial_name),
    ]

    def replace(value: Any) -> Any:
        if isinstance(value, str):
            for old, new in replacements:
                value = value.replace(old, new)
            return value
        if isinstance(value, list):
            return [replace(item) for item in value]
        if isinstance(value, dict):
            return {key: replace(item) for key, item in value.items()}
        return value

    data = replace(data)
    data["trial_name"] = output_trial_name
    data["trial_uri"] = output_trial.as_uri()
    data.setdefault("config", {})
    data["config"]["trial_name"] = output_trial_name
    data["config"]["trials_dir"] = str(output_job)

    # If copy mode is hardlink, unlink first so rewriting metadata does not
    # mutate the source trial result.
    result_path.unlink(missing_ok=True)
    write_json(result_path, data)


def maybe_rewrite_trial_config(
    *,
    config_path: Path,
    source_job: Path,
    source_trial: Path,
    output_job: Path,
    output_trial: Path,
    output_trial_name: str,
) -> None:
    if not config_path.exists():
        return
    try:
        data = load_json(config_path)
    except json.JSONDecodeError:
        return

    replacements = [
        (str(source_trial), str(output_trial)),
        (str(source_job), str(output_job)),
        (source_trial.name, output_trial_name),
    ]

    def replace(value: Any) -> Any:
        if isinstance(value, str):
            for old, new in replacements:
                value = value.replace(old, new)
            return value
        if isinstance(value, list):
            return [replace(item) for item in value]
        if isinstance(value, dict):
            return {key: replace(item) for key, item in value.items()}
        return value

    data = replace(data)
    data["trial_name"] = output_trial_name
    data["trials_dir"] = str(output_job)
    config_path.unlink(missing_ok=True)
    write_json(config_path, data)


def format_eval_key(agent_name: str, model_name: str | None, dataset_name: str) -> str:
    if model_name:
        return f"{agent_name}__{model_name}__{dataset_name}".replace("/", "-")
    return f"{agent_name}__{dataset_name}".replace("/", "-")


def build_job_stats(trial_results: list[dict[str, Any]]) -> dict[str, Any]:
    stats: dict[str, Any] = {"n_trials": 0, "n_errors": 0, "evals": {}}
    for result in trial_results:
        stats["n_trials"] += 1
        agent_info = result.get("agent_info") or {}
        model_info = agent_info.get("model_info") or {}
        agent_name = str(agent_info.get("name") or "agent")
        model_name = model_info.get("name")
        dataset_name = str(result.get("source") or "adhoc")
        eval_key = format_eval_key(agent_name, model_name, dataset_name)
        eval_stats = stats["evals"].setdefault(
            eval_key,
            {
                "n_trials": 0,
                "n_errors": 0,
                "metrics": [],
                "reward_stats": {},
                "exception_stats": {},
            },
        )

        verifier_result = result.get("verifier_result") or {}
        rewards = verifier_result.get("rewards")
        if isinstance(rewards, dict):
            eval_stats["n_trials"] += 1
            for key, value in rewards.items():
                reward_bucket = eval_stats["reward_stats"].setdefault(key, {})
                reward_bucket.setdefault(str(value), []).append(result["trial_name"])

        exception_info = result.get("exception_info")
        if isinstance(exception_info, dict) and exception_info:
            exception_type = str(exception_info.get("exception_type") or "unknown")
            eval_stats["exception_stats"].setdefault(exception_type, []).append(
                result["trial_name"]
            )
            eval_stats["n_errors"] += 1
            stats["n_errors"] += 1

    return stats


def parse_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def unique_destination(output_job: Path, preferred_name: str) -> Path:
    candidate = output_job / preferred_name
    if not candidate.exists():
        return candidate
    for index in range(2, 10000):
        candidate = output_job / f"{preferred_name}__merge{index}"
        if not candidate.exists():
            return candidate
    raise RuntimeError(f"could not allocate unique trial directory for {preferred_name}")


def summarize_result(result: dict[str, Any]) -> dict[str, Any]:
    reward = Counter()
    exceptions = Counter()
    for eval_stats in (result.get("stats") or {}).get("evals", {}).values():
        for value, trials in (
            eval_stats.get("reward_stats", {}).get("reward", {}).items()
        ):
            reward[str(value)] += len(trials)
        for exc_type, trials in eval_stats.get("exception_stats", {}).items():
            exceptions[exc_type] += len(trials)
    return {
        "n_total_trials": result.get("n_total_trials"),
        "n_trials": result.get("stats", {}).get("n_trials"),
        "n_errors": result.get("stats", {}).get("n_errors"),
        "reward": dict(reward),
        "exception_stats": dict(exceptions),
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Merge sharded Harbor benchmark job directories into one result job."
    )
    parser.add_argument(
        "--jobs",
        nargs="+",
        required=True,
        help="Job directories, job names under jobs/, or shell globs. Order matters for duplicate handling.",
    )
    parser.add_argument(
        "--output-job",
        required=True,
        help="Merged job directory path, or job name under jobs/.",
    )
    parser.add_argument(
        "--duplicate-strategy",
        choices=("error", "first", "last"),
        default="error",
        help="How to handle duplicate task_name values across input jobs.",
    )
    parser.add_argument(
        "--copy-mode",
        choices=("copy", "hardlink"),
        default="copy",
        help="Use hardlink to save space on the same filesystem; result metadata is still rewritten safely.",
    )
    parser.add_argument(
        "--overwrite-output",
        action="store_true",
        help="Remove an existing output job directory before writing.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Validate inputs and print the selected merge plan without writing output.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    job_dirs = expand_job_args(args.jobs)
    output_job = resolve_output(args.output_job)

    if not job_dirs:
        raise SystemExit("no input jobs resolved")
    missing = [str(path) for path in job_dirs if not (path / "result.json").exists()]
    if missing:
        raise SystemExit("input job(s) missing result.json:\n" + "\n".join(missing))

    selected: OrderedDict[str, tuple[Path, Path]] = OrderedDict()
    skipped: list[dict[str, str]] = []
    duplicates = 0
    for job_dir in job_dirs:
        for trial_dir in iter_trial_dirs(job_dir):
            task_name = trial_task_name(trial_dir)
            if task_name in selected:
                duplicates += 1
                if args.duplicate_strategy == "error":
                    previous_job, previous_trial = selected[task_name]
                    raise SystemExit(
                        "duplicate task_name encountered; use "
                        "--duplicate-strategy first or last if intentional:\n"
                        f"task={task_name}\n"
                        f"previous={previous_job / previous_trial.name}\n"
                        f"new={trial_dir}"
                    )
                if args.duplicate_strategy == "first":
                    skipped.append(
                        {
                            "task_name": task_name,
                            "source_job": job_dir.name,
                            "source_trial": trial_dir.name,
                            "reason": "duplicate_skipped_first_wins",
                        }
                    )
                    continue
                previous_job, previous_trial = selected[task_name]
                skipped.append(
                    {
                        "task_name": task_name,
                        "source_job": previous_job.name,
                        "source_trial": previous_trial.name,
                        "reason": "duplicate_replaced_by_later_job",
                    }
                )
            selected[task_name] = (job_dir, trial_dir)

    plan = {
        "input_jobs": [str(path) for path in job_dirs],
        "output_job": str(output_job),
        "duplicate_strategy": args.duplicate_strategy,
        "duplicates_seen": duplicates,
        "selected_trials": len(selected),
        "skipped_trials": len(skipped),
    }
    if args.dry_run:
        print(json.dumps(plan, ensure_ascii=False, indent=2))
        return

    if output_job.exists():
        if not args.overwrite_output:
            raise SystemExit(
                f"output job already exists: {output_job}\n"
                "Use --overwrite-output to replace it."
            )
        shutil.rmtree(output_job)
    output_job.mkdir(parents=True)

    copy_function = copy_function_for(args.copy_mode)
    manifest: list[dict[str, Any]] = []
    trial_results: list[dict[str, Any]] = []
    seen_dest_names: set[str] = set()

    for task_name, (source_job, source_trial) in selected.items():
        destination = unique_destination(output_job, source_trial.name)
        destination_name = destination.name
        if destination_name in seen_dest_names:
            raise RuntimeError(f"duplicate destination trial name: {destination_name}")
        seen_dest_names.add(destination_name)

        shutil.copytree(source_trial, destination, copy_function=copy_function)
        rewrite_trial_result(
            result_path=destination / "result.json",
            source_job=source_job,
            source_trial=source_trial,
            output_job=output_job,
            output_trial=destination,
            output_trial_name=destination_name,
        )
        maybe_rewrite_trial_config(
            config_path=destination / "config.json",
            source_job=source_job,
            source_trial=source_trial,
            output_job=output_job,
            output_trial=destination,
            output_trial_name=destination_name,
        )

        result = load_json(destination / "result.json")
        trial_results.append(result)
        manifest.append(
            {
                "task_name": task_name,
                "source_job": source_job.name,
                "source_trial": source_trial.name,
                "destination_trial": destination_name,
                "reward": (
                    (result.get("verifier_result") or {})
                    .get("rewards", {})
                    .get("reward")
                ),
                "exception_type": (
                    (result.get("exception_info") or {}).get("exception_type")
                    if result.get("exception_info")
                    else None
                ),
            }
        )

    started_values = [
        value
        for value in (parse_datetime(result.get("started_at")) for result in trial_results)
        if value is not None
    ]
    finished_values = [
        value
        for value in (parse_datetime(result.get("finished_at")) for result in trial_results)
        if value is not None
    ]
    merged_result = {
        "id": str(uuid4()),
        "started_at": min(started_values).isoformat() if started_values else None,
        "finished_at": max(finished_values).isoformat() if finished_values else None,
        "n_total_trials": len(trial_results),
        "stats": build_job_stats(trial_results),
    }
    write_json(output_job / "result.json", merged_result)
    write_json(
        output_job / "config.json",
        {
            "job_name": output_job.name,
            "merge": plan,
        },
    )
    with (output_job / "merge_manifest.jsonl").open("w") as handle:
        for row in manifest:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")
    if skipped:
        with (output_job / "merge_skipped.jsonl").open("w") as handle:
            for row in skipped:
                handle.write(json.dumps(row, ensure_ascii=False) + "\n")

    print(
        json.dumps(
            {
                **plan,
                "summary": summarize_result(merged_result),
                "manifest": str(output_job / "merge_manifest.jsonl"),
                "skipped": str(output_job / "merge_skipped.jsonl") if skipped else None,
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.exit(130)
