#!/usr/bin/env python
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.job_run_lock import exclusive_job_run  # noqa: E402
from scripts.materialize_swebench_tts_evolution_gates import (  # noqa: E402
    add_reward_condition_args,
    reward_condition_from_args,
)
from providers import resolve_provider  # noqa: E402


DEFAULT_PYTHON = Path(
    "/vePFS-Mindverse/user/intern/jungao/Marcronv1_SWE/.venv312/bin/python"
)
DEFAULT_DATASET = Path(
    "/vePFS-Mindverse/user/intern/jungao/Marcronv1-Coding/deep-swe/tasks"
)
DEFAULT_BASE_SKILLS = (
    ROOT
    / "skills/downstream/"
    "swebench_verified_cc_novita_glm52_v0201_frozen_downstream_20260711_074608/"
    "v0201"
)
DEFAULT_POLICY = (
    ROOT
    / "run_logs/swegym_skill_evo/"
    "swegym_novita_glm52_c15_resume_merged_20260630_071956/"
    "training/policy_state.json"
)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def timestamp_id() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")


def reward_condition_cli_args(condition: dict[str, Any]) -> list[str]:
    return [
        "--reward-operator",
        str(condition["operator"]),
        "--reward-value",
        f"{float(condition['value']):g}",
        (
            "--include-missing-reward"
            if condition["include_missing"]
            else "--no-include-missing-reward"
        ),
    ]


def absolute_path_preserving_symlinks(path: Path) -> Path:
    return Path(os.path.abspath(path.expanduser()))


def validate_child_python(python: Path) -> None:
    completed = subprocess.run(
        [
            str(python),
            "-c",
            "import scripts.run_benchmark; import scripts.aggregate_benchmark_job",
        ],
        cwd=ROOT,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        check=False,
    )
    if completed.returncode != 0:
        detail = (completed.stdout or "").strip()[-2000:]
        raise SystemExit(
            f"DeepSWE child Python preflight failed for {python}: {detail}"
        )


