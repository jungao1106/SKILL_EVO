#!/usr/bin/env python
import argparse
import json
import os
import re
import shlex
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_PYTHON = "/vePFS-Mindverse/user/intern/jungao/Marcronv1_SWE/.venv312/bin/python"
DEFAULT_ENV_FILE = "/vePFS-Mindverse/user/intern/jungao/Marcronv1_SWE/.env"
DEFAULT_DATASET = "swe-bench/swe-bench-verified@2"
DEFAULT_BASELINE_GLOB = "jobs/skill_evo_verified_glm51_full_v0001_*_baseline_noskills"
DEFAULT_V0001_EVAL_GLOB = "jobs/skill_evo_verified_glm51_full_v0001_*_eval_skills"
DEFAULT_TASK_FILE_GLOB = "run_logs/skill_evo_shards/skill_evo_verified_glm51_full_v0001_[0-9]*_[0-9]*_20260602_1921.txt"


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


def run(command: list[str], *, dry_run: bool = False) -> None:
    print(shell_join(command), flush=True)
    if not dry_run:
        subprocess.run(command, cwd=ROOT, check=True)


def glob_paths(pattern: str) -> list[Path]:
    return sorted(Path(".").glob(pattern)) if not pattern.startswith("/") else sorted(Path("/").glob(pattern[1:]))


def expand_glob(pattern: str) -> list[Path]:
    import glob

    return sorted(Path(path).resolve() for path in glob.glob(pattern))


def version_number(version_id: str) -> int:
    if not version_id.startswith("v"):
        raise ValueError(f"Invalid version id: {version_id}")
    return int(version_id[1:])


def version_id(number: int) -> str:
    return f"v{number:04d}"


def build_policy_update(
    *,
    python: str,
    iteration_id: str,
    base_label: str,
    previous_label: str,
    current_label: str,
    base_job_glob: str,
    previous_job_glob: str,
    current_job_glob: str,
    current_skill_version: str,
    expected_current_trials: int,
    require_complete_current: bool,
    apply_policy: bool,
    dry_run: bool,
) -> Path:
    out_dir = ROOT / "run_logs" / "policy_updates" / iteration_id
    command = [
        python,
        "scripts/update_policies_from_iteration.py",
        "--iteration-id",
        iteration_id,
        "--base-label",
        base_label,
        "--previous-label",
        previous_label,
        "--current-label",
        current_label,
        "--base-job-glob",
        base_job_glob,
        "--previous-job-glob",
        previous_job_glob,
        "--current-job-glob",
        current_job_glob,
        "--current-skill-version",
        current_skill_version,
        "--out-dir",
        str(out_dir),
    ]
    if expected_current_trials:
        command.extend(["--expected-current-trials", str(expected_current_trials)])
    if require_complete_current:
        command.append("--require-complete-current")
    if apply_policy:
        command.append("--apply")
    run(command, dry_run=dry_run)
    return out_dir


def curate_next_version(
    *,
    python: str,
    previous_version: str,
    next_version: str,
    cases_jsonl: Path,
    overwrite: bool,
    max_cases: int | None,
    dry_run: bool,
) -> None:
    command = [
        python,
        "scripts/curate_next_skill_version.py",
        "--previous-version",
        previous_version,
        "--next-version",
        next_version,
        "--cases-jsonl",
        str(cases_jsonl),
    ]
    if overwrite:
        command.append("--overwrite")
    if max_cases is not None:
        command.extend(["--max-cases", str(max_cases)])
    run(command, dry_run=dry_run)


def eval_command(
    *,
    python: str,
    run_name: str,
    dataset: str,
    baseline_job_dir: Path,
    task_file: Path,
    skill_version: str,
    concurrency: int,
    agent_timeout_sec: float,
    agent_setup_timeout_sec: float,
    memory_path: Path | None,
    eval_job_dir: Path | None = None,
) -> list[str]:
    command = [
        python,
        "scripts/run_skill_evo_eval_only.py",
        "--run-name",
        run_name,
        "--dataset",
        dataset,
        "--provider",
        "openai",
        "--baseline-job-dir",
        str(baseline_job_dir),
        "--task-names-file",
        str(task_file),
        "--skill-version-id",
        skill_version,
        "--concurrency",
        str(concurrency),
        "--agent-timeout-sec",
        str(agent_timeout_sec),
        "--agent-setup-timeout-sec",
        str(agent_setup_timeout_sec),
    ]
    if memory_path is not None:
        command.extend(["--memory-path", str(memory_path)])
    if eval_job_dir is not None:
        command.extend(["--eval-job-dir", str(eval_job_dir)])
    return command


