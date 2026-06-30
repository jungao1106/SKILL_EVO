#!/usr/bin/env python
import argparse
import json
import os
import re
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
from providers import ensure_macaron_attribution_header, ensure_reasoning_effort_none


DEFAULT_DATASET = "swe-bench/swe-bench-verified@2"
DEFAULT_EVO_ROOT = ROOT / "run_logs" / "evolution"
DEFAULT_SKILL_ARCHIVE_ROOT = ROOT / "skills" / "accepted"
POLICY_ROOT = ROOT / "evolution" / "policies"
SKILL_VERSION_INDEX = "VERSIONS.json"
VERSION_ID_RE = re.compile(r"^v(\d{4,})$")


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


def _version_number(version_id: str) -> int | None:
    match = VERSION_ID_RE.match(version_id)
    if not match:
        return None
    return int(match.group(1))


def _index_versions(index_path: Path) -> set[str]:
    if not index_path.exists():
        return set()
    try:
        data = json.loads(index_path.read_text(errors="replace"))
    except (OSError, ValueError):
        return set()
    versions = data.get("versions") if isinstance(data, dict) else None
    if isinstance(versions, dict):
        return {key for key in versions if _version_number(str(key)) is not None}
    if isinstance(versions, list):
        found = set()
        for item in versions:
            if isinstance(item, dict):
                version_id = item.get("version_id")
                if isinstance(version_id, str) and _version_number(version_id) is not None:
                    found.add(version_id)
        return found
    return set()


def archived_skill_versions(skill_archive_root: Path) -> set[str]:
    versions: set[str] = set()
    if skill_archive_root.exists():
        for child in skill_archive_root.iterdir():
            if child.is_dir() and _version_number(child.name) is not None:
                versions.add(child.name)
    versions.update(_index_versions(skill_archive_root / SKILL_VERSION_INDEX))

    try:
        relative_root = skill_archive_root.relative_to(ROOT)
    except ValueError:
        return versions

    try:
        proc = subprocess.run(
            ["git", "ls-files", relative_root.as_posix()],
            cwd=ROOT,
            check=False,
            capture_output=True,
            text=True,
        )
    except OSError:
        return versions
    if proc.returncode != 0:
        return versions
    for line in proc.stdout.splitlines():
        try:
            relative = Path(line).relative_to(relative_root)
        except ValueError:
            continue
        if not relative.parts:
            continue
        version_id = relative.parts[0]
        if _version_number(version_id) is not None:
            versions.add(version_id)
    return versions


def next_skill_version_id(skill_archive_root: Path) -> str:
    numbers = [
        number
        for number in (_version_number(version_id) for version_id in archived_skill_versions(skill_archive_root))
        if number is not None
    ]
    next_number = max(numbers) + 1 if numbers else 0
    return f"v{next_number:04d}"


def is_full_dataset_selection(args: argparse.Namespace) -> bool:
    return (
        args.n_tasks is None
        and not args.include_task_name
        and not args.task_names_file
    )


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
    task_evidence_dir: Path,
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
        str(task_evidence_dir),
        "--generated-skill-dir",
        str(generated_skill_dir),
    ]
    if args.append_skill_version:
        command.append("--append-generated-skill-files")
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


