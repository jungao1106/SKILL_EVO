#!/usr/bin/env python
import argparse
import json
import os
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from dotenv import load_dotenv


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from evolution.score import compare_jobs, summarize_job, write_report


DEFAULT_DATASET = "swe-bench/swe-bench-verified@2"
DEFAULT_EVO_ROOT = ROOT / "analysis" / "evolution"
POLICY_ROOT = ROOT / "evolution" / "policies"


def utc_stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")


def log(message: str) -> None:
    print(f"[skill-evo] {message}", flush=True)


def run_command(
    command: list[str],
    *,
    env: dict[str, str],
    dry_run: bool,
    cwd: Path = ROOT,
) -> None:
    rendered = " ".join(command)
    log(rendered)
    if dry_run:
        return
    subprocess.run(command, cwd=cwd, env=env, check=True)


def read_active_version(memory_path: Path) -> str:
    data = json.loads(memory_path.read_text(errors="replace"))
    version_id = data.get("active_version")
    if not isinstance(version_id, str) or not version_id:
        raise RuntimeError(f"Memory has no active_version: {memory_path}")
    return version_id


def copy_default_policies(run_dir: Path) -> dict[str, str]:
    training_dir = run_dir / "training"
    training_dir.mkdir(parents=True, exist_ok=True)
    outputs: dict[str, str] = {}
    for name in ("curator_policy.md", "reward_policy.md"):
        src = POLICY_ROOT / name
        dst = training_dir / name
        shutil.copyfile(src, dst)
        outputs[name] = str(dst)
    return outputs


def task_args(args: argparse.Namespace) -> list[str]:
    values: list[str] = []
    if args.n_tasks is not None:
        values.extend(["--n-tasks", str(args.n_tasks)])
    for task_name in args.include_task_name or []:
        values.extend(["--include-task-name", task_name])
    for task_file in args.task_names_file or []:
        values.extend(["--task-names-file", str(task_file)])
    return values


def benchmark_command(
    args: argparse.Namespace,
    *,
    job_name: str,
    use_skills: bool,
) -> list[str]:
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
    command.append("--use-skills" if use_skills else "--no-skills")
    return command


def update_memory_command(
    args: argparse.Namespace,
    *,
    baseline_job_dir: Path,
    memory_path: Path,
    task_skill_dir: Path,
    generated_skill_dir: Path,
    version_id: str,
) -> list[str]:
    command = [
        sys.executable,
        str(ROOT / "scripts" / "update_skill_harness_memory.py"),
        "--job-dir",
        str(baseline_job_dir),
        "--out",
        str(memory_path),
        "--version-id",
        version_id,
        "--task-skill-dir",
        str(task_skill_dir),
        "--generated-skill-dir",
        str(generated_skill_dir),
    ]
    if args.summarize_with_backbone:
        command.append("--summarize-with-backbone")
        command.extend(["--llm-max-tokens", str(args.llm_max_tokens)])
        command.extend(["--skill-resource-max-chars", str(args.skill_resource_max_chars)])
        command.extend(
            [
                "--skill-resource-max-total-chars",
                str(args.skill_resource_max_total_chars),
            ]
        )
    return command


def write_manifest(run_dir: Path, manifest: dict[str, Any]) -> None:
    path = run_dir / "manifest.json"
    path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Run the v2 skill-evolution loop directly on SWE-bench Verified: "
            "baseline -> train five-stage skills -> eval -> score report."
        )
    )
    parser.add_argument("--run-name", default=None)
    parser.add_argument("--dataset", default=os.getenv("HARBOR_DATASET", DEFAULT_DATASET))
    parser.add_argument("--provider", choices=["openai", "tinker"], default=os.getenv("LLM_PROVIDER", "openai"))
    parser.add_argument("--provider-base-url", default=os.getenv("PROVIDER_BASE_URL"))
    parser.add_argument("--provider-model", default=os.getenv("PROVIDER_MODEL"))
    parser.add_argument("--provider-api-key", default=os.getenv("PROVIDER_API_KEY"))
    parser.add_argument("--provider-api", default=os.getenv("PROVIDER_API"))
    parser.add_argument("--n-tasks", type=int, default=None)
    parser.add_argument("--include-task-name", action="append", default=None)
    parser.add_argument("--task-names-file", action="append", type=Path, default=None)
    parser.add_argument("--concurrency", type=int, default=int(os.getenv("E2B_CONCURRENCY", "1")))
    parser.add_argument("--baseline-job-dir", type=Path, default=None)
    parser.add_argument("--eval-job-dir", type=Path, default=None)
    parser.add_argument("--skip-baseline", action="store_true")
    parser.add_argument("--skip-training", action="store_true")
    parser.add_argument("--skip-eval", action="store_true")
    parser.add_argument("--summarize-with-backbone", action="store_true")
    parser.add_argument("--llm-max-tokens", type=int, default=2600)
    parser.add_argument("--skill-resource-max-chars", type=int, default=1200)
    parser.add_argument("--skill-resource-max-total-chars", type=int, default=18000)
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
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args()


