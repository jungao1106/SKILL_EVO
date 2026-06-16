#!/usr/bin/env python3
"""Launch the SKILLS_EVO SWE-Bench Verified skill-evolution run in shards."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import shlex
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[4]
DEFAULT_DATASET = "swe-bench/swe-bench-verified@2"
DEFAULT_PYTHON = "/vePFS-Mindverse/user/intern/jungao/Marcronv1_SWE/.venv312/bin/python"
DEFAULT_ENV_FILE = "/vePFS-Mindverse/user/intern/jungao/Marcronv1_SWE/.env"


def shell_join(parts: list[str]) -> str:
    return " ".join(shlex.quote(part) for part in parts)


def contiguous_ranges(n_tasks: int, n_shards: int) -> list[tuple[int, int]]:
    base, remainder = divmod(n_tasks, n_shards)
    ranges: list[tuple[int, int]] = []
    start = 1
    for index in range(n_shards):
        size = base + (1 if index < remainder else 0)
        if size <= 0:
            continue
        end = start + size - 1
        ranges.append((start, end))
        start = end + 1
    return ranges


def range_label(start: int, end: int, total: int) -> str:
    width = max(3, len(str(total)))
    return f"{start:0{width}d}_{end:0{width}d}"


async def resolve_tasks(dataset: str) -> list[str]:
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    from scripts.run_benchmark import build_config

    class Args:
        pass

    args = Args()
    args.dataset = dataset
    args.provider = "openai"
    args.job_name = "skill_evo_task_probe"
    args.concurrency = 1
    args.n_tasks = None
    args.include_task_name = None
    args.exclude_task_name = None
    args.overwrite_tasks = False
    args.force_build = False
    args.keep_sandboxes = False
    args.e2b_template_namespace = os.getenv("E2B_TEMPLATE_NAMESPACE", "anchen1011")
    args.e2b_pi_template_suffix = os.getenv("E2B_PI_TEMPLATE_SUFFIX", "pi_c6d7003a")
    args.keep_dockerfile_comments = False
    args.e2b_sandbox_timeout_sec = int(os.getenv("E2B_SANDBOX_TIMEOUT_SEC", "3600"))
    args.agent_setup_timeout_sec = 1200
    args.agent_timeout_sec = 1
    args.override_cpus = 1
    args.override_memory_mb = 4096
    args.override_storage_mb = 10240
    args.timeout_multiplier = 1.0
    args.agent_timeout_multiplier = None
    args.verifier_timeout_multiplier = None
    args.agent_setup_timeout_multiplier = 2.0
    args.environment_build_timeout_multiplier = 2.0
    args.quiet = True
    args.debug = False
    args.model_context_window = 128000
    args.model_max_tokens = 32000
    args.thinking = os.getenv("PI_THINKING", "off")
    args.tools = os.getenv("PI_TOOLS", "read,write,edit,bash,grep,find,ls")
    args.result_only = False
    args.use_skills = False

    config = build_config(args)
    tasks: list[str] = []
    for dataset_config in config.datasets:
        for task_config in await dataset_config.get_task_configs():
            if task_config.name:
                tasks.append(task_config.name)
            elif task_config.path:
                tasks.append(task_config.path.name)
    return tasks


def env_prefix(env_file: str) -> str:
    return (
        f"set -a; . {shlex.quote(env_file)}; set +a; "
        "export LLM_PROVIDER=openai; "
        'export OPENAI_COMPAT_API_KEY="$NOVITA_API_KEY"; '
        'export OPENAI_COMPAT_BASE_URL="${NOVITA_BASE_URL:-https://api.novita.ai/v3/openai}"; '
        'export OPENAI_COMPAT_MODEL="${NOVITA_MODEL:-zai-org/glm-5.1}"; '
        "export OPENAI_COMPAT_API=openai-completions; "
        'export OPENAI_COMPAT_CONTEXT_WINDOW="${NOVITA_CONTEXT_WINDOW:-128000}"; '
        'export OPENAI_COMPAT_MAX_TOKENS="${NOVITA_MAX_TOKENS:-32000}"; '
    )


def build_runner_command(
    *,
    python: str,
    run_name: str,
    dataset: str,
    task_file: Path,
    skill_version_id: str,
    concurrency: int,
    agent_timeout: float,
    agent_setup_timeout: float,
    summarize_with_backbone: bool,
) -> list[str]:
    command = [
        python,
        "scripts/run_skill_evo_verified.py",
        "--run-name",
        run_name,
        "--dataset",
        dataset,
        "--provider",
        "openai",
        "--task-names-file",
        str(task_file),
        "--concurrency",
        str(concurrency),
        "--agent-timeout-sec",
        str(agent_timeout),
        "--agent-setup-timeout-sec",
        str(agent_setup_timeout),
        "--skill-version-id",
        skill_version_id,
        "--append-skill-version",
    ]
    if summarize_with_backbone:
        command.append("--summarize-with-backbone")
    return command


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Launch 5x20 SWE-Bench Verified skill evolution shards."
    )
    parser.add_argument("--dataset", default=DEFAULT_DATASET)
    parser.add_argument("--job-prefix", default="skill_evo_verified_glm51_full")
    parser.add_argument("--skill-version-id", required=True)
    parser.add_argument("--n-shards", type=int, default=5)
    parser.add_argument("--concurrency", type=int, default=20)
    parser.add_argument("--agent-timeout-sec", type=float, default=900)
    parser.add_argument("--agent-setup-timeout-sec", type=float, default=1200)
    parser.add_argument("--python", default=DEFAULT_PYTHON)
    parser.add_argument("--env-file", default=DEFAULT_ENV_FILE)
    parser.add_argument("--output-dir", type=Path, default=Path("run_logs/skill_evo_shards"))
    parser.add_argument("--date-tag", default="")
    parser.add_argument("--stagger-sec", type=float, default=120)
    parser.add_argument("--summarize-with-backbone", action="store_true")
    parser.add_argument("--no-tmux", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--plan-file", type=Path, default=None)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    date_tag = args.date_tag or datetime.now(timezone.utc).strftime("%Y%m%d_%H%M")
    args.output_dir.mkdir(parents=True, exist_ok=True)

    tasks = asyncio.run(resolve_tasks(args.dataset))
    all_tasks_file = args.output_dir / f"{args.job_prefix}_all_tasks_{date_tag}.txt"
    all_tasks_file.write_text("\n".join(tasks) + "\n")
    ranges = contiguous_ranges(len(tasks), args.n_shards)
    plan_rows: list[dict[str, object]] = []

    for shard_index, (start, end) in enumerate(ranges, start=1):
        label = range_label(start, end, len(tasks))
        shard_tasks = tasks[start - 1 : end]
        task_file = args.output_dir / f"{args.job_prefix}_{label}_{date_tag}.txt"
        task_file.write_text("\n".join(shard_tasks) + "\n")
        run_name = f"{args.job_prefix}_{label}_c{args.concurrency}_t{int(args.agent_timeout_sec)}_{date_tag}"
        runner_command = build_runner_command(
            python=args.python,
            run_name=run_name,
            dataset=args.dataset,
            task_file=task_file,
            skill_version_id=args.skill_version_id,
            concurrency=args.concurrency,
            agent_timeout=args.agent_timeout_sec,
            agent_setup_timeout=args.agent_setup_timeout_sec,
            summarize_with_backbone=args.summarize_with_backbone,
        )
        command_text = f"cd {shlex.quote(str(ROOT))}; {env_prefix(args.env_file)}{shell_join(runner_command)}"
        launch_command = (
            runner_command
            if args.no_tmux
            else ["tmux", "new-session", "-d", "-s", run_name, command_text]
        )
        print(f"{label}: {shell_join(launch_command)}")
        plan_rows.append(
            {
                "index": shard_index,
                "range": label,
                "run_name": run_name,
                "task_file": str(task_file),
                "task_count": len(shard_tasks),
                "skill_version_id": args.skill_version_id,
                "runner_command": runner_command,
                "launch_command": launch_command,
                "launch_command_text": command_text,
            }
        )
        if not args.dry_run:
            subprocess.run(launch_command, check=True)
            if args.stagger_sec > 0 and shard_index < len(ranges):
                time.sleep(args.stagger_sec)

    plan_file = args.plan_file or args.output_dir / f"{args.job_prefix}_plan_{date_tag}.jsonl"
    plan_file.write_text("\n".join(json.dumps(row, ensure_ascii=False) for row in plan_rows) + "\n")
    print(f"tasks={len(tasks)} all_tasks={all_tasks_file} plan={plan_file}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