def read_env_file(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.is_file():
        return values
    for raw_line in path.read_text(errors="replace").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip("\"").strip("'")
        if key:
            values[key] = value
    return values


def write_json_atomic(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    with tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        dir=path.parent,
        prefix=f".{path.name}.",
        delete=False,
    ) as handle:
        handle.write(payload)
        temporary = Path(handle.name)
    os.replace(temporary, path)


def run_logged(
    *,
    command: list[str],
    env: dict[str, str],
    log_path: Path,
) -> int:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    printable = " ".join(command)
    print(f"[{utc_now()}] run: {printable}", flush=True)
    with log_path.open("a", encoding="utf-8") as log:
        log.write(f"\n[{utc_now()}] run: {printable}\n")
        log.flush()
        completed = subprocess.run(
            command,
            cwd=ROOT,
            env=env,
            stdout=log,
            stderr=subprocess.STDOUT,
            check=False,
        )
        log.write(f"[{utc_now()}] exit_code={completed.returncode}\n")
    return completed.returncode


def aggregate_is_complete(
    path: Path, expected_trials: int, expected_run_id: str
) -> bool:
    if not path.is_file():
        return False
    try:
        report = json.loads(path.read_text(errors="replace"))
    except (OSError, json.JSONDecodeError):
        return False
    completeness = report.get("completeness") or {}
    return bool(
        report.get("complete") is True
        and report.get("run_id") == expected_run_id
        and report.get("benchmark_name") == "deepswe"
        and not (report.get("infra_invalid_trials") or [])
        and int(completeness.get("expected_trials") or 0) == expected_trials
        and int(completeness.get("trial_result_files") or 0) == expected_trials
    )


def run_eval_until_valid(
    *,
    args: argparse.Namespace,
    env: dict[str, str],
    job_name: str,
    skill_root: Path,
    output_dir: Path,
    log_dir: Path,
    expected_trials: int,
) -> Path:
    report_path = output_dir / "score_report.json"
    if aggregate_is_complete(report_path, expected_trials, job_name):
        return report_path
    eval_command = [
        str(args.python),
        "scripts/run_deepswe.py",
        "--dataset",
        str(args.dataset),
        "--benchmark-name",
        "deepswe",
        "--agent",
        "claude-code",
        "--provider",
        args.provider,
        "--provider-model",
        args.model,
        "--job-name",
        job_name,
        "--concurrency",
        str(args.concurrency),
        "--agent-timeout-sec",
        str(args.agent_timeout_sec),
        "--agent-setup-timeout-sec",
        str(args.agent_setup_timeout_sec),
        "--e2b-sandbox-timeout-sec",
        "14400",
        "--verifier-buffer-sec",
        "4800",
        "--override-cpus",
        "2",
        "--override-memory-mb",
        "8192",
        "--override-storage-mb",
        "20480",
        "--max-retries",
        "3",
        "--claude-sdk-version",
        args.claude_sdk_version,
        "--force-agent-internet",
        "--use-skills",
    ]
    aggregate_command = [
        str(args.python),
        "scripts/aggregate_benchmark_job.py",
        "--run-id",
        job_name,
        "--job-dir",
        str(ROOT / "jobs" / job_name),
        "--out-dir",
        str(output_dir),
        "--expected-trials",
        str(expected_trials),
    ]
    eval_env = dict(env)
    eval_env["PI_SKILL_PACK_ROOT"] = str(skill_root.resolve())
    for recovery_round in range(1, args.recovery_rounds + 1):
        run_logged(
            command=eval_command,
            env=eval_env,
            log_path=log_dir / f"{job_name}.round{recovery_round}.log",
        )
        aggregate_rc = run_logged(
            command=aggregate_command,
            env=eval_env,
            log_path=log_dir / f"{job_name}.aggregate.log",
        )
        if aggregate_rc == 0 and aggregate_is_complete(
            report_path, expected_trials, job_name
        ):
            return report_path
        print(
            f"[{utc_now()}] {job_name} remains incomplete/infra-invalid after "
            f"recovery round {recovery_round}/{args.recovery_rounds}",
            flush=True,
        )
    raise RuntimeError(
        f"DeepSWE job did not produce a valid {expected_trials}-trial aggregate: "
        f"{job_name}"
    )


def parse_args() -> argparse.Namespace:
    stamp = timestamp_id()
    parser = argparse.ArgumentParser(
        description="Run a fresh, fail-closed DeepSWE frozen + TTS evolution through gate 4."
    )
    parser.add_argument("--launcher-id", default=f"deepswe_full_evolution_{stamp}")
    parser.add_argument(
        "--frozen-run-id",
        default=f"deepswe_cc_novita_glm52_infrafix_frozen_{stamp}",
    )
    parser.add_argument(
        "--tts-run-id",
        default=f"deepswe_cc_novita_glm52_infrafix_tts_evo_{stamp}",
    )
    parser.add_argument("--dataset", type=Path, default=DEFAULT_DATASET)
    parser.add_argument("--base-skill-root", type=Path, default=DEFAULT_BASE_SKILLS)
    parser.add_argument("--policy-state", type=Path, default=DEFAULT_POLICY)
    parser.add_argument("--env-file", type=Path, default=ROOT / ".env")
    parser.add_argument("--python", type=Path, default=DEFAULT_PYTHON)
    parser.add_argument("--provider", default="novita")
    parser.add_argument("--model", default="zai-org/glm-5.2")
    parser.add_argument("--claude-sdk-version", default="0.2.116")
    parser.add_argument("--pi-version", default="0.80.6")
    parser.add_argument("--concurrency", type=int, default=15)
    parser.add_argument("--agent-timeout-sec", type=int, default=7200)
    parser.add_argument("--agent-setup-timeout-sec", type=int, default=1200)
    parser.add_argument("--recovery-rounds", type=int, default=4)
    parser.add_argument("--max-gate", type=int, default=4)
    add_reward_condition_args(parser)
    return parser.parse_args()


def run_full(args: argparse.Namespace, state_path: Path) -> None:
    args.dataset = args.dataset.expanduser().resolve()
    args.base_skill_root = args.base_skill_root.expanduser().resolve()
    args.policy_state = args.policy_state.expanduser().resolve()
    args.env_file = args.env_file.expanduser().resolve()
    args.python = absolute_path_preserving_symlinks(args.python)
    reward_condition = reward_condition_from_args(args)
    reward_cli_args = reward_condition_cli_args(reward_condition)
    if not 2 <= args.max_gate <= 4:
        raise SystemExit("--max-gate must be between 2 and 4.")
    for path, label in (
        (args.dataset, "DeepSWE dataset"),
        (args.base_skill_root, "base skill root"),
        (args.policy_state, "writer/evaluator policy"),
        (args.python, "Python executable"),
    ):
        if not path.exists():
            raise SystemExit(f"Missing {label}: {path}")
    validate_child_python(args.python)
    expected_trials = len(list(args.dataset.glob("*/task.toml")))
    if expected_trials != 113:
        raise SystemExit(
            f"Expected the full 113-task DeepSWE dataset, found {expected_trials}: "
            f"{args.dataset}"
        )

    run_dir = state_path.parent
    tmp_dir = run_dir / "tmp"
    tmp_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=tmp_dir):
        pass
    tmp_free_bytes = shutil.disk_usage(tmp_dir).free
    if tmp_free_bytes < 5 * 1024**3:
        raise SystemExit(
            f"DeepSWE run temp directory has less than 5 GiB free: {tmp_dir}"
        )

    env = read_env_file(args.env_file)
    env.update(os.environ)
    env.update(
        {
            "LLM_PROVIDER": args.provider,
            "CLAUDE_CODE_ATTRIBUTION_HEADER": "0",
            "FORCE_DISABLE_THINKING": "1",
            "PI_THINKING": "off",
            "OPENAI_COMPAT_REASONING_EFFORT": "none",
            "OPENAI_COMPAT_ENABLE_THINKING": "false",
            "NOVITA_REASONING_EFFORT": "none",
            "NOVITA_ENABLE_THINKING": "false",
            "FORCE_AGENT_INTERNET": "1",
            "PI_USE_SKILL_HARNESS_MEMORY": "false",
            "CLAUDE_USE_SKILL_HARNESS_MEMORY": "false",
            "PI_SKILL_RETRIEVAL_SCOPE": "transfer",
            "CLAUDE_AGENT_SDK_VERSION": args.claude_sdk_version,
            "PI_CODING_AGENT_VERSION": args.pi_version,
            "E2B_CONCURRENCY": str(args.concurrency),
            "TMPDIR": str(tmp_dir),
            "TMP": str(tmp_dir),
            "TEMP": str(tmp_dir),
        }
    )
    provider = resolve_provider(args.provider)
    missing_env = ["E2B_API_KEY"] + [
        name
        for name in provider.required_env(agent="claude-code")
        if not env.get(name)
    ]
    missing_env = [name for name in dict.fromkeys(missing_env) if not env.get(name)]
    if missing_env:
        raise SystemExit("Missing required environment variables: " + ", ".join(missing_env))

    log_dir = run_dir / "logs"
    state = {
        "schema_version": 1,
        "launcher_id": args.launcher_id,
        "frozen_run_id": args.frozen_run_id,
        "tts_run_id": args.tts_run_id,
        "max_gate": args.max_gate,
        "reward_condition": reward_condition,
        "expected_trials": expected_trials,
        "tmp_dir": str(tmp_dir),
        "tmp_free_bytes_at_start": tmp_free_bytes,
        "started_at": utc_now(),
        "status": "running",
        "current_step": "frozen_eval",
    }
    write_json_atomic(state_path, state)

    frozen_out = run_dir / "frozen" / "aggregate"
    frozen_report = run_eval_until_valid(
        args=args,
        env=env,
        job_name=args.frozen_run_id,
        skill_root=args.base_skill_root,
        output_dir=frozen_out,
        log_dir=log_dir,
        expected_trials=expected_trials,
    )
    state["current_step"] = "materialize_gate_1"
    state["frozen_report"] = str(frozen_report)
    write_json_atomic(state_path, state)

    tts_root = ROOT / "run_logs" / "deepswe_tts_evo"
    skill_output_root = ROOT / "skills" / "test_time"
    materialize_command = [
        str(args.python),
        "scripts/materialize_deepswe_tts_evolution_gates.py",
        "--source-run-id",
        args.frozen_run_id,
        "--source-aggregate",
        str(frozen_report),
        "--base-skill-root",
        str(args.base_skill_root),
        "--policy-state",
        str(args.policy_state),
        "--run-id",
        args.tts_run_id,
        "--out-root",
        str(tts_root),
        "--skill-output-root",
        str(skill_output_root),
        "--benchmark-name",
        "deepswe",
        *reward_cli_args,
    ]
    if run_logged(
        command=materialize_command,
        env=env,
        log_path=log_dir / "materialize_gate_1.log",
    ) != 0:
        raise RuntimeError("Initial DeepSWE gate materialization failed")

    attach_gate0 = [
        str(args.python),
        "scripts/attach_deepswe_gate_report.py",
        "--run-id",
        args.tts_run_id,
        "--gate-index",
        "0",
        "--aggregate",
        str(frozen_report),
        "--eval-run-id",
        args.frozen_run_id,
        "--tts-root",
        str(tts_root),
    ]
    if run_logged(
        command=attach_gate0,
        env=env,
        log_path=log_dir / "attach_gate_0.log",
    ) != 0:
        raise RuntimeError("Attaching the frozen report to gate 0 failed")

    state["current_step"] = "gate_1_eval"
    write_json_atomic(state_path, state)
    gate1_root = skill_output_root / args.tts_run_id / "gate_001"
    gate1_job = f"{args.tts_run_id}_gate001_eval"
    gate1_report = run_eval_until_valid(
        args=args,
        env=env,
        job_name=gate1_job,
        skill_root=gate1_root,
        output_dir=run_dir / "gate_001" / "aggregate",
        log_dir=log_dir,
        expected_trials=expected_trials,
    )
    attach_gate1 = [
        str(args.python),
        "scripts/attach_deepswe_gate_report.py",
        "--run-id",
        args.tts_run_id,
        "--gate-index",
        "1",
        "--aggregate",
        str(gate1_report),
        "--eval-run-id",
        gate1_job,
        "--tts-root",
        str(tts_root),
    ]
    if run_logged(
        command=attach_gate1,
        env=env,
        log_path=log_dir / "attach_gate_1.log",
    ) != 0:
        raise RuntimeError("Attaching the gate 1 report failed")

    state["current_step"] = "subset_gates_2_to_4"
    state["gate_1_report"] = str(gate1_report)
    write_json_atomic(state_path, state)
    subset_command = [
        str(args.python),
        "scripts/run_swebench_tts_subset_evo_loop.py",
        "--run-id",
        args.tts_run_id,
        "--tts-root",
        str(tts_root),
        "--skill-output-root",
        str(skill_output_root),
        "--policy-state",
        str(args.policy_state),
        "--dataset",
        str(args.dataset),
        "--benchmark-name",
        "deepswe",
        "--start-gate",
        "2",
        "--max-gate",
        str(args.max_gate),
        "--harness",
        "claude-code",
        "--provider",
        args.provider,
        "--provider-model",
        args.model,
        "--claude-sdk-version",
        args.claude_sdk_version,
        "--concurrency",
        str(args.concurrency),
        "--agent-timeout-sec",
        str(args.agent_timeout_sec),
        "--agent-setup-timeout-sec",
        str(args.agent_setup_timeout_sec),
        "--e2b-sandbox-timeout-sec",
        "14400",
        "--poll-sec",
        "120",
        "--recovery-rounds",
        str(args.recovery_rounds),
        "--env-file",
        str(args.env_file),
        "--python",
        str(args.python),
        *reward_cli_args,
    ]
    if run_logged(
        command=subset_command,
        env=env,
        log_path=log_dir / "subset_gates_2_to_4.log",
    ) != 0:
        raise RuntimeError("DeepSWE subset evolution failed")

    state["status"] = "complete"
    state["current_step"] = "complete"
    state["completed_at"] = utc_now()
    state["manifest"] = str(tts_root / args.tts_run_id / "manifest.json")
    write_json_atomic(state_path, state)
    print(json.dumps(state, ensure_ascii=False, indent=2, sort_keys=True), flush=True)


def main() -> None:
    args = parse_args()
    run_dir = ROOT / "run_logs" / "deepswe_full_evolution" / args.launcher_id
    state_path = run_dir / "state.json"
    with exclusive_job_run(run_dir):
        try:
            run_full(args, state_path)
        except BaseException as exc:
            state: dict[str, Any] = {}
            if state_path.is_file():
                try:
                    state = json.loads(state_path.read_text(errors="replace"))
                except json.JSONDecodeError:
                    state = {}
            state.update(
                {
                    "launcher_id": args.launcher_id,
                    "status": "failed",
                    "failed_at": utc_now(),
                    "error_type": exc.__class__.__name__,
                    "error": str(exc),
                }
            )
            write_json_atomic(state_path, state)
            raise


if __name__ == "__main__":
    main()
