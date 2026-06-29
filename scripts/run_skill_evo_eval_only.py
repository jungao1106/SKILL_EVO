#!/usr/bin/env python
import argparse
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from dotenv import load_dotenv


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from evolution.score import compare_jobs, write_report


DEFAULT_DATASET = "swe-bench/swe-bench-verified@2"
DEFAULT_EVO_ROOT = ROOT / "run_logs" / "evolution"
DEFAULT_SKILL_ROOT = ROOT / "skills" / "accepted"


def log(message: str) -> None:
    print(f"[skill-evo-eval] {message}", flush=True)


def run_command(command: list[str], *, env: dict[str, str], dry_run: bool) -> None:
    log(" ".join(command))
    if dry_run:
        return
    subprocess.run(command, cwd=ROOT, env=env, check=True)


def task_args(args: argparse.Namespace) -> list[str]:
    values: list[str] = []
    for task_file in args.task_names_file or []:
        values.extend(["--task-names-file", str(task_file)])
    for task_name in args.include_task_name or []:
        values.extend(["--include-task-name", task_name])
    if args.n_tasks is not None:
        values.extend(["--n-tasks", str(args.n_tasks)])
    return values


def benchmark_command(args: argparse.Namespace, *, job_name: str) -> list[str]:
    command = [
        sys.executable,
        str(ROOT / "scripts" / "run_benchmark.py"),
        "--dataset",
        args.dataset,
        "--provider",
        args.provider,
        "--job-name",
        job_name,
        "--concurrency",
        str(args.concurrency),
        "--agent-setup-timeout-sec",
        str(args.agent_setup_timeout_sec),
        "--e2b-sandbox-timeout-sec",
        str(args.e2b_sandbox_timeout_sec),
        "--override-cpus",
        str(args.override_cpus),
        "--override-memory-mb",
        str(args.override_memory_mb),
        "--override-storage-mb",
        str(args.override_storage_mb),
    ]
    if args.agent_timeout_sec is not None:
        command.extend(["--agent-timeout-sec", str(args.agent_timeout_sec)])
    if args.provider_base_url:
        command.extend(["--provider-base-url", args.provider_base_url])
    if args.provider_model:
        command.extend(["--provider-model", args.provider_model])
    if args.provider_api_key:
        command.extend(["--provider-api-key", args.provider_api_key])
    if args.provider_api:
        command.extend(["--provider-api", args.provider_api])
    if args.model_context_window is not None:
        command.extend(["--model-context-window", str(args.model_context_window)])
    if args.model_max_tokens is not None:
        command.extend(["--model-max-tokens", str(args.model_max_tokens)])
    if args.result_only:
        command.append("--result-only")
    if args.force_build:
        command.append("--force-build")
    if args.keep_sandboxes:
        command.append("--keep-sandboxes")
    command.extend(task_args(args))
    command.append("--use-skills")
    return command


def write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True) + "\n")


