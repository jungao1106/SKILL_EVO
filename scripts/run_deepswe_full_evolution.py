#!/usr/bin/env python
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import tomllib
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from evolution.score import summarize_job  # noqa: E402
from providers import populate_anthropic_provider_env, resolve_provider  # noqa: E402
from scripts.aggregate_benchmark_job import (  # noqa: E402
    infra_invalid_trials,
    job_identity_issues,
)
from scripts.job_run_lock import exclusive_job_run  # noqa: E402
from scripts.materialize_deepswe_tts_evolution_gates import (  # noqa: E402
    TASKWISE_OR_AGGREGATE_KIND,
    validate_taskwise_or_aggregate,
)
from scripts.run_deepswe_setting_bon import (  # noqa: E402
    validate_requested_execution,
    validate_skill_contract,
)


DEFAULT_PYTHON = Path(
    "/vePFS-Mindverse/user/intern/jungao/Marcronv1_SWE/.venv312/bin/python"
)
DEFAULT_DATASET = Path(
    "/vePFS-Mindverse/user/intern/jungao/Marcronv1-Coding/deep-swe/tasks"
)
DEFAULT_BASE_SKILLS = (
    ROOT / "skills/downstream/"
    "swebench_verified_cc_novita_glm52_v0201_frozen_downstream_20260711_074608/"
    "v0201"
)
DEFAULT_POLICY = (
    ROOT / "run_logs/swegym_skill_evo/"
    "swegym_novita_glm52_c15_resume_merged_20260630_071956/"
    "training/policy_state.json"
)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def timestamp_id() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")


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
        value = value.strip().strip('"').strip("'")
        if key:
            values[key] = value
    return values


def provider_runtime_env(path: Path, provider: Any) -> dict[str, str]:
    process_env = dict(os.environ)
    populate_anthropic_provider_env(provider, process_env)
    env = read_env_file(path)
    env.update(process_env)
    return env


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


def infra_invalid_fingerprint(job_dir: Path) -> tuple[tuple[str, str, str], ...]:
    """Fingerprint invalid trial evidence so recovery cannot spin without work."""

    fingerprint: list[tuple[str, str, str]] = []
    for row in infra_invalid_trials(job_dir) if job_dir.is_dir() else []:
        trial_name = str(row.get("trial_name") or "")
        reason = str(row.get("reason") or "")
        result_path = job_dir / trial_name / "result.json"
        digest = (
            hashlib.sha256(result_path.read_bytes()).hexdigest()
            if result_path.is_file()
            else "missing"
        )
        fingerprint.append((trial_name, reason, digest))
    return tuple(sorted(fingerprint))


def dataset_task_names(dataset: Path) -> list[str]:
    names: list[str] = []
    for task_path in sorted(dataset.glob("*/task.toml")):
        try:
            payload = tomllib.loads(task_path.read_text(errors="strict"))
        except (OSError, UnicodeError, tomllib.TOMLDecodeError) as exc:
            raise ValueError(
                f"Invalid DeepSWE task metadata: {task_path}: {exc}"
            ) from exc
        name = (payload.get("task") or {}).get("name")
        if not isinstance(name, str) or not name:
            raise ValueError(f"Missing task.name in DeepSWE task metadata: {task_path}")
        names.append(name)
    if len(set(names)) != len(names):
        raise ValueError("DeepSWE dataset contains duplicate task names")
    return names


