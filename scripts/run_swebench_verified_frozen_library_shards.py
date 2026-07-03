#!/usr/bin/env python
from __future__ import annotations

import argparse
import json
import re
import shlex
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

DEFAULT_DATASET = "swe-bench/swe-bench-verified@2"
DEFAULT_TASK_FILE_GLOB = (
    "run_logs/skill_evo_shards/"
    "skill_evo_verified_glm51_full_v0001_[0-9][0-9][0-9]_[0-9][0-9][0-9]_20260602_1921.txt"
)
DEFAULT_PROMOTION_DECISIONS = (
    ROOT
    / "run_logs"
    / "swegym_skill_evo"
    / "swegym_novita_glm52_c15_resume_merged_20260630_071956"
    / "training"
    / "promotion_decisions.jsonl"
)


def utc_tag() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")


def shell_join(parts: list[str]) -> str:
    return " ".join(shlex.quote(str(part)) for part in parts)


def write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True) + "\n")


def expand_glob(pattern: str) -> list[Path]:
    import glob

    return sorted(Path(path).resolve() for path in glob.glob(pattern))


def task_file_count(path: Path) -> int:
    return sum(1 for line in path.read_text(errors="replace").splitlines() if line.strip())


def range_label(path: Path, index: int) -> str:
    match = re.search(r"(?<!\d)(\d{3})_(\d{3})(?!\d)", path.stem)
    if match:
        return f"{match.group(1)}_{match.group(2)}"
    return f"shard{index:02d}"


def safe_name(value: str, *, limit: int = 120) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_.-]+", "_", value.strip())
    cleaned = re.sub(r"_+", "_", cleaned).strip("._-")
    return (cleaned or "run")[:limit]


def tmux_session_exists(session: str) -> bool:
    proc = subprocess.run(
        ["tmux", "has-session", "-t", session],
        cwd=ROOT,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    )
    return proc.returncode == 0


