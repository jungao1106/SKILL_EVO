#!/usr/bin/env python
import argparse
import json
import shlex
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_PYTHON = "/vePFS-Mindverse/user/intern/jungao/Marcronv1_SWE/.venv312/bin/python"
DEFAULT_ENV_FILE = "/vePFS-Mindverse/user/intern/jungao/Marcronv1_SWE/.env"
DEFAULT_DATASET = "swe-bench/swe-bench-verified@2"
DEFAULT_BASELINE_JOB_GLOB = "jobs/skill_evo_verified_glm51_full_v0001_*_baseline_noskills"
DEFAULT_TASK_FILE_GLOB = "run_logs/skill_evo_shards/skill_evo_verified_glm51_full_v0001_[0-9][0-9][0-9]_[0-9][0-9][0-9]_20260602_1921.txt"


def utc_tag() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d_%H%M")


def shell_join(parts: list[str]) -> str:
    return " ".join(shlex.quote(part) for part in parts)


def env_prefix(env_file: str) -> str:
    return (
        f"set -a; . {shlex.quote(env_file)}; set +a; "
        "export LLM_PROVIDER=openai; "
        'export OPENAI_COMPAT_API_KEY="$NOVITA_API_KEY"; '
        'export OPENAI_COMPAT_BASE_URL="${NOVITA_BASE_URL:-https://api.novita.ai/v3/openai}"; '
        'export OPENAI_COMPAT_MODEL="${NOVITA_MODEL:-zai-org/glm-5.2}"; '
        "export OPENAI_COMPAT_API=openai-completions; "
        'export OPENAI_COMPAT_CONTEXT_WINDOW="${NOVITA_CONTEXT_WINDOW:-128000}"; '
        'export OPENAI_COMPAT_MAX_TOKENS="${NOVITA_MAX_TOKENS:-32000}"; '
        'export OPENAI_COMPAT_REASONING_EFFORT="${OPENAI_COMPAT_REASONING_EFFORT:-none}"; '
        'export OPENAI_COMPAT_ENABLE_THINKING="${OPENAI_COMPAT_ENABLE_THINKING:-false}"; '
        'case "$OPENAI_COMPAT_BASE_URL" in *macaron*) '
        'export CLAUDE_CODE_ATTRIBUTION_HEADER="${CLAUDE_CODE_ATTRIBUTION_HEADER:-0}";; '
        "esac; "
    )


def expand_glob(pattern: str) -> list[Path]:
    import glob

    return sorted(Path(path).resolve() for path in glob.glob(pattern))


def version_number(version_id: str) -> int:
    if not version_id.startswith("v"):
        raise ValueError(f"Invalid version id: {version_id}")
    return int(version_id[1:])


def version_id(number: int) -> str:
    return f"v{number:04d}"


def task_file_range_label(path: Path, fallback_index: int) -> str:
    import re

    match = re.search(r"(?<!\d)(\d{3})_(\d{3})(?!\d)", path.stem)
    if match:
        return f"{match.group(1)}_{match.group(2)}"
    return f"shard{fallback_index:02d}"


def job_trial_count(job: Path) -> int:
    return len([path for path in job.glob("*/result.json") if path.parent != job])


def task_file_count(path: Path) -> int:
    return sum(1 for line in path.read_text(errors="replace").splitlines() if line.strip())


def tmux_session_exists(session: str) -> bool:
    proc = subprocess.run(
        ["tmux", "has-session", "-t", session],
        cwd=ROOT,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    )
    return proc.returncode == 0


def wait_for_eval_jobs(
    *,
    prefix: str,
    expected_trial_counts: dict[str, int],
    sessions: list[str],
    poll_sec: float,
    timeout_sec: float,
) -> None:
    deadline = time.time() + timeout_sec
    while time.time() < deadline:
        jobs = [
            ROOT / "jobs" / name
            for name in sorted(expected_trial_counts)
            if (ROOT / "jobs" / name).exists()
        ]
        complete = [
            job
            for job in jobs
            if (job / "result.json").exists()
            and job_trial_count(job) >= expected_trial_counts[job.name]
        ]
        counts = ", ".join(
            f"{job.name}:{job_trial_count(job)}/{expected_trial_counts[job.name]}"
            for job in jobs
        )
        print(f"[from-noskills] {prefix} complete={len(complete)}/{len(expected_trial_counts)} trial_counts=[{counts}]", flush=True)
        if len(complete) >= len(expected_trial_counts):
            return
        if sessions and not any(tmux_session_exists(session) for session in sessions):
            missing = sorted(set(expected_trial_counts) - {job.name for job in jobs})
            raise RuntimeError(
                "All initial eval tmux sessions exited before completion. "
                f"Missing eval job dirs: {missing}"
            )
        time.sleep(poll_sec)
    raise TimeoutError(f"Timed out waiting for {len(expected_trial_counts)} complete eval jobs matching {prefix}")