def task_set_sha256(task_names: list[str]) -> str:
    payload = ("\n".join(sorted(task_names)) + "\n").encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def validate_reused_frozen_execution(
    args: argparse.Namespace,
    report_path: Path,
    *,
    runtime_environment: dict[str, str],
) -> dict[str, Any]:
    """Bind a reused frozen report to the exact downstream execution settings."""

    try:
        report = json.loads(report_path.read_text(errors="strict"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"Invalid frozen execution report: {report_path}: {exc}") from exc
    if not isinstance(report, dict):
        raise ValueError("Frozen execution report must contain a JSON object")
    skill_tree_sha256 = validate_skill_contract(report, args.base_skill_root)
    requested = argparse.Namespace(
        benchmark_name="deepswe",
        harness="claude-code",
        provider=args.provider,
        provider_model=args.model,
        provider_anthropic_base_url=None,
        provider_base_url=None,
        provider_api=None,
        dataset=args.dataset,
        env_file=args.env_file,
        claude_max_turns=None,
        claude_max_budget_usd=None,
        agent_timeout_sec=args.agent_timeout_sec,
        agent_setup_timeout_sec=args.agent_setup_timeout_sec,
        e2b_sandbox_timeout_sec=14400,
        claude_sdk_version=args.claude_sdk_version,
        pi_version=args.pi_version,
        concurrency=args.concurrency,
    )
    allowed_code_changes = (
        frozenset({"scripts/run_benchmark.py"})
        if getattr(args, "allow_run_benchmark_code_change", False)
        else frozenset()
    )
    audit = validate_requested_execution(
        requested,
        report,
        args.base_skill_root,
        allowed_code_changes=allowed_code_changes,
        runtime_environment=runtime_environment,
    )
    return {
        "report_path": str(report_path),
        "report_sha256": hashlib.sha256(report_path.read_bytes()).hexdigest(),
        "base_skill_root": str(args.base_skill_root),
        "base_skill_tree_sha256": skill_tree_sha256,
        "code_change_reason": (
            "explicit run_benchmark infrastructure repair"
            if allowed_code_changes
            else None
        ),
        **audit,
    }


def validate_existing_frozen_report(
    path: Path,
    *,
    expected_trials: int,
    expected_run_id: str,
    expected_task_names: list[str],
    expected_skill_root: Path | None = None,
) -> Path:
    """Validate a frozen aggregate and its authoritative trial results."""

    report_path = path.expanduser().resolve()
    if not report_path.is_file():
        raise ValueError(f"Missing existing frozen score report: {report_path}")
    try:
        report = json.loads(report_path.read_text(errors="strict"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError(
            f"Invalid existing frozen score report: {report_path}: {exc}"
        ) from exc
    if not isinstance(report, dict):
        raise ValueError("Existing frozen score report must contain a JSON object")

    def require(condition: bool, detail: str) -> None:
        if not condition:
            raise ValueError(
                f"Existing frozen score report failed validation: {detail}"
            )

    require(
        len(expected_task_names) == expected_trials
        and len(set(expected_task_names)) == expected_trials,
        "expected dataset task identity is invalid",
    )
    require(report.get("schema_version") == 1, "schema_version must be 1")
    require(report.get("complete") is True, "complete must be true")
    require(report.get("run_id") == expected_run_id, "run_id mismatch")
    require(report.get("benchmark_name") == "deepswe", "benchmark must be deepswe")
    require(report.get("infra_invalid_trials") == [], "infra-invalid trials remain")
    is_taskwise_or = report.get("kind") == TASKWISE_OR_AGGREGATE_KIND

    completeness = report.get("completeness")
    require(isinstance(completeness, dict), "missing completeness object")
    require(
        type(completeness.get("expected_trials")) is int
        and completeness["expected_trials"] == expected_trials,
        "completeness.expected_trials mismatch",
    )
    require(
        type(completeness.get("trial_result_files")) is int
        and completeness["trial_result_files"] == expected_trials,
        "completeness.trial_result_files mismatch",
    )
    require(
        type(completeness.get("infra_invalid_trials")) is int
        and completeness["infra_invalid_trials"] == 0,
        "completeness.infra_invalid_trials must be 0",
    )
    if is_taskwise_or:
        require(
            bool(completeness.get("aggregated_at")),
            "missing completeness.aggregated_at",
        )
    else:
        require(
            bool(completeness.get("finished_at")),
            "missing completeness.finished_at",
        )

    evaluation = report.get("evaluation")
    require(isinstance(evaluation, dict), "missing evaluation object")
    require(evaluation.get("job_name") == expected_run_id, "evaluation job mismatch")
    require(
        type(evaluation.get("n_trials")) is int
        and evaluation["n_trials"] == expected_trials,
        "evaluation.n_trials mismatch",
    )
    report_tasks = report.get("tasks")
    evaluation_tasks = evaluation.get("tasks")
    require(isinstance(report_tasks, list), "tasks must be a list")
    require(isinstance(evaluation_tasks, list), "evaluation.tasks must be a list")
    require(len(report_tasks) == expected_trials, "tasks length mismatch")
    require(
        len(evaluation_tasks) == expected_trials, "evaluation.tasks length mismatch"
    )
    require(report_tasks == evaluation_tasks, "top-level and evaluation tasks differ")
    require(
        all(isinstance(row, dict) for row in report_tasks),
        "task rows must be objects",
    )

    observed_task_names = [row.get("task_name") for row in report_tasks]
    observed_trial_names = [row.get("trial_name") for row in report_tasks]
    require(
        all(isinstance(name, str) and name for name in observed_task_names),
        "task row has an invalid task_name",
    )
    require(
        all(isinstance(name, str) and name for name in observed_trial_names),
        "task row has an invalid trial_name",
    )
    require(
        len(set(observed_task_names)) == expected_trials,
        "task names are not unique",
    )
    require(
        len(set(observed_trial_names)) == expected_trials,
        "trial names are not unique",
    )
    require(
        sorted(observed_task_names) == sorted(expected_task_names),
        "task set does not match the requested dataset",
    )

    if is_taskwise_or:
        try:
            source_reports = validate_taskwise_or_aggregate(
                report,
                report_path,
                expected_skill_root=expected_skill_root,
            )
        except (ValueError, SystemExit) as exc:
            raise ValueError(
                "Existing frozen score report failed validation: "
                f"invalid taskwise OR lineage: {exc}"
            ) from exc
        for source, source_path, source_report in source_reports:
            source_rows = source_report.get("tasks") or (
                source_report.get("evaluation") or {}
            ).get("tasks")
            require(
                isinstance(source_rows, list)
                and all(isinstance(row, dict) for row in source_rows),
                f"sample {source['sample']} source tasks are invalid",
            )
            source_task_names = [str(row.get("task_name") or "") for row in source_rows]
            validate_existing_frozen_report(
                source_path,
                expected_trials=len(source_task_names),
                expected_run_id=str(source["run_id"]),
                expected_task_names=source_task_names,
            )
        return report_path

    job_dir_value = evaluation.get("job_dir")
    require(
        isinstance(job_dir_value, str) and job_dir_value, "missing evaluation.job_dir"
    )
    job_dir = Path(job_dir_value).expanduser().resolve()
    require(job_dir.is_dir(), f"missing source job directory: {job_dir}")
    require(job_dir.name == expected_run_id, "source job directory name mismatch")
    root_result_path = job_dir / "result.json"
    job_config_path = job_dir / "config.json"
    require(
        root_result_path.is_file(), f"missing source job result: {root_result_path}"
    )
    require(job_config_path.is_file(), f"missing source job config: {job_config_path}")
    try:
        root_result = json.loads(root_result_path.read_text(errors="strict"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError(
            f"Invalid source job result: {root_result_path}: {exc}"
        ) from exc
    require(isinstance(root_result, dict), "source job result must be a JSON object")
    require(
        root_result.get("finished_at") == completeness["finished_at"],
        "source job finished_at differs from the report",
    )
    require(
        type(root_result.get("n_total_trials")) is int
        and root_result["n_total_trials"] == expected_trials,
        "source job total trial count mismatch",
    )

    provenance = report.get("provenance")
    require(isinstance(provenance, dict), "missing provenance object")
    require(
        provenance.get("benchmark_name") == "deepswe", "provenance benchmark mismatch"
    )
    require(
        provenance.get("job_config_sha256")
        == hashlib.sha256(job_config_path.read_bytes()).hexdigest(),
        "source job config hash mismatch",
    )
    require(
        provenance.get("task_set_sha256") == task_set_sha256(expected_task_names),
        "task set hash mismatch",
    )

    try:
        current_evaluation = summarize_job(job_dir)
    except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
        raise ValueError(
            f"Could not summarize source frozen job: {job_dir}: {exc}"
        ) from exc
    require(
        current_evaluation == evaluation,
        "source trial results no longer match the score report",
    )
    current_infra_invalid = infra_invalid_trials(job_dir)
    require(
        current_infra_invalid == [],
        f"source job now has infra-invalid trials: {current_infra_invalid}",
    )
    identity_issues, configured_trials = job_identity_issues(job_dir)
    require(identity_issues == [], f"source job identity issues: {identity_issues}")
    require(
        configured_trials == expected_trials,
        "source job configured trial count mismatch",
    )
    return report_path


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
    job_dir = ROOT / "jobs" / job_name
    previous_invalid = infra_invalid_fingerprint(job_dir)
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
        current_invalid = infra_invalid_fingerprint(job_dir)
        if current_invalid and current_invalid == previous_invalid:
            details = ", ".join(
                f"{trial_name}:{reason}"
                for trial_name, reason, _digest in current_invalid
            )
            raise RuntimeError(
                "DeepSWE recovery made no progress and will not rerun the model: "
                f"{details}"
            )
        previous_invalid = current_invalid
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
        "--existing-frozen-report",
        type=Path,
        default=None,
        help=(
            "Reuse a completed frozen score_report.json after validating it and "
            "all 113 source trial results; gate 1 and later gates still run."
        ),
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
    parser.add_argument(
        "--allow-run-benchmark-code-change",
        action="store_true",
        help=(
            "Allow exactly scripts/run_benchmark.py to differ from a reused frozen "
            "report, for an explicitly audited infrastructure repair."
        ),
    )
    return parser.parse_args()


def run_full(args: argparse.Namespace, state_path: Path) -> None:
    args.dataset = args.dataset.expanduser().resolve()
    args.base_skill_root = args.base_skill_root.expanduser().resolve()
    args.policy_state = args.policy_state.expanduser().resolve()
    args.env_file = args.env_file.expanduser().resolve()
    args.python = absolute_path_preserving_symlinks(args.python)
    if args.existing_frozen_report is not None:
        args.existing_frozen_report = args.existing_frozen_report.expanduser().resolve()
    if (
        getattr(args, "allow_run_benchmark_code_change", False)
        and args.existing_frozen_report is None
    ):
        raise SystemExit(
            "--allow-run-benchmark-code-change requires --existing-frozen-report"
        )
    if not 2 <= args.max_gate <= 4:
        raise SystemExit("--max-gate must be between 2 and 4.")
    for path, label in (
        (args.dataset, "DeepSWE dataset"),
        (args.base_skill_root, "base skill root"),
        (args.policy_state, "evaluator policy"),
        (args.python, "Python executable"),
    ):
        if not path.exists():
            raise SystemExit(f"Missing {label}: {path}")
    validate_child_python(args.python)
    expected_task_names = dataset_task_names(args.dataset)
    expected_trials = len(expected_task_names)
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

    provider = resolve_provider(args.provider)
    env = provider_runtime_env(args.env_file, provider)
    env.update(
        {
            "LLM_PROVIDER": args.provider,
            "CLAUDE_CODE_ATTRIBUTION_HEADER": "0",
            "FORCE_DISABLE_THINKING": "1",
            "PI_THINKING": "off",
            "OPENAI_COMPAT_REASONING_EFFORT": "none",
            "OPENAI_COMPAT_ENABLE_THINKING": "false",
            f"{provider.env_prefix}_REASONING_EFFORT": "none",
            f"{provider.env_prefix}_ENABLE_THINKING": "false",
            "FORCE_AGENT_INTERNET": "1",
            "PI_USE_SKILL_HARNESS_MEMORY": "false",
            "CLAUDE_USE_SKILL_HARNESS_MEMORY": "false",
            "PI_SKILL_RETRIEVAL_SCOPE": "transfer",
            "TIMEOUT_MULTIPLIER": "1.0",
            "AGENT_TIMEOUT_MULTIPLIER": "",
            "VERIFIER_TIMEOUT_MULTIPLIER": "",
            "AGENT_SETUP_TIMEOUT_MULTIPLIER": "2.0",
            "ENVIRONMENT_BUILD_TIMEOUT_MULTIPLIER": "2.0",
            "CLAUDE_CODE_MAX_OUTPUT_TOKENS": "",
            "CLAUDE_AGENT_SDK_VERSION": args.claude_sdk_version,
            "PI_CODING_AGENT_VERSION": args.pi_version,
            "E2B_CONCURRENCY": str(args.concurrency),
            "TMPDIR": str(tmp_dir),
            "TMP": str(tmp_dir),
            "TEMP": str(tmp_dir),
        }
    )
    missing_env = ["E2B_API_KEY"] + [
        name for name in provider.required_env(agent="claude-code") if not env.get(name)
    ]
    missing_env = [name for name in dict.fromkeys(missing_env) if not env.get(name)]
    if missing_env:
        raise SystemExit(
            "Missing required environment variables: " + ", ".join(missing_env)
        )

    log_dir = run_dir / "logs"
    state = {
        "schema_version": 1,
        "launcher_id": args.launcher_id,
        "frozen_run_id": args.frozen_run_id,
        "tts_run_id": args.tts_run_id,
        "max_gate": args.max_gate,
        "expected_trials": expected_trials,
        "tmp_dir": str(tmp_dir),
        "tmp_free_bytes_at_start": tmp_free_bytes,
        "started_at": utc_now(),
        "status": "running",
        "current_step": (
            "validate_existing_frozen_report"
            if args.existing_frozen_report is not None
            else "frozen_eval"
        ),
        "existing_frozen_report": (
            str(args.existing_frozen_report)
            if args.existing_frozen_report is not None
            else None
        ),
    }
    write_json_atomic(state_path, state)

    if args.existing_frozen_report is not None:
        frozen_report = validate_existing_frozen_report(
            args.existing_frozen_report,
            expected_trials=expected_trials,
            expected_run_id=args.frozen_run_id,
            expected_task_names=expected_task_names,
            expected_skill_root=args.base_skill_root,
        )
        state["frozen_execution_contract"] = validate_reused_frozen_execution(
            args,
            frozen_report,
            runtime_environment=env,
        )
        state["frozen_report_reused"] = True
    else:
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
        state["frozen_report_reused"] = False
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
    ]
    if (
        run_logged(
            command=materialize_command,
            env=env,
            log_path=log_dir / "materialize_gate_1.log",
        )
        != 0
    ):
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
    if (
        run_logged(
            command=attach_gate0,
            env=env,
            log_path=log_dir / "attach_gate_0.log",
        )
        != 0
    ):
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
    if (
        run_logged(
            command=attach_gate1,
            env=env,
            log_path=log_dir / "attach_gate_1.log",
        )
        != 0
    ):
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
    ]
    if (
        run_logged(
            command=subset_command,
            env=env,
            log_path=log_dir / "subset_gates_2_to_4.log",
        )
        != 0
    ):
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