def main() -> None:
    load_dotenv(ROOT / ".env", override=False)
    args = parse_args()

    run_name = args.run_name or f"verified_testset_{utc_stamp()}"
    run_dir = DEFAULT_EVO_ROOT / run_name
    training_dir = run_dir / "training"
    eval_dir = run_dir / "evaluation"
    training_dir.mkdir(parents=True, exist_ok=True)
    eval_dir.mkdir(parents=True, exist_ok=True)
    policy_paths = copy_default_policies(run_dir)

    env = os.environ.copy()
    env["SKILL_EVO_RUN_DIR"] = str(run_dir)

    baseline_job_name = f"{run_name}_baseline_noskills"
    eval_job_name = f"{run_name}_eval_skills"
    baseline_job_dir = (
        args.baseline_job_dir.expanduser().resolve()
        if args.baseline_job_dir
        else ROOT / "jobs" / baseline_job_name
    )
    eval_job_dir = (
        args.eval_job_dir.expanduser().resolve()
        if args.eval_job_dir
        else ROOT / "jobs" / eval_job_name
    )
    memory_path = training_dir / "skill_harness_memory.json"
    task_skill_dir = training_dir / "task_skill_cards"
    generated_skill_dir = training_dir / "skill_packs"
    version_id = "v0001"

    manifest: dict[str, Any] = {
        "schema_version": 1,
        "run_name": run_name,
        "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "dataset": args.dataset,
        "provider": args.provider,
        "mode": "direct_test_set_training_and_eval",
        "policy_paths": policy_paths,
        "baseline_job_dir": str(baseline_job_dir),
        "eval_job_dir": str(eval_job_dir),
        "memory_path": str(memory_path),
        "task_skill_dir": str(task_skill_dir),
        "generated_skill_dir": str(generated_skill_dir),
        "version_id": version_id,
        "dry_run": args.dry_run,
    }
    write_manifest(run_dir, manifest)

    if not args.skip_baseline and args.baseline_job_dir is None:
        run_command(
            benchmark_command(args, job_name=baseline_job_name, use_skills=False),
            env=env,
            dry_run=args.dry_run,
        )
    elif args.baseline_job_dir is not None:
        log(f"using existing baseline job: {baseline_job_dir}")

    if not args.skip_training:
        if args.dry_run:
            log("dry-run: would build five-stage skill memory from baseline job")
        else:
            if not (baseline_job_dir / "result.json").exists():
                raise SystemExit(f"Missing baseline result: {baseline_job_dir / 'result.json'}")
        run_command(
            update_memory_command(
                args,
                baseline_job_dir=baseline_job_dir,
                memory_path=memory_path,
                task_skill_dir=task_skill_dir,
                generated_skill_dir=generated_skill_dir,
                version_id=version_id,
            ),
            env=env,
            dry_run=args.dry_run,
        )

    active_version = version_id
    if not args.dry_run and memory_path.exists():
        active_version = read_active_version(memory_path)
    skill_pack_root = generated_skill_dir / active_version
    env["PI_SKILL_HARNESS_MEMORY_PATH"] = str(memory_path)
    env["PI_TASK_STAGE_SKILLS_ROOT"] = str(skill_pack_root)
    env["PI_USE_SKILL_HARNESS_MEMORY"] = "true"

    manifest.update(
        {
            "active_version": active_version,
            "skill_pack_root": str(skill_pack_root),
            "eval_env": {
                "PI_SKILL_HARNESS_MEMORY_PATH": env["PI_SKILL_HARNESS_MEMORY_PATH"],
                "PI_TASK_STAGE_SKILLS_ROOT": env["PI_TASK_STAGE_SKILLS_ROOT"],
                "PI_USE_SKILL_HARNESS_MEMORY": env["PI_USE_SKILL_HARNESS_MEMORY"],
            },
        }
    )
    write_manifest(run_dir, manifest)

    if not args.skip_eval and args.eval_job_dir is None:
        if not args.dry_run and not skill_pack_root.exists():
            raise SystemExit(f"Missing skill pack root: {skill_pack_root}")
        run_command(
            benchmark_command(args, job_name=eval_job_name, use_skills=True),
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
        raise SystemExit(f"Missing baseline result for report: {baseline_job_dir / 'result.json'}")
    if not (eval_job_dir / "result.json").exists():
        if args.skip_eval:
            baseline_summary = summarize_job(baseline_job_dir)
            report = {
                "baseline": baseline_summary,
                "evaluation": {
                    "job_dir": str(eval_job_dir),
                    "job_name": eval_job_dir.name,
                    "n_trials": 0,
                    "n_errors": 0,
                    "resolved": 0,
                    "mean_reward": None,
                    "tasks": [],
                },
                "mean_delta": None,
                "resolved_delta": None,
                "tasks": [],
                "note": "eval skipped or missing",
            }
        else:
            raise SystemExit(f"Missing eval result for report: {eval_job_dir / 'result.json'}")
    else:
        report = compare_jobs(baseline_job_dir, eval_job_dir)
    report.update(
        {
            "run_name": run_name,
            "manifest_path": str(run_dir / "manifest.json"),
            "memory_path": str(memory_path),
            "skill_pack_root": str(skill_pack_root),
        }
    )
    write_report(report, report_json, report_md)
    log(f"wrote report: {report_json}")
    log(f"wrote report: {report_md}")


if __name__ == "__main__":
    main()