def update_version_index(
    *,
    version_id: str,
    skill_pack_root: Path,
    run_name: str,
    run_dir: Path,
    baseline_job_dir: Path,
    eval_job_dir: Path,
    report_json: Path,
    report_md: Path,
    report: dict[str, Any],
) -> None:
    index_path = skill_pack_root.parent / "VERSIONS.json"
    lock_path = index_path.with_name(f"{index_path.name}.lock")
    with lock_path.open("w") as lock_handle:
        try:
            import fcntl

            fcntl.flock(lock_handle, fcntl.LOCK_EX)
        except ImportError:
            pass
        if index_path.exists():
            try:
                index = json.loads(index_path.read_text(errors="replace"))
            except (OSError, ValueError):
                index = {}
        else:
            index = {}
        if not isinstance(index, dict):
            index = {}
        versions = index.setdefault("versions", {})
        existing = versions.get(version_id)
        if not isinstance(existing, dict):
            existing = {}
        shard_runs = existing.get("shard_runs")
        if not isinstance(shard_runs, list):
            shard_runs = []
        shard_runs = [
            row
            for row in shard_runs
            if not isinstance(row, dict) or row.get("run_name") != run_name
        ]
        shard_runs.append(
            {
                "run_name": run_name,
                "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "mode": "eval_only",
                "run_dir": str(run_dir),
                "baseline_job_dir": str(baseline_job_dir),
                "eval_job_dir": str(eval_job_dir),
                "score_report_json": str(report_json),
                "score_report_md": str(report_md),
                "metrics": {
                    "baseline_trials": report.get("baseline", {}).get("n_trials"),
                    "baseline_errors": report.get("baseline", {}).get("n_errors"),
                    "baseline_resolved": report.get("baseline", {}).get("resolved"),
                    "baseline_mean_reward": report.get("baseline", {}).get("mean_reward"),
                    "eval_trials": report.get("evaluation", {}).get("n_trials"),
                    "eval_errors": report.get("evaluation", {}).get("n_errors"),
                    "eval_resolved": report.get("evaluation", {}).get("resolved"),
                    "eval_mean_reward": report.get("evaluation", {}).get("mean_reward"),
                    "mean_delta": report.get("mean_delta"),
                    "resolved_delta": report.get("resolved_delta"),
                },
            }
        )
        versions[version_id] = {
            **existing,
            "version_id": version_id,
            "updated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "mode": "swebench_verified_eval_only",
            "skill_pack_root": str(skill_pack_root),
            "shard_runs": shard_runs,
            "aggregate": {
                "shards": len(shard_runs),
                "eval_trials": sum(
                    int((row.get("metrics") or {}).get("eval_trials") or 0)
                    for row in shard_runs
                    if isinstance(row, dict)
                ),
                "eval_errors": sum(
                    int((row.get("metrics") or {}).get("eval_errors") or 0)
                    for row in shard_runs
                    if isinstance(row, dict)
                ),
                "eval_resolved": sum(
                    int((row.get("metrics") or {}).get("eval_resolved") or 0)
                    for row in shard_runs
                    if isinstance(row, dict)
                ),
            },
        }
        index["schema_version"] = 1
        index["active_version"] = version_id
        index["updated_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
        tmp_path = index_path.with_name(f".{index_path.name}.{os.getpid()}.tmp")
        write_json(tmp_path, index)
        tmp_path.replace(index_path)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Evaluate an existing skill version against an existing no-skills baseline."
    )
    parser.add_argument("--run-name", required=True)
    parser.add_argument("--dataset", default=os.getenv("HARBOR_DATASET", DEFAULT_DATASET))
    parser.add_argument("--provider", choices=["openai", "tinker"], default=os.getenv("LLM_PROVIDER", "openai"))
    parser.add_argument("--provider-base-url", default=os.getenv("PROVIDER_BASE_URL"))
    parser.add_argument("--provider-model", default=os.getenv("PROVIDER_MODEL"))
    parser.add_argument("--provider-api-key", default=os.getenv("PROVIDER_API_KEY"))
    parser.add_argument("--provider-api", default=os.getenv("PROVIDER_API"))
    parser.add_argument("--baseline-job-dir", type=Path, required=True)
    parser.add_argument("--eval-job-dir", type=Path, default=None)
    parser.add_argument("--skill-version-id", required=True)
    parser.add_argument("--skill-root", type=Path, default=DEFAULT_SKILL_ROOT)
    parser.add_argument("--memory-path", type=Path, default=None)
    parser.add_argument("--task-names-file", action="append", type=Path, default=None)
    parser.add_argument("--include-task-name", action="append", default=None)
    parser.add_argument("--n-tasks", type=int, default=None)
    parser.add_argument("--concurrency", type=int, default=int(os.getenv("E2B_CONCURRENCY", "1")))
    parser.add_argument("--agent-timeout-sec", type=float, default=None)
    parser.add_argument("--agent-setup-timeout-sec", type=float, default=float(os.getenv("AGENT_SETUP_TIMEOUT_SEC", "1200")))
    parser.add_argument("--e2b-sandbox-timeout-sec", type=int, default=int(os.getenv("E2B_SANDBOX_TIMEOUT_SEC", "3600")))
    parser.add_argument("--override-cpus", type=int, default=int(os.getenv("E2B_OVERRIDE_CPUS", "1")))
    parser.add_argument("--override-memory-mb", type=int, default=int(os.getenv("E2B_OVERRIDE_MEMORY_MB", "4096")))
    parser.add_argument("--override-storage-mb", type=int, default=int(os.getenv("E2B_OVERRIDE_STORAGE_MB", "10240")))
    parser.add_argument("--model-context-window", type=int, default=None)
    parser.add_argument("--model-max-tokens", type=int, default=None)
    parser.add_argument("--result-only", action="store_true")
    parser.add_argument("--force-build", action="store_true")
    parser.add_argument("--keep-sandboxes", action="store_true")
    parser.add_argument("--skip-eval", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args()


def main() -> None:
    load_dotenv(ROOT / ".env", override=False)
    args = parse_args()
    run_dir = DEFAULT_EVO_ROOT / args.run_name
    eval_dir = run_dir / "evaluation"
    eval_dir.mkdir(parents=True, exist_ok=True)
    baseline_job_dir = args.baseline_job_dir.expanduser().resolve()
    eval_job_name = f"{args.run_name}_eval_skills"
    eval_job_dir = (
        args.eval_job_dir.expanduser().resolve()
        if args.eval_job_dir
        else ROOT / "jobs" / eval_job_name
    )
    skill_pack_root = args.skill_root.expanduser().resolve() / args.skill_version_id
    if not args.dry_run and not skill_pack_root.exists():
        raise SystemExit(f"Missing skill pack root: {skill_pack_root}")

    env = os.environ.copy()
    env["SKILL_EVO_RUN_DIR"] = str(run_dir)
    env["PI_SKILL_PACK_ROOT"] = str(skill_pack_root)
    env["PI_USE_SKILL_HARNESS_MEMORY"] = "false"
    env["PI_SKILL_RETRIEVAL_SCOPE"] = "transfer"
    if args.memory_path:
        env["PI_SKILL_HARNESS_MEMORY_PATH"] = str(args.memory_path.expanduser().resolve())

    manifest = {
        "schema_version": 1,
        "run_name": args.run_name,
        "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "mode": "eval_only",
        "dataset": args.dataset,
        "baseline_job_dir": str(baseline_job_dir),
        "eval_job_dir": str(eval_job_dir),
        "skill_version_id": args.skill_version_id,
        "skill_pack_root": str(skill_pack_root),
        "memory_path": env.get("PI_SKILL_HARNESS_MEMORY_PATH"),
        "pi_skill_env": {
            "PI_SKILL_PACK_ROOT": env["PI_SKILL_PACK_ROOT"],
            "PI_USE_SKILL_HARNESS_MEMORY": env["PI_USE_SKILL_HARNESS_MEMORY"],
            "PI_SKILL_RETRIEVAL_SCOPE": env["PI_SKILL_RETRIEVAL_SCOPE"],
        },
    }
    write_json(run_dir / "manifest.json", manifest)

    if not args.skip_eval and args.eval_job_dir is None:
        run_command(
            benchmark_command(args, job_name=eval_job_name),
            env=env,
            dry_run=args.dry_run,
        )
    elif args.eval_job_dir is not None:
        log(f"using existing eval job: {eval_job_dir}")

    report_json = eval_dir / "score_report.json"
    report_md = eval_dir / "score_report.md"
    if args.dry_run:
        log(f"dry-run: report would be written to {report_json}")
        return
    if not (baseline_job_dir / "result.json").exists():
        raise SystemExit(f"Missing baseline result: {baseline_job_dir / 'result.json'}")
    if not (eval_job_dir / "result.json").exists():
        raise SystemExit(f"Missing eval result: {eval_job_dir / 'result.json'}")
    report = compare_jobs(baseline_job_dir, eval_job_dir)
    report.update(
        {
            "run_name": args.run_name,
            "manifest_path": str(run_dir / "manifest.json"),
            "skill_pack_root": str(skill_pack_root),
        }
    )
    write_report(report, report_json, report_md)
    update_version_index(
        version_id=args.skill_version_id,
        skill_pack_root=skill_pack_root,
        run_name=args.run_name,
        run_dir=run_dir,
        baseline_job_dir=baseline_job_dir,
        eval_job_dir=eval_job_dir,
        report_json=report_json,
        report_md=report_md,
        report=report,
    )
    log(f"wrote report: {report_json}")
    log(f"wrote report: {report_md}")


if __name__ == "__main__":
    main()
