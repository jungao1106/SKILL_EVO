#!/usr/bin/env python3
"""Launch sharded benchmark jobs with bounded per-process concurrency."""

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


DEFAULT_REPO_ROOT = Path(__file__).resolve().parents[4]
DEFAULT_DATASET = "swe-bench/swe-bench-verified@2"


def repo_root() -> Path:
    env_root = os.getenv("SKILLS_EVO_ROOT") or os.getenv("REPO_ROOT")
    return Path(env_root).expanduser().resolve() if env_root else DEFAULT_REPO_ROOT


def parse_range(value: str) -> tuple[int, int]:
    try:
        start_s, end_s = value.split("-", 1)
        start, end = int(start_s), int(end_s)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"invalid range {value!r}; use START-END") from exc
    if start < 1 or end < start:
        raise argparse.ArgumentTypeError(f"invalid range {value!r}; indexes are 1-based")
    return start, end


def read_tasks(path: Path) -> list[str]:
    tasks = []
    for line in path.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            tasks.append(line)
    if not tasks:
        raise SystemExit(f"task list is empty: {path}")
    return tasks


def parse_dataset(value: str) -> tuple[str, str | None]:
    if "@" in value:
        name, ref = value.split("@", 1)
        return name, ref
    return value, None


async def resolve_dataset_tasks(dataset: str, *, python_root: Path) -> list[str]:
    root = python_root
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
    from scripts.run_benchmark import build_config

    class Args:
        pass

    args = Args()
    args.dataset = dataset
    args.provider = "openai"
    args.job_name = "task_list_probe"
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
    task_names: list[str] = []
    for dataset_config in config.datasets:
        for task_config in await dataset_config.get_task_configs():
            if task_config.name:
                task_names.append(task_config.name)
            elif task_config.path:
                task_names.append(task_config.path.name)
    return task_names


def contiguous_ranges(n_tasks: int, n_shards: int) -> list[tuple[int, int]]:
    if n_shards < 1:
        raise ValueError("n_shards must be positive")
    if n_tasks < 1:
        raise ValueError("n_tasks must be positive")
    ranges: list[tuple[int, int]] = []
    base, remainder = divmod(n_tasks, n_shards)
    start = 1
    for index in range(n_shards):
        size = base + (1 if index < remainder else 0)
        if size <= 0:
            continue
        end = start + size - 1
        ranges.append((start, end))
        start = end + 1
    return ranges


def range_label(start: int, end: int) -> str:
    width = max(3, len(str(end)))
    return f"{start:0{width}d}_{end:0{width}d}"


def shell_join(parts: list[str]) -> str:
    return " ".join(shlex.quote(part) for part in parts)


def build_command(args: argparse.Namespace, job_name: str, task_file: Path) -> list[str]:
    cmd = [
        args.python,
        args.runner,
        "--dataset",
        args.dataset,
        "--concurrency",
        str(args.concurrency),
        "--job-name",
        job_name,
        "--agent-timeout-sec",
        str(args.timeout),
    ]
    if args.task_arg_mode == "file":
        cmd.extend([args.task_names_file_arg, str(task_file)])
    else:
        for task_name in read_tasks(task_file):
            cmd.extend([args.task_name_arg, task_name])
    if args.agent:
        cmd.extend(["--agent", args.agent])
    if args.provider:
        cmd.extend(["--provider", args.provider])
    if args.run_name:
        cmd.extend(["--run-name", job_name])
    if args.skill_version_id:
        cmd.extend(["--skill-version-id", args.skill_version_id])
    if args.append_skill_version:
        cmd.append("--append-skill-version")
    if args.summarize_with_backbone:
        cmd.append("--summarize-with-backbone")
    if args.agent_setup_timeout:
        cmd.extend(["--agent-setup-timeout-sec", str(args.agent_setup_timeout)])
    if args.result_only:
        cmd.append("--result-only")
    if args.quiet:
        cmd.append("--quiet")
    for extra in args.extra_arg:
        cmd.extend(shlex.split(extra))
    return cmd