def write_skill_version_index(
    index_path: Path,
    *,
    version_id: str,
    run_name: str,
    run_dir: Path,
    args: argparse.Namespace,
    baseline_job_dir: Path,
    eval_job_dir: Path,
    memory_path: Path,
    skill_pack_root: Path,
    report_json: Path,
    report_md: Path,
    report: dict[str, Any],
) -> None:
    index_path.parent.mkdir(parents=True, exist_ok=True)
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
        versions = index.get("versions")
        if not isinstance(versions, dict):
            versions = {}
            index["versions"] = versions

        existing_version = versions.get(version_id)
        if not isinstance(existing_version, dict):
            existing_version = {}
        shard_runs = existing_version.get("shard_runs")
        if not isinstance(shard_runs, list):
            shard_runs = []
        shard_run = {
            "run_name": run_name,
            "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "task_selection": "all_dataset_tasks" if is_full_dataset_selection(args) else "filtered_tasks",
            "run_dir": str(run_dir),
            "baseline_job_dir": str(baseline_job_dir),
            "eval_job_dir": str(eval_job_dir),
            "memory_path": str(memory_path),
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
        shard_runs = [
            item
            for item in shard_runs
            if not isinstance(item, dict) or item.get("run_name") != run_name
        ]
        shard_runs.append(shard_run)
        total_eval_trials = sum(
            int((item.get("metrics") or {}).get("eval_trials") or 0)
            for item in shard_runs
            if isinstance(item, dict)
        )
        total_eval_errors = sum(
            int((item.get("metrics") or {}).get("eval_errors") or 0)
            for item in shard_runs
            if isinstance(item, dict)
        )
        total_eval_resolved = sum(
            int((item.get("metrics") or {}).get("eval_resolved") or 0)
            for item in shard_runs
            if isinstance(item, dict)
        )
        versions[version_id] = {
            **existing_version,
            "version_id": version_id,
            "run_name": existing_version.get("run_name") or run_name,
            "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "dataset": args.dataset,
            "provider": args.provider,
            "mode": "swebench_verified_train_equals_eval",
            "skill_pack_root": str(skill_pack_root),
            "shard_runs": shard_runs,
            "aggregate": {
                "shards": len(shard_runs),
                "eval_trials": total_eval_trials,
                "eval_errors": total_eval_errors,
                "eval_resolved": total_eval_resolved,
            },
        }
        index["schema_version"] = 1
        index["active_version"] = version_id
        index["updated_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
        tmp_path = index_path.with_name(f".{index_path.name}.{os.getpid()}.tmp")
        tmp_path.write_text(json.dumps(index, ensure_ascii=False, indent=2) + "\n")
        tmp_path.replace(index_path)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Run the v2 skill-evolution loop directly on SWE-bench Verified: "
            "baseline -> build task evidence and accepted skills -> eval -> score report."
        )
    )
    parser.add_argument("--run-name", default=None)
    parser.add_argument("--dataset", default=os.getenv("HARBOR_DATASET", DEFAULT_DATASET))
    parser.add_argument("--provider", choices=["openai", "tinker"], default=os.getenv("LLM_PROVIDER", "openai"))
    parser.add_argument("--provider-base-url", default=os.getenv("PROVIDER_BASE_URL"))
    parser.add_argument("--provider-model", default=os.getenv("PROVIDER_MODEL"))
    parser.add_argument("--provider-api-key", default=os.getenv("PROVIDER_API_KEY"))
    parser.add_argument("--provider-api", default=os.getenv("PROVIDER_API"))
    parser.add_argument(
        "--all-verified",
        action="store_true",
        help="Explicitly use every task in the dataset for both training and eval.",
    )
    parser.add_argument("--n-tasks", type=int, default=None)
    parser.add_argument("--include-task-name", action="append", default=None)
    parser.add_argument("--task-names-file", action="append", type=Path, default=None)
    parser.add_argument("--concurrency", type=int, default=int(os.getenv("E2B_CONCURRENCY", "1")))
    parser.add_argument("--baseline-job-dir", type=Path, default=None)
    parser.add_argument("--eval-job-dir", type=Path, default=None)
    parser.add_argument("--skip-baseline", action="store_true")
    parser.add_argument("--skip-training", action="store_true")
    parser.add_argument("--skip-eval", action="store_true")
    parser.add_argument(
        "--skill-archive-root",
        type=Path,
        default=DEFAULT_SKILL_ARCHIVE_ROOT,
        help="Root directory where accepted skill artifacts are versioned.",
    )
    parser.add_argument(
        "--skill-version-id",
        default=None,
        help="Archive version id such as v0012. Defaults to the next unused skills/accepted version.",
    )
    parser.add_argument(
        "--overwrite-skill-version",
        action="store_true",
        help="Allow replacing an existing archived skill version directory.",
    )
    parser.add_argument(
        "--append-skill-version",
        action="store_true",
        help=(
            "Append this shard's accepted artifacts into an existing shared "
            "iteration version directory instead of treating an existing version as an error."
        ),
    )
    parser.add_argument(
        "--summarize-with-backbone",
        dest="summarize_with_backbone",
        action="store_true",
        default=True,
        help="Use the trace-recorded provider/model to summarize task evidence. Enabled by default.",
    )
    parser.add_argument(
        "--no-summarize-with-backbone",
        dest="summarize_with_backbone",
        action="store_false",
        help="Disable model summarization and use heuristic task evidence extraction only.",
    )
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
    if args.all_verified and not is_full_dataset_selection(args):
        raise SystemExit(
            "--all-verified means no task filter: remove --n-tasks, "
            "--include-task-name, and --task-names-file."
        )

    run_name = args.run_name or f"verified_testset_{utc_stamp()}"
    run_dir = DEFAULT_EVO_ROOT / run_name
    training_dir = run_dir / "training"
    eval_dir = run_dir / "evaluation"
    training_dir.mkdir(parents=True, exist_ok=True)
    eval_dir.mkdir(parents=True, exist_ok=True)
    policy_paths = copy_default_policies(run_dir)

    env = os.environ.copy()
    env["SKILL_EVO_RUN_DIR"] = str(run_dir)
    ensure_macaron_attribution_header(args.provider_base_url or env.get("OPENAI_COMPAT_BASE_URL"), env)
    ensure_reasoning_effort_none(
        args.provider_base_url or env.get("OPENAI_COMPAT_BASE_URL"),
        env,
        env_prefix="OPENAI_COMPAT",
    )

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
    task_evidence_dir = training_dir / "task_evidence_cards"
    skill_archive_root = args.skill_archive_root.expanduser().resolve()
    generated_skill_dir = skill_archive_root
    version_id = args.skill_version_id or next_skill_version_id(skill_archive_root)
    existing_skill_versions = archived_skill_versions(skill_archive_root)
    if (
        version_id in existing_skill_versions
        and not args.overwrite_skill_version
        and not args.append_skill_version
    ):
        message = (
            f"Skill archive version already exists: {skill_archive_root / version_id}. "
            "Use --skill-version-id with a new vXXXX, pass --overwrite-skill-version, "
            "or pass --append-skill-version for sharded writes into one iteration."
        )
        if args.dry_run:
            log(f"dry-run warning: {message}")
        else:
            raise SystemExit(message)
    skill_version_index = skill_archive_root / SKILL_VERSION_INDEX

    manifest: dict[str, Any] = {
        "schema_version": 1,
        "run_name": run_name,
        "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "dataset": args.dataset,
        "provider": args.provider,
        "mode": "direct_test_set_training_and_eval",
        "train_eval_split": "same_tasks",
        "task_selection": "all_dataset_tasks" if is_full_dataset_selection(args) else "filtered_tasks",
        "policy_paths": policy_paths,
        "baseline_job_dir": str(baseline_job_dir),
        "eval_job_dir": str(eval_job_dir),
        "memory_path": str(memory_path),
        "task_evidence_dir": str(task_evidence_dir),
        "skill_archive_root": str(skill_archive_root),
        "generated_skill_dir": str(generated_skill_dir),
        "skill_version_index": str(skill_version_index),
        "version_id": version_id,
        "append_skill_version": args.append_skill_version,
        "dry_run": args.dry_run,
    }
    write_manifest(run_dir, manifest)
    log(f"skill archive version={version_id} root={skill_archive_root}")

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
            log("dry-run: would build task evidence memory and accepted skill artifacts from baseline job")
        else:
            if not (baseline_job_dir / "result.json").exists():
                raise SystemExit(f"Missing baseline result: {baseline_job_dir / 'result.json'}")
        run_command(
            update_memory_command(
                args,
                baseline_job_dir=baseline_job_dir,
                memory_path=memory_path,
                task_evidence_dir=task_evidence_dir,
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
    env["PI_SKILL_PACK_ROOT"] = str(skill_pack_root)
    env["PI_USE_SKILL_HARNESS_MEMORY"] = "false"
    env["PI_SKILL_RETRIEVAL_SCOPE"] = "transfer"

    manifest.update(
        {
            "active_version": active_version,
            "skill_pack_root": str(skill_pack_root),
            "skill_archive_root": str(skill_archive_root),
            "skill_version_index": str(skill_version_index),
            "eval_env": {
                "PI_SKILL_HARNESS_MEMORY_PATH": env["PI_SKILL_HARNESS_MEMORY_PATH"],
                "PI_SKILL_PACK_ROOT": env["PI_SKILL_PACK_ROOT"],
                "PI_USE_SKILL_HARNESS_MEMORY": env["PI_USE_SKILL_HARNESS_MEMORY"],
                "PI_SKILL_RETRIEVAL_SCOPE": env["PI_SKILL_RETRIEVAL_SCOPE"],
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
            "skill_archive_root": str(skill_archive_root),
            "skill_pack_root": str(skill_pack_root),
            "skill_version_index": str(skill_version_index),
        }
    )
    write_report(report, report_json, report_md)
    write_skill_version_index(
        skill_version_index,
        version_id=active_version,
        run_name=run_name,
        run_dir=run_dir,
        args=args,
        baseline_job_dir=baseline_job_dir,
        eval_job_dir=eval_job_dir,
        memory_path=memory_path,
        skill_pack_root=skill_pack_root,
        report_json=report_json,
        report_md=report_md,
        report=report,
    )
    log(f"wrote report: {report_json}")
    log(f"wrote report: {report_md}")
    log(f"updated skill version index: {skill_version_index}")


if __name__ == "__main__":
    main()