def verify_python_runtime(python: str) -> None:
    command = [
        python,
        "-c",
        "import dotenv, harbor; import sys; print(sys.executable)",
    ]
    proc = subprocess.run(
        command,
        cwd=ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if proc.returncode != 0:
        raise SystemExit(
            "Python runtime cannot import required benchmark dependencies "
            f"(dotenv, harbor): {python}\n"
            f"stdout:\n{proc.stdout}\n"
            f"stderr:\n{proc.stderr}"
        )


def source_env_block(env_file: Path) -> list[str]:
    lines = [
        "set -a",
        f'if [ -f {shlex.quote(str(env_file))} ]; then . {shlex.quote(str(env_file))}; fi',
        "set +a",
        "export LLM_PROVIDER=openai",
        "export OPENAI_COMPAT_API=${OPENAI_COMPAT_API:-openai-completions}",
        "export OPENAI_COMPAT_REASONING_EFFORT=none",
        "export OPENAI_COMPAT_ENABLE_THINKING=false",
        "export CLAUDE_CODE_ATTRIBUTION_HEADER=0",
        "export PI_THINKING=off",
    ]
    return lines


def build_baseline_command(
    *,
    args: argparse.Namespace,
    shard: Path,
    baseline_job_name: str,
) -> list[str]:
    return [
        args.python,
        "scripts/run_benchmark.py",
        "--dataset",
        args.dataset,
        "--provider",
        "openai",
        "--job-name",
        baseline_job_name,
        "--task-names-file",
        str(shard),
        "--concurrency",
        str(args.concurrency_per_shard),
        "--agent-timeout-sec",
        str(args.agent_timeout_sec),
        "--agent-setup-timeout-sec",
        str(args.agent_setup_timeout_sec),
        "--e2b-sandbox-timeout-sec",
        str(args.e2b_sandbox_timeout_sec),
        "--no-skills",
    ]


def build_direct_eval_command(
    *,
    args: argparse.Namespace,
    shard: Path,
    job_name: str,
) -> list[str]:
    return [
        args.python,
        "scripts/run_benchmark.py",
        "--dataset",
        args.dataset,
        "--provider",
        "openai",
        "--job-name",
        job_name,
        "--task-names-file",
        str(shard),
        "--concurrency",
        str(args.concurrency_per_shard),
        "--agent-timeout-sec",
        str(args.agent_timeout_sec),
        "--agent-setup-timeout-sec",
        str(args.agent_setup_timeout_sec),
        "--e2b-sandbox-timeout-sec",
        str(args.e2b_sandbox_timeout_sec),
        "--use-skills",
    ]


def build_eval_only_command(
    *,
    args: argparse.Namespace,
    shard: Path,
    eval_run_name: str,
    baseline_job_dir: Path,
    eval_job_dir: Path | None,
) -> list[str]:
    command = [
        args.python,
        "scripts/run_skill_evo_eval_only.py",
        "--run-name",
        eval_run_name,
        "--dataset",
        args.dataset,
        "--provider",
        "openai",
        "--baseline-job-dir",
        str(baseline_job_dir),
        "--task-names-file",
        str(shard),
        "--skill-version-id",
        args.skill_version_id,
        "--frozen-library-promotion-decisions",
        str(args.promotion_decisions),
        "--frozen-library-root",
        str(args.frozen_library_root),
        "--frozen-min-success-repo-support",
        str(args.frozen_min_success_repo_support),
        "--frozen-min-success-positive-support",
        str(args.frozen_min_success_positive_support),
        "--frozen-max-success-skills",
        str(args.frozen_max_success_skills),
        "--frozen-min-failure-repo-support",
        str(args.frozen_min_failure_repo_support),
        "--frozen-max-failure-skills",
        str(args.frozen_max_failure_skills),
        "--concurrency",
        str(args.concurrency_per_shard),
        "--agent-timeout-sec",
        str(args.agent_timeout_sec),
        "--agent-setup-timeout-sec",
        str(args.agent_setup_timeout_sec),
        "--e2b-sandbox-timeout-sec",
        str(args.e2b_sandbox_timeout_sec),
    ]
    if args.frozen_include_support_1_failures:
        command.append("--frozen-include-support-1-failures")
    if args.frozen_include_memory_only_success:
        command.append("--frozen-include-memory-only-success")
    if args.frozen_exclude_general:
        command.append("--frozen-exclude-general")
    if eval_job_dir is not None:
        command.extend(["--eval-job-dir", str(eval_job_dir)])
    return command


def render_direct_shard_script(
    *,
    args: argparse.Namespace,
    shard: Path,
    log_path: Path,
    eval_job_name: str,
    skill_pack_root: Path,
) -> str:
    eval_job_dir = ROOT / "jobs" / eval_job_name
    eval_command = build_direct_eval_command(
        args=args,
        shard=shard,
        job_name=eval_job_name,
    )
    lines = [
        "#!/usr/bin/env bash",
        "set -euo pipefail",
        f"cd {shlex.quote(str(ROOT))}",
        f"mkdir -p {shlex.quote(str(log_path.parent))}",
        f"exec >> {shlex.quote(str(log_path))} 2>&1",
        'echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] direct skills shard start"',
        *source_env_block(args.env_file),
        f"export E2B_CONCURRENCY={shlex.quote(str(args.concurrency_per_shard))}",
        f"export PI_SKILL_PACK_ROOT={shlex.quote(str(skill_pack_root))}",
        "export PI_USE_SKILL_HARNESS_MEMORY=false",
        "export PI_SKILL_RETRIEVAL_SCOPE=transfer",
        f'echo "eval_job={eval_job_name}"',
        f'echo "skill_pack_root={skill_pack_root}"',
        f'echo "task_file={shard}"',
        f"if [ -f {shlex.quote(str(eval_job_dir / 'result.json'))} ]; then",
        '  echo "eval result exists; skipping direct skills run"',
        "else",
        f"  {shell_join(eval_command)}",
        "fi",
        'echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] direct skills shard done"',
    ]
    return "\n".join(lines) + "\n"


def render_shard_script(
    *,
    args: argparse.Namespace,
    shard: Path,
    log_path: Path,
    baseline_job_name: str,
    eval_run_name: str,
    eval_job_name: str,
) -> str:
    baseline_job_dir = ROOT / "jobs" / baseline_job_name
    eval_job_dir = ROOT / "jobs" / eval_job_name
    baseline_command = build_baseline_command(
        args=args,
        shard=shard,
        baseline_job_name=baseline_job_name,
    )
    eval_command = build_eval_only_command(
        args=args,
        shard=shard,
        eval_run_name=eval_run_name,
        baseline_job_dir=baseline_job_dir,
        eval_job_dir=None,
    )
    eval_report_command = build_eval_only_command(
        args=args,
        shard=shard,
        eval_run_name=eval_run_name,
        baseline_job_dir=baseline_job_dir,
        eval_job_dir=eval_job_dir,
    )
    lines = [
        "#!/usr/bin/env bash",
        "set -euo pipefail",
        f"cd {shlex.quote(str(ROOT))}",
        f"mkdir -p {shlex.quote(str(log_path.parent))}",
        f"exec >> {shlex.quote(str(log_path))} 2>&1",
        'echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] shard start"',
        *source_env_block(args.env_file),
        f"export E2B_CONCURRENCY={shlex.quote(str(args.concurrency_per_shard))}",
        f'echo "baseline_job={baseline_job_name}"',
        f'echo "eval_run={eval_run_name}"',
        f'echo "eval_job={eval_job_name}"',
        f'echo "task_file={shard}"',
        f"if [ -f {shlex.quote(str(baseline_job_dir / 'result.json'))} ]; then",
        '  echo "baseline result exists; skipping baseline run"',
        "else",
        f"  {shell_join(baseline_command)}",
        "fi",
        f"if [ -f {shlex.quote(str(eval_job_dir / 'result.json'))} ]; then",
        '  echo "eval result exists; recomputing eval report only"',
        f"  {shell_join(eval_report_command)}",
        "else",
        f"  {shell_join(eval_command)}",
        "fi",
        'echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] shard done"',
    ]
    return "\n".join(lines) + "\n"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Launch SWE-bench Verified direct frozen-library skill evals as fixed shards. "
            "By default this does not run a no-skill baseline."
        )
    )
    parser.add_argument(
        "--run-prefix",
        default="swebench_verified_glm52_novita_v0100_frozen",
        help="Prefix used for tmux sessions, job names, and run logs.",
    )
    parser.add_argument("--run-id", default=None, help="Exact run id. Defaults to <run-prefix>_<UTC tag>.")
    parser.add_argument("--dataset", default=DEFAULT_DATASET)
    parser.add_argument("--task-file-glob", default=DEFAULT_TASK_FILE_GLOB)
    parser.add_argument("--num-shards", type=int, default=5)
    parser.add_argument("--concurrency-per-shard", type=int, default=10)
    parser.add_argument("--python", default=sys.executable)
    parser.add_argument("--env-file", type=Path, default=ROOT / ".env")
    parser.add_argument("--skill-version-id", default="v0100")
    parser.add_argument("--skill-root", type=Path, default=ROOT / "skills" / "accepted")
    parser.add_argument("--promotion-decisions", type=Path, default=DEFAULT_PROMOTION_DECISIONS)
    parser.add_argument("--frozen-library-root", type=Path, default=ROOT / "skills" / "downstream")
    parser.add_argument("--frozen-min-success-repo-support", type=int, default=2)
    parser.add_argument("--frozen-min-success-positive-support", type=int, default=2)
    parser.add_argument("--frozen-max-success-skills", type=int, default=8)
    parser.add_argument("--frozen-include-memory-only-success", action="store_true")
    parser.add_argument("--frozen-min-failure-repo-support", type=int, default=2)
    parser.add_argument("--frozen-max-failure-skills", type=int, default=8)
    parser.add_argument("--frozen-include-support-1-failures", action="store_true")
    parser.add_argument("--frozen-exclude-general", action="store_true")
    parser.add_argument(
        "--with-baseline",
        action="store_true",
        help="Legacy mode: run a no-skill baseline before each skill eval and compare them.",
    )
    parser.add_argument(
        "--materialize-only",
        action="store_true",
        help="Materialize the frozen skill library and write the manifest without launching tmux jobs.",
    )
    parser.add_argument("--agent-timeout-sec", type=float, default=3600)
    parser.add_argument("--agent-setup-timeout-sec", type=float, default=1200)
    parser.add_argument("--e2b-sandbox-timeout-sec", type=int, default=7200)
    parser.add_argument("--stagger-sec", type=float, default=20)
    parser.add_argument("--replace-sessions", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    args.env_file = args.env_file.expanduser().resolve()
    args.skill_root = args.skill_root.expanduser().resolve()
    args.promotion_decisions = args.promotion_decisions.expanduser().resolve()
    args.frozen_library_root = args.frozen_library_root.expanduser().resolve()
    args.python = str(Path(args.python).expanduser()) if "/" in args.python else args.python
    verify_python_runtime(args.python)

    source_skill_root = args.skill_root / args.skill_version_id
    if not source_skill_root.exists():
        raise SystemExit(f"Missing source skill root: {source_skill_root}")
    if not args.promotion_decisions.exists():
        raise SystemExit(f"Missing promotion decisions: {args.promotion_decisions}")
    task_files = expand_glob(args.task_file_glob)
    if len(task_files) != args.num_shards:
        raise SystemExit(
            f"Expected {args.num_shards} task shard files from {args.task_file_glob!r}, found {len(task_files)}"
        )

    from evolution.frozen_library import materialize_frozen_skill_library_from_files

    run_id = safe_name(args.run_id or f"{args.run_prefix}_{utc_tag()}")
    run_dir = ROOT / "run_logs" / "swebench_verified_frozen_shards" / run_id
    skill_pack_root = args.frozen_library_root / run_id / args.skill_version_id
    frozen_manifest = None
    if args.dry_run:
        frozen_manifest = {
            "dry_run": True,
            "output_root": str(skill_pack_root),
        }
    else:
        frozen_manifest = materialize_frozen_skill_library_from_files(
            source_skill_root=source_skill_root,
            output_root=skill_pack_root,
            promotion_decisions_path=args.promotion_decisions,
            run_name=run_id,
            min_success_repo_support=args.frozen_min_success_repo_support,
            min_success_positive_support=args.frozen_min_success_positive_support,
            max_success_skills=args.frozen_max_success_skills,
            require_accepted_success=not args.frozen_include_memory_only_success,
            min_failure_repo_support=args.frozen_min_failure_repo_support,
            max_failure_skills=args.frozen_max_failure_skills,
            include_support_1_failures=args.frozen_include_support_1_failures,
            include_general=not args.frozen_exclude_general,
            clean=True,
        )
    session_prefix = safe_name(f"swv_{run_id}", limit=70)
    manifest_rows: list[dict[str, Any]] = []

    for index, shard in enumerate(task_files, start=1):
        label = range_label(shard, index)
        baseline_job_name = safe_name(f"{run_id}_{label}_baseline_noskills") if args.with_baseline else None
        eval_run_name = safe_name(f"{run_id}_{label}_frozen")
        eval_job_name = (
            safe_name(f"{eval_run_name}_eval_skills")
            if args.with_baseline
            else safe_name(f"{run_id}_{label}_skills")
        )
        session = safe_name(f"{session_prefix}_s{index:02d}", limit=90)
        log_path = run_dir / f"{label}.log"
        script_path = run_dir / f"{label}.sh"
        if args.with_baseline:
            script_text = render_shard_script(
                args=args,
                shard=shard,
                log_path=log_path,
                baseline_job_name=str(baseline_job_name),
                eval_run_name=eval_run_name,
                eval_job_name=eval_job_name,
            )
        else:
            script_text = render_direct_shard_script(
                args=args,
                shard=shard,
                log_path=log_path,
                eval_job_name=eval_job_name,
                skill_pack_root=skill_pack_root,
            )
        manifest_rows.append(
            {
                "index": index,
                "label": label,
                "task_file": str(shard),
                "task_count": task_file_count(shard),
                "session": session,
                "log_path": str(log_path),
                "script_path": str(script_path),
                "baseline_job_name": baseline_job_name,
                "baseline_job_dir": str(ROOT / "jobs" / str(baseline_job_name)) if baseline_job_name else None,
                "eval_run_name": eval_run_name,
                "eval_job_name": eval_job_name,
                "eval_job_dir": str(ROOT / "jobs" / eval_job_name),
                "evolution_run_dir": str(ROOT / "run_logs" / "evolution" / eval_run_name) if args.with_baseline else None,
            }
        )
        if not args.dry_run:
            script_path.parent.mkdir(parents=True, exist_ok=True)
            script_path.write_text(script_text)
            script_path.chmod(0o755)

    manifest = {
        "schema_version": 1,
        "run_id": run_id,
        "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "dataset": args.dataset,
        "mode": (
            "swebench_verified_frozen_library_sharded_eval_with_baseline"
            if args.with_baseline
            else "swebench_verified_frozen_library_direct_skills_eval"
        ),
        "num_shards": len(manifest_rows),
        "concurrency_per_shard": args.concurrency_per_shard,
        "total_requested_concurrency": args.concurrency_per_shard * len(manifest_rows),
        "skill_version_id": args.skill_version_id,
        "source_skill_root": str(source_skill_root),
        "promotion_decisions": str(args.promotion_decisions),
        "frozen_library_root": str(args.frozen_library_root),
        "skill_pack_root": str(skill_pack_root),
        "frozen_library_manifest": frozen_manifest,
        "with_baseline": args.with_baseline,
        "selection": {
            "frozen_min_success_repo_support": args.frozen_min_success_repo_support,
            "frozen_min_success_positive_support": args.frozen_min_success_positive_support,
            "frozen_max_success_skills": args.frozen_max_success_skills,
            "frozen_include_memory_only_success": args.frozen_include_memory_only_success,
            "frozen_min_failure_repo_support": args.frozen_min_failure_repo_support,
            "frozen_max_failure_skills": args.frozen_max_failure_skills,
            "frozen_include_support_1_failures": args.frozen_include_support_1_failures,
            "frozen_exclude_general": args.frozen_exclude_general,
        },
        "shards": manifest_rows,
    }
    if args.dry_run:
        print(json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True))
        return

    write_json(run_dir / "manifest.json", manifest)
    if args.materialize_only:
        print(f"[swebench-verified-frozen] materialized skill library: {skill_pack_root}", flush=True)
        print(f"[swebench-verified-frozen] manifest: {run_dir / 'manifest.json'}", flush=True)
        return
    for row in manifest_rows:
        session = row["session"]
        if tmux_session_exists(session):
            if not args.replace_sessions:
                raise SystemExit(f"tmux session already exists: {session}")
            subprocess.run(["tmux", "kill-session", "-t", session], cwd=ROOT, check=True)
        command_text = f"bash {shlex.quote(row['script_path'])}"
        print(f"[swebench-verified-frozen] launch {session}: {command_text}", flush=True)
        subprocess.run(["tmux", "new-session", "-d", "-s", session, command_text], cwd=ROOT, check=True)
        if args.stagger_sec > 0 and row is not manifest_rows[-1]:
            time.sleep(args.stagger_sec)
    print(f"[swebench-verified-frozen] manifest: {run_dir / 'manifest.json'}", flush=True)


if __name__ == "__main__":
    main()