def tmux_command_text(args: argparse.Namespace, runner_cmd: list[str]) -> str:
    command_text = shell_join(runner_cmd)
    if args.workdir:
        return f"cd {shlex.quote(str(args.workdir))} && {command_text}"
    return command_text


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Split a task list into ranges and launch one benchmark runner per shard."
    )
    parser.add_argument("--runner", required=True, help="Benchmark runner script, e.g. scripts/run_benchmark.py")
    parser.add_argument("--dataset", default=DEFAULT_DATASET, help="Dataset argument passed to the runner")
    parser.add_argument("--task-list", type=Path, default=None, help="One task name per line")
    parser.add_argument("--ranges", default="", help="Comma-separated 1-based ranges, e.g. 1-100,101-200")
    parser.add_argument("--auto-task-list", action="store_true", help="Resolve all dataset tasks and write a stable task list.")
    parser.add_argument("--n-shards", type=int, default=5, help="Number of contiguous shards for --auto-task-list.")
    parser.add_argument("--job-prefix", required=True, help="Prefix for generated job names")
    parser.add_argument("--agent", default="", help="Optional --agent value for the runner")
    parser.add_argument("--provider", default="", help="Optional --provider value for the runner")
    parser.add_argument("--run-name", action="store_true", help="Pass each generated job name as --run-name to the runner.")
    parser.add_argument("--skill-version-id", default="", help="Shared iteration skill version, e.g. v0001.")
    parser.add_argument("--append-skill-version", action="store_true", help="Pass --append-skill-version to the runner.")
    parser.add_argument("--summarize-with-backbone", action="store_true", help="Pass --summarize-with-backbone to the runner.")
    parser.add_argument("--concurrency", type=int, default=20, help="Per-process concurrency")
    parser.add_argument("--timeout", type=float, default=900, help="Agent timeout in seconds")
    parser.add_argument("--agent-setup-timeout", type=float, default=1200, help="Agent setup timeout passed to the runner.")
    parser.add_argument("--python", default="python", help="Python executable")
    parser.add_argument("--output-dir", type=Path, default=Path("data/task_splits"), help="Where shard task files are written")
    parser.add_argument("--workdir", type=Path, default=None, help="Working directory for tmux-launched runner commands")
    parser.add_argument(
        "--task-arg-mode",
        choices=["file", "repeat"],
        default="file",
        help=(
            "How shard task names are passed to the runner: 'file' passes one "
            "task file argument, 'repeat' expands one argument per task."
        ),
    )
    parser.add_argument(
        "--task-names-file-arg",
        default="--task-names-file",
        help="Runner flag used with --task-arg-mode file.",
    )
    parser.add_argument(
        "--task-name-arg",
        default="--include-task-name",
        help="Runner flag repeated for each task with --task-arg-mode repeat.",
    )
    parser.add_argument("--date-tag", default="", help="Optional date/job suffix; defaults to UTC YYYYmmdd_HHMM")
    parser.add_argument("--tmux", action="store_true", default=True, help="Launch each shard in tmux")
    parser.add_argument("--no-tmux", dest="tmux", action="store_false", help="Print shell commands without tmux wrapping")
    parser.add_argument("--result-only", action="store_true", help="Pass --result-only to the runner")
    parser.add_argument("--quiet", action="store_true", help="Pass --quiet to the runner")
    parser.add_argument("--dry-run", action="store_true", help="Write shard files and print commands without launching")
    parser.add_argument(
        "--stagger-sec",
        type=float,
        default=0,
        help="Seconds to sleep between launching shards. Useful for E2B sandbox bursts.",
    )
    parser.add_argument(
        "--plan-file",
        type=Path,
        default=None,
        help="Optional JSONL file where generated shard launch commands are written.",
    )
    parser.add_argument(
        "--extra-arg",
        action="append",
        default=[],
        help="Additional runner argument string. Repeat for multiple groups.",
    )
    args = parser.parse_args()

    root = repo_root()
    if args.auto_task_list:
        date_hint = args.date_tag or datetime.now(timezone.utc).strftime("%Y%m%d_%H%M")
        args.output_dir.mkdir(parents=True, exist_ok=True)
        task_list_path = args.task_list or args.output_dir / f"{args.job_prefix}_all_tasks_{date_hint}.txt"
        tasks = asyncio.run(resolve_dataset_tasks(args.dataset, python_root=root))
        task_list_path.write_text("\n".join(tasks) + "\n")
        args.task_list = task_list_path
        if not args.ranges:
            args.ranges = ",".join(
                f"{start}-{end}" for start, end in contiguous_ranges(len(tasks), args.n_shards)
            )
    else:
        if args.task_list is None:
            raise SystemExit("--task-list is required unless --auto-task-list is used")
        tasks = read_tasks(args.task_list)
    if not args.ranges:
        raise SystemExit("--ranges is required unless --auto-task-list is used")
    ranges = [parse_range(item.strip()) for item in args.ranges.split(",") if item.strip()]
    date_tag = args.date_tag or datetime.now(timezone.utc).strftime("%Y%m%d_%H%M")
    args.output_dir.mkdir(parents=True, exist_ok=True)
    plan_rows: list[dict[str, object]] = []

    for shard_index, (start, end) in enumerate(ranges, start=1):
        if end > len(tasks):
            raise SystemExit(f"range {start}-{end} exceeds task list length {len(tasks)}")
        label = range_label(start, end)
        shard_tasks = tasks[start - 1 : end]
        task_file = args.output_dir / f"{args.job_prefix}_{label}_{date_tag}.txt"
        task_file.write_text("\n".join(shard_tasks) + "\n")

        job_name = f"{args.job_prefix}_{label}_c{args.concurrency}_t{int(args.timeout)}_{date_tag}"
        runner_cmd = build_command(args, job_name, task_file)
        if args.tmux:
            command_text = tmux_command_text(args, runner_cmd)
            launch_cmd = ["tmux", "new-session", "-d", "-s", job_name, command_text]
        else:
            launch_cmd = runner_cmd

        print(f"{label}: {shell_join(launch_cmd)}")
        plan_rows.append(
            {
                "index": shard_index,
                "range": label,
                "job_name": job_name,
                "task_file": str(task_file),
                "task_count": len(shard_tasks),
                "runner_command": runner_cmd,
                "launch_command": launch_cmd,
                "launch_command_text": shell_join(launch_cmd),
                "stagger_sec": args.stagger_sec,
            }
        )
        if not args.dry_run:
            subprocess.run(launch_cmd, check=True)
            if args.stagger_sec > 0 and shard_index < len(ranges):
                time.sleep(args.stagger_sec)

    if args.plan_file:
        args.plan_file.parent.mkdir(parents=True, exist_ok=True)
        args.plan_file.write_text(
            "\n".join(json.dumps(row, ensure_ascii=False) for row in plan_rows) + "\n"
        )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