def launch_eval_shards(
    *,
    python: str,
    env_file: str,
    dataset: str,
    job_prefix: str,
    skill_version: str,
    baseline_job_dirs: list[Path],
    task_files: list[Path],
    concurrency: int,
    agent_timeout_sec: float,
    agent_setup_timeout_sec: float,
    stagger_sec: float,
    dry_run: bool,
    no_tmux: bool,
    smoke: bool,
) -> tuple[list[str], dict[str, int]]:
    sessions: list[str] = []
    expected_trial_counts: dict[str, int] = {}
    if len(baseline_job_dirs) != len(task_files):
        raise SystemExit("baseline shard count must match task file count")
    date_tag = utc_tag()
    for index, (baseline_job_dir, task_file) in enumerate(zip(baseline_job_dirs, task_files), start=1):
        range_match = re.search(r"(?<!\d)(\d{3})_(\d{3})(?!\d)", task_file.stem)
        range_label = f"{range_match.group(1)}_{range_match.group(2)}" if range_match else ""
        if not range_label:
            range_label = f"shard{index:02d}"
        run_name = f"{job_prefix}_{skill_version}_{range_label}_c{concurrency}_t{int(agent_timeout_sec)}_{date_tag}"
        expected_trial_counts[f"{run_name}_eval_skills"] = task_file_count(task_file)
        command = eval_command(
            python=python,
            run_name=run_name,
            dataset=dataset,
            baseline_job_dir=baseline_job_dir,
            task_file=task_file,
            skill_version=skill_version,
            concurrency=concurrency,
            agent_timeout_sec=agent_timeout_sec,
            agent_setup_timeout_sec=agent_setup_timeout_sec,
            memory_path=None,
        )
        if smoke or no_tmux:
            launch = command
        else:
            log_path = ROOT / "run_logs" / "auto_iterations" / f"{run_name}.session.log"
            command_text = (
                f"cd {shlex.quote(str(ROOT))}; mkdir -p {shlex.quote(str(log_path.parent))}; "
                f"{env_prefix(env_file)}{shell_join(command)} "
                f"> {shlex.quote(str(log_path))} 2>&1"
            )
            launch = ["tmux", "new-session", "-d", "-s", run_name, command_text]
            sessions.append(run_name)
        print(f"{run_name}: {shell_join(launch)}", flush=True)
        if not dry_run:
            if smoke or no_tmux:
                env_command = f"cd {shlex.quote(str(ROOT))}; {env_prefix(env_file)}{shell_join(command)}"
                subprocess.run(["bash", "-lc", env_command], check=True)
            else:
                subprocess.run(launch, check=True)
                if stagger_sec > 0 and index < len(task_files):
                    time.sleep(stagger_sec)
    return sessions, expected_trial_counts


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


def wait_for_jobs(
    *,
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
        print(f"[monitor] complete={len(complete)}/{len(expected_trial_counts)} trial_counts=[{counts}]", flush=True)
        if len(complete) >= len(expected_trial_counts):
            return
        if sessions and not any(tmux_session_exists(session) for session in sessions):
            missing = sorted(set(expected_trial_counts) - {job.name for job in jobs})
            raise RuntimeError(
                "All eval tmux sessions exited before completion. "
                f"Missing eval job dirs: {missing}"
            )
        time.sleep(poll_sec)
    raise TimeoutError(f"Timed out waiting for {len(expected_trial_counts)} complete eval jobs")


def write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True) + "\n")