def launch_tmux(session: str, command_text: str, *, dry_run: bool) -> None:
    print(f"[from-noskills] {session}: {command_text}", flush=True)
    if dry_run:
        return
    subprocess.run(["tmux", "new-session", "-d", "-s", session, command_text], cwd=ROOT, check=True)


def launch_initial_skill_round(
    *,
    args: argparse.Namespace,
    version: str,
    tag: str,
    baseline_jobs: list[Path],
    task_files: list[Path],
) -> tuple[list[str], dict[str, int]]:
    if len(baseline_jobs) != len(task_files):
        raise SystemExit("baseline job shard count must match task file count")
    sessions: list[str] = []
    expected_trial_counts: dict[str, int] = {}
    for index, (baseline_job, task_file) in enumerate(zip(baseline_jobs, task_files), start=1):
        label = task_file_range_label(task_file, index)
        run_name = f"{args.initial_job_prefix}_{version}_{label}_c{args.concurrency}_t{int(args.agent_timeout_sec)}_{tag}"
        expected_trial_counts[f"{run_name}_eval_skills"] = task_file_count(task_file)
        command = [
            args.python,
            "scripts/run_skill_evo_verified.py",
            "--run-name",
            run_name,
            "--dataset",
            args.dataset,
            "--provider",
            "openai",
            "--baseline-job-dir",
            str(baseline_job),
            "--task-names-file",
            str(task_file),
            "--skill-version-id",
            version,
            "--append-skill-version",
            "--summarize-with-backbone",
            "--concurrency",
            str(args.concurrency),
            "--agent-timeout-sec",
            str(args.agent_timeout_sec),
            "--agent-setup-timeout-sec",
            str(args.agent_setup_timeout_sec),
        ]
        log_path = ROOT / "run_logs" / "auto_iterations" / f"{run_name}.session.log"
        command_text = (
            f"cd {shlex.quote(str(ROOT))}; mkdir -p {shlex.quote(str(log_path.parent))}; "
            f"{env_prefix(args.env_file)}{shell_join(command)} "
            f"> {shlex.quote(str(log_path))} 2>&1"
        )
        launch_tmux(run_name, command_text, dry_run=args.dry_run)
        sessions.append(run_name)
        if not args.dry_run and args.stagger_sec > 0 and index < len(task_files):
            time.sleep(args.stagger_sec)
    return sessions, expected_trial_counts


def run_auto_iterations(
    *,
    args: argparse.Namespace,
    start_version: str,
    target_version: str,
    initial_eval_glob: str,
) -> None:
    command = [
        args.python,
        "scripts/run_skill_evo_auto_iterations.py",
        "--start-version",
        start_version,
        "--target-version",
        target_version,
        "--baseline-job-glob",
        args.baseline_job_glob,
        "--start-eval-job-glob",
        initial_eval_glob,
        "--task-file-glob",
        args.task_file_glob,
        "--python",
        args.python,
        "--env-file",
        args.env_file,
        "--job-prefix",
        args.auto_job_prefix,
        "--concurrency",
        str(args.concurrency),
        "--agent-timeout-sec",
        str(args.agent_timeout_sec),
        "--agent-setup-timeout-sec",
        str(args.agent_setup_timeout_sec),
        "--stagger-sec",
        str(args.stagger_sec),
        "--monitor-timeout-sec",
        str(args.monitor_timeout_sec),
        "--poll-sec",
        str(args.poll_sec),
    ]
    if args.monitor:
        command.append("--monitor")
    if args.apply_policy:
        command.append("--apply-policy")
    if args.overwrite:
        command.append("--overwrite")
    print(f"[from-noskills] auto-iterations: {shell_join(command)}", flush=True)
    if not args.dry_run:
        subprocess.run(command, cwd=ROOT, check=True)


def write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True) + "\n")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Start a skill-evolution chain from existing no-skills baseline shards, then continue reward-agent auto iterations."
    )
    parser.add_argument("--start-version", default="v0006")
    parser.add_argument("--rounds", type=int, default=5)
    parser.add_argument("--dataset", default=DEFAULT_DATASET)
    parser.add_argument("--baseline-job-glob", default=DEFAULT_BASELINE_JOB_GLOB)
    parser.add_argument("--task-file-glob", default=DEFAULT_TASK_FILE_GLOB)
    parser.add_argument("--initial-job-prefix", default="skill_evo_reward_agent_from_noskills_glm51")
    parser.add_argument("--auto-job-prefix", default="skill_evo_reward_agent_auto_glm51")
    parser.add_argument("--python", default=DEFAULT_PYTHON)
    parser.add_argument("--env-file", default=DEFAULT_ENV_FILE)
    parser.add_argument("--concurrency", type=int, default=20)
    parser.add_argument("--agent-timeout-sec", type=float, default=3600)
    parser.add_argument("--agent-setup-timeout-sec", type=float, default=1200)
    parser.add_argument("--stagger-sec", type=float, default=120)
    parser.add_argument("--poll-sec", type=float, default=120)
    parser.add_argument("--monitor-timeout-sec", type=float, default=172800)
    parser.add_argument("--date-tag", default="")
    parser.add_argument("--monitor", action="store_true")
    parser.add_argument("--apply-policy", action="store_true")
    parser.add_argument("--overwrite", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    start_num = version_number(args.start_version)
    if args.rounds < 1:
        raise SystemExit("--rounds must be positive")
    target_version = version_id(start_num + args.rounds - 1)
    tag = args.date_tag or utc_tag()
    baseline_jobs = expand_glob(args.baseline_job_glob)
    task_files = expand_glob(args.task_file_glob)
    if not baseline_jobs:
        raise SystemExit(f"No baseline jobs matched {args.baseline_job_glob}")
    if not task_files:
        raise SystemExit(f"No task files matched {args.task_file_glob}")

    manifest_path = ROOT / "run_logs" / "auto_iterations" / f"{args.initial_job_prefix}_{args.start_version}_to_{target_version}_{tag}_manifest.json"
    manifest: dict[str, Any] = {
        "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "mode": "from_existing_noskills_baseline_then_reward_agent_auto_iterations",
        "start_version": args.start_version,
        "target_version": target_version,
        "rounds": args.rounds,
        "baseline_jobs": [str(path) for path in baseline_jobs],
        "task_files": [str(path) for path in task_files],
        "initial_job_prefix": args.initial_job_prefix,
        "auto_job_prefix": args.auto_job_prefix,
        "date_tag": tag,
        "dry_run": args.dry_run,
    }
    write_json(manifest_path, manifest)

    sessions, expected_trial_counts = launch_initial_skill_round(
        args=args,
        version=args.start_version,
        tag=tag,
        baseline_jobs=baseline_jobs,
        task_files=task_files,
    )
    initial_eval_glob = f"jobs/{args.initial_job_prefix}_{args.start_version}_*_{tag}_eval_skills"
    manifest["initial_eval_glob"] = initial_eval_glob
    manifest["initial_tmux_sessions"] = sessions
    manifest["initial_expected_trial_counts"] = expected_trial_counts
    write_json(manifest_path, manifest)

    if args.monitor and not args.dry_run:
        wait_for_eval_jobs(
            prefix=f"{args.initial_job_prefix}_{args.start_version}_",
            expected_trial_counts=expected_trial_counts,
            sessions=sessions,
            poll_sec=args.poll_sec,
            timeout_sec=args.monitor_timeout_sec,
        )

    if args.rounds > 1:
        run_auto_iterations(
            args=args,
            start_version=args.start_version,
            target_version=target_version,
            initial_eval_glob=initial_eval_glob,
        )

    manifest["completed_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    manifest["status"] = "launched" if not args.monitor else "complete"
    write_json(manifest_path, manifest)
    print(f"[from-noskills] wrote {manifest_path}", flush=True)


if __name__ == "__main__":
    main()