def first_task_name_from_job(job_dir: Path) -> str:
    for result_path in sorted(job_dir.glob("*/result.json")):
        try:
            data = json.loads(result_path.read_text(errors="replace"))
        except (OSError, ValueError):
            continue
        task_name = data.get("task_name")
        if isinstance(task_name, str) and task_name:
            return task_name
    raise SystemExit(f"No task_name found in baseline job: {job_dir}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Automate SKILLS_EVO policy updates and skill eval iterations up to a target version."
    )
    parser.add_argument("--start-version", default="v0001")
    parser.add_argument("--target-version", default="v0005")
    parser.add_argument("--baseline-job-glob", default=DEFAULT_BASELINE_GLOB)
    parser.add_argument("--start-eval-job-glob", default=DEFAULT_V0001_EVAL_GLOB)
    parser.add_argument("--start-previous-job-glob", default=None)
    parser.add_argument("--start-previous-label", default=None)
    parser.add_argument("--task-file-glob", default=DEFAULT_TASK_FILE_GLOB)
    parser.add_argument("--dataset", default=DEFAULT_DATASET)
    parser.add_argument("--python", default=DEFAULT_PYTHON)
    parser.add_argument("--env-file", default=DEFAULT_ENV_FILE)
    parser.add_argument("--job-prefix", default="skill_evo_auto")
    parser.add_argument("--concurrency", type=int, default=20)
    parser.add_argument("--agent-timeout-sec", type=float, default=3600)
    parser.add_argument("--agent-setup-timeout-sec", type=float, default=1200)
    parser.add_argument("--stagger-sec", type=float, default=120)
    parser.add_argument("--monitor", action="store_true")
    parser.add_argument("--monitor-timeout-sec", type=float, default=86400)
    parser.add_argument("--poll-sec", type=float, default=120)
    parser.add_argument("--apply-policy", action="store_true")
    parser.add_argument("--overwrite", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--smoke", action="store_true")
    parser.add_argument("--smoke-task-file", type=Path, default=None)
    parser.add_argument("--smoke-baseline-job-dir", type=Path, default=None)
    parser.add_argument("--max-curation-cases", type=int, default=None)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    start_num = version_number(args.start_version)
    target_num = version_number(args.target_version)
    if target_num <= start_num:
        raise SystemExit("--target-version must be greater than --start-version")

    baseline_jobs = expand_glob(args.baseline_job_glob)
    if not baseline_jobs:
        raise SystemExit(f"No baseline jobs matched {args.baseline_job_glob}")
    task_files = expand_glob(args.task_file_glob)
    if not task_files:
        raise SystemExit(f"No task files matched {args.task_file_glob}")

    previous_eval_glob = args.start_eval_job_glob
    previous_compare_glob = args.start_previous_job_glob or args.baseline_job_glob
    base_compare_glob = args.baseline_job_glob
    previous_label = args.start_previous_label or "v0_noskills"
    manifest: dict[str, Any] = {
        "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "start_version": args.start_version,
        "target_version": args.target_version,
        "iterations": [],
    }

    if args.smoke:
        if args.smoke_baseline_job_dir is None:
            baseline_jobs = [baseline_jobs[0]]
        else:
            baseline_jobs = [args.smoke_baseline_job_dir.expanduser().resolve()]
        if args.smoke_task_file is None:
            smoke_file = ROOT / "run_logs" / "smoke_tasks_v0002.txt"
            first_task = first_task_name_from_job(baseline_jobs[0])
            smoke_file.write_text(first_task + "\n")
            task_files = [smoke_file]
        else:
            task_files = [args.smoke_task_file.expanduser().resolve()]

    expected_current_trials = sum(task_file_count(path) for path in task_files)

    for number in range(start_num + 1, target_num + 1):
        prev_version = version_id(number - 1)
        curr_version = version_id(number)
        iteration_id = f"{curr_version}_from_{prev_version}"
        policy_dir = build_policy_update(
            python=args.python,
            iteration_id=iteration_id,
            base_label="v0_noskills",
            previous_label=previous_label,
            current_label=prev_version,
            base_job_glob=base_compare_glob,
            previous_job_glob=previous_compare_glob,
            current_job_glob=previous_eval_glob,
            current_skill_version=prev_version,
            expected_current_trials=expected_current_trials,
            require_complete_current=not args.smoke,
            apply_policy=args.apply_policy,
            dry_run=args.dry_run,
        )
        reward_cases_jsonl = policy_dir / "reward_cases.jsonl"
        cases_jsonl = reward_cases_jsonl if reward_cases_jsonl.exists() else policy_dir / "selected_cases.jsonl"
        next_version = f"smoke_{curr_version}" if args.smoke else curr_version
        curate_next_version(
            python=args.python,
            previous_version=prev_version if not args.smoke else args.start_version,
            next_version=next_version,
            cases_jsonl=cases_jsonl,
            overwrite=args.overwrite or args.smoke,
            max_cases=args.max_curation_cases,
            dry_run=args.dry_run,
        )
        eval_prefix = f"{args.job_prefix}_{next_version}"
        sessions, eval_expected_trial_counts = launch_eval_shards(
            python=args.python,
            env_file=args.env_file,
            dataset=args.dataset,
            job_prefix=args.job_prefix,
            skill_version=next_version,
            baseline_job_dirs=baseline_jobs,
            task_files=task_files,
            concurrency=1 if args.smoke else args.concurrency,
            agent_timeout_sec=args.agent_timeout_sec,
            agent_setup_timeout_sec=args.agent_setup_timeout_sec,
            stagger_sec=0 if args.smoke else args.stagger_sec,
            dry_run=args.dry_run,
            no_tmux=False,
            smoke=args.smoke,
        )
        if args.monitor and not args.smoke and not args.dry_run:
            wait_for_jobs(
                expected_trial_counts=eval_expected_trial_counts,
                sessions=sessions,
                poll_sec=args.poll_sec,
                timeout_sec=args.monitor_timeout_sec,
            )
        compared_current_glob = previous_eval_glob
        previous_eval_glob = f"jobs/{eval_prefix}_*_*_eval_skills"
        previous_compare_glob = compared_current_glob
        previous_label = prev_version
        manifest["iterations"].append(
            {
                "iteration_id": iteration_id,
                "previous_version": prev_version,
                "current_version": curr_version,
                "skill_version_written": next_version,
                "policy_update_dir": str(policy_dir),
                "eval_job_glob": previous_eval_glob,
                "tmux_sessions": sessions,
                "expected_trial_counts": eval_expected_trial_counts,
            }
        )
        if args.smoke:
            break

    out = ROOT / "run_logs" / "auto_iterations" / f"{args.job_prefix}_{utc_tag()}_manifest.json"
    write_json(out, manifest)
    print(f"Wrote {out}")


if __name__ == "__main__":
    main()
