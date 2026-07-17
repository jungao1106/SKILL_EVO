#!/usr/bin/env python
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import tempfile
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in os.sys.path:
    os.sys.path.insert(0, str(ROOT))

from scripts.job_run_lock import exclusive_job_run, job_is_running  # noqa: E402
from scripts.materialize_deepswe_tts_evolution_gates import (  # noqa: E402
    sha256_file,
    sha256_tree,
    validate_source_aggregate,
)
from scripts.run_swebench_tts_subset_evo_loop import (  # noqa: E402
    dataset_filter_task_names,
    run_subset_eval,
    safe_slug,
    subset_execution_payload,
)
from scripts.run_benchmark import DEEPSWE_ARTIFACT_HOOK_VERSION  # noqa: E402


@dataclass(frozen=True)
class Setting:
    name: str
    gate_index: int
    source_report_path: Path
    skill_root: Path


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(errors="strict"))


def write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    serialized = (
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    )
    with tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        dir=path.parent,
        prefix=f".{path.name}.",
        delete=False,
    ) as handle:
        handle.write(serialized)
        temporary = Path(handle.name)
    os.replace(temporary, path)


def report_rows(report: dict[str, Any]) -> list[dict[str, Any]]:
    rows = report.get("tasks")
    if not isinstance(rows, list):
        evaluation = report.get("evaluation") or {}
        rows = evaluation.get("tasks")
    if not isinstance(rows, list) or not all(isinstance(row, dict) for row in rows):
        raise ValueError("Source report does not contain task rows")
    return rows


def reward_value(row: dict[str, Any]) -> float | None:
    value = row.get("reward")
    if isinstance(value, bool) or value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def is_reward_one(row: dict[str, Any]) -> bool:
    return reward_value(row) == 1.0


def task_name(row: dict[str, Any]) -> str:
    value = str(row.get("task_name") or "")
    if not value:
        raise ValueError(f"Task row has no task_name: {row}")
    return value


def retry_task_names(report: dict[str, Any]) -> set[str]:
    retry: set[str] = set()
    for row in report_rows(report):
        reward = reward_value(row)
        if reward not in {0.0, 1.0}:
            raise ValueError(
                "BoN sample 2 requires a complete binary-reward source report: "
                f"task={task_name(row)} reward={row.get('reward')!r}"
            )
        if reward == 0.0:
            retry.add(task_name(row))
    return retry


def source_job_dir(report: dict[str, Any]) -> Path:
    evaluation = report.get("evaluation") or {}
    value = evaluation.get("job_dir") or report.get("job_dir")
    if not isinstance(value, str) or not value:
        raise ValueError("Source report does not identify its job directory")
    path = Path(value).expanduser().resolve()
    if not path.is_dir():
        raise ValueError(f"Source job directory does not exist: {path}")
    return path


def source_resume_contract(report: dict[str, Any]) -> dict[str, Any]:
    provenance = report.get("provenance") or {}
    contract = provenance.get("resume_contract")
    if isinstance(contract, dict):
        return contract
    config_path = source_job_dir(report) / "config.json"
    config = read_json(config_path)
    agents = config.get("agents") or []
    kwargs = (agents[0] or {}).get("kwargs") if agents else None
    contract = (kwargs or {}).get("resume_contract")
    if not isinstance(contract, dict):
        raise ValueError(f"Source job has no resume contract: {config_path}")
    return contract


def validate_skill_contract(report: dict[str, Any], skill_root: Path) -> str:
    skill_root = skill_root.expanduser().resolve()
    if not skill_root.is_dir():
        raise ValueError(f"Skill root does not exist: {skill_root}")
    contract = source_resume_contract(report)
    skills = contract.get("skills") or {}
    roots = [
        str(Path(value).expanduser().resolve()) for value in skills.get("roots") or []
    ]
    expected_root = str(skill_root)
    if roots != [expected_root]:
        raise ValueError(
            "Source report skill root differs from the requested same-setting root: "
            f"source={roots} requested={[expected_root]}"
        )
    hashes = skills.get("tree_sha256") or []
    current_hash = sha256_tree(skill_root)
    if hashes != [current_hash]:
        raise ValueError(
            "Source setting skill tree changed after evaluation: "
            f"source={hashes} current={[current_hash]}"
        )
    return current_hash


def normalized_job_config(config: dict[str, Any]) -> dict[str, Any]:
    normalized = json.loads(json.dumps(config))
    normalized["job_name"] = "<same-setting-job>"
    retry = normalized.get("retry") or {}
    for key in ("include_exceptions", "exclude_exceptions"):
        if isinstance(retry.get(key), list):
            retry[key] = sorted(retry[key])
    for dataset in normalized.get("datasets") or []:
        dataset["task_names"] = []
    for agent in normalized.get("agents") or []:
        contract = (agent.get("kwargs") or {}).get("resume_contract") or {}
        dataset = contract.get("dataset") or {}
        dataset["task_names"] = []
    return normalized


def validate_attempt_contract(
    source_report: dict[str, Any],
    sample_2_report: dict[str, Any],
) -> None:
    source_contract = source_resume_contract(source_report)
    sample_2_contract = source_resume_contract(sample_2_report)
    for key in (
        "version",
        "artifact_hook_version",
        "artifact_hook_enabled",
        "force_agent_internet",
        "provider",
        "agent_parameters",
        "skills",
        "runtime_knobs",
        "dependency_versions",
        "code_sha256",
    ):
        if sample_2_contract.get(key) != source_contract.get(key):
            raise ValueError(f"Sample-2 execution contract differs at {key}")
    source_dataset = source_contract.get("dataset") or {}
    sample_2_dataset = sample_2_contract.get("dataset") or {}
    for key in ("path", "tree_sha256"):
        if sample_2_dataset.get(key) != source_dataset.get(key):
            raise ValueError(f"Sample-2 dataset contract differs at {key}")

    source_config = read_json(source_job_dir(source_report) / "config.json")
    sample_2_config = read_json(source_job_dir(sample_2_report) / "config.json")
    if normalized_job_config(sample_2_config) != normalized_job_config(source_config):
        raise ValueError(
            "Sample-2 job config differs from sample 1 beyond job/task identity"
        )


def validate_requested_execution(
    args: argparse.Namespace,
    source_report: dict[str, Any],
    skill_root: Path,
) -> None:
    source_contract = source_resume_contract(source_report)
    requested = subset_execution_payload(args, skill_root)
    if source_contract.get("artifact_hook_version") != DEEPSWE_ARTIFACT_HOOK_VERSION:
        raise ValueError("Current artifact hook differs from the source setting")
    if source_contract.get("artifact_hook_enabled") is not True:
        raise ValueError("Source setting did not enable the required artifact hook")
    if source_contract.get("provider") != requested.get("provider"):
        raise ValueError(
            "Requested provider/endpoint/model differs from source setting"
        )
    if source_contract.get("agent_parameters") != requested.get("agent_parameters"):
        raise ValueError("Requested agent parameters differ from source setting")
    if source_contract.get("runtime_knobs") != requested.get("runtime_knobs"):
        raise ValueError("Requested runtime knobs differ from source setting")
    source_dataset = source_contract.get("dataset") or {}
    for source_key, requested_key in (
        ("path", "dataset"),
        ("tree_sha256", "dataset_tree_sha256"),
    ):
        if source_dataset.get(source_key) != requested.get(requested_key):
            raise ValueError(f"Requested dataset differs at {source_key}")
    source_dependencies = source_contract.get("dependency_versions") or {}
    dependency_name = (
        "claude-agent-sdk" if args.harness == "claude-code" else "pi-coding-agent"
    )
    if source_dependencies.get(dependency_name) != (
        requested.get("dependency_versions") or {}
    ).get(dependency_name):
        raise ValueError(f"Requested dependency differs at {dependency_name}")
    source_code = source_contract.get("code_sha256") or {}
    requested_code = requested.get("code_sha256") or {}
    contract_code_paths = {
        "agents/claude_sdk_agent.py",
        "agents/pi_agent.py",
        "agents/skill_harness_memory.py",
        "environments/e2b_swebench.py",
        "providers/__init__.py",
        "providers/specs.py",
        "scripts/run_benchmark.py",
    }
    if any(
        source_code.get(path) != requested_code.get(path)
        for path in contract_code_paths
    ):
        raise ValueError("Current benchmark code differs from the source setting")

    source_config = read_json(source_job_dir(source_report) / "config.json")
    agents = source_config.get("agents") or []
    source_agent = agents[0] if agents else {}
    environment = source_config.get("environment") or {}
    environment_kwargs = environment.get("kwargs") or {}
    expected_values = {
        "concurrency": (source_config.get("n_concurrent_trials"), args.concurrency),
        "agent_timeout_sec": (
            source_agent.get("override_timeout_sec"),
            args.agent_timeout_sec,
        ),
        "agent_setup_timeout_sec": (
            source_agent.get("override_setup_timeout_sec"),
            args.agent_setup_timeout_sec,
        ),
        "sandbox_timeout_sec": (
            environment_kwargs.get("sandbox_timeout_sec"),
            args.e2b_sandbox_timeout_sec,
        ),
        "cpus": (environment.get("override_cpus"), 2),
        "memory_mb": (environment.get("override_memory_mb"), 8192),
        "storage_mb": (environment.get("override_storage_mb"), 20480),
    }
    for label, (source_value, requested_value) in expected_values.items():
        if source_value is None or float(source_value) != float(requested_value):
            raise ValueError(
                f"Requested {label} differs from source setting: "
                f"source={source_value} requested={requested_value}"
            )


def discover_settings(
    *,
    launcher_dir: Path,
    tts_run_dir: Path,
    max_gate: int,
) -> list[Setting]:
    state = read_json(launcher_dir / "state.json")
    expected_tts_run_id = str(state.get("tts_run_id") or "")
    if not expected_tts_run_id or tts_run_dir.name != expected_tts_run_id:
        raise ValueError(
            "Launcher and TTS run directory do not belong to the same run: "
            f"launcher={expected_tts_run_id!r} tts={tts_run_dir.name!r}"
        )
    frozen_path = Path(str(state.get("frozen_report") or "")).expanduser().resolve()
    gate1_path = Path(str(state.get("gate_1_report") or "")).expanduser().resolve()
    if not frozen_path.is_file() or not gate1_path.is_file():
        raise ValueError("Frozen and Gate 1 reports must exist before BoN planning")

    frozen_report = read_json(frozen_path)
    frozen_roots = (source_resume_contract(frozen_report).get("skills") or {}).get(
        "roots"
    ) or []
    gate1_report = read_json(gate1_path)
    gate1_roots = (source_resume_contract(gate1_report).get("skills") or {}).get(
        "roots"
    ) or []
    if len(frozen_roots) != 1 or len(gate1_roots) != 1:
        raise ValueError(
            "Frozen and Gate 1 reports must each record exactly one skill root"
        )

    settings = [
        Setting("frozen", 0, frozen_path, Path(frozen_roots[0]).expanduser().resolve()),
        Setting("gate_001", 1, gate1_path, Path(gate1_roots[0]).expanduser().resolve()),
    ]
    manifest_path = tts_run_dir / "manifest.json"
    manifest = read_json(manifest_path)
    if manifest.get("run_id") != expected_tts_run_id:
        raise ValueError("TTS manifest run_id differs from the launcher state")
    gates = {
        int(row.get("gate_index") or 0): row
        for row in manifest.get("gates") or []
        if isinstance(row, dict)
    }
    gate0_report = (gates.get(0) or {}).get("verifier_report") or {}
    gate1_manifest_report = (gates.get(1) or {}).get("verifier_report") or {}
    if (
        Path(str(gate0_report.get("aggregate_path") or "")).expanduser().resolve()
        != frozen_path
    ):
        raise ValueError("TTS Gate 0 is not attached to the launcher frozen report")
    if gate0_report.get("eval_run_id") != state.get("frozen_run_id"):
        raise ValueError("TTS Gate 0 eval run differs from the launcher frozen run")
    if (
        Path(str(gate1_manifest_report.get("aggregate_path") or ""))
        .expanduser()
        .resolve()
        != gate1_path
    ):
        raise ValueError("TTS Gate 1 is not attached to the launcher Gate 1 report")
    for gate_index in range(2, max_gate + 1):
        gate = gates.get(gate_index)
        verifier_report = (gate or {}).get("verifier_report") or {}
        report_value = verifier_report.get("aggregate_path")
        skill_value = (gate or {}).get("skill_root")
        if not report_value or not skill_value:
            continue
        report_path = Path(str(report_value)).expanduser().resolve()
        if not report_path.is_file():
            continue
        settings.append(
            Setting(
                f"gate_{gate_index:03d}",
                gate_index,
                report_path,
                Path(str(skill_value)).expanduser().resolve(),
            )
        )
    return settings


def paths_overlap(left: Path, right: Path) -> bool:
    left = left.expanduser().resolve()
    right = right.expanduser().resolve()
    return left == right or left.is_relative_to(right) or right.is_relative_to(left)


def validate_output_root(
    *,
    output_root: Path,
    launcher_dir: Path,
    tts_run_dir: Path,
    settings: list[Setting],
) -> None:
    output_root = output_root.expanduser().resolve()
    launcher_dir = launcher_dir.expanduser().resolve()
    if output_root == launcher_dir or launcher_dir.is_relative_to(output_root):
        raise ValueError("BoN output directory would overwrite the source launcher")
    protected = [tts_run_dir, ROOT / "jobs"]
    for setting in settings:
        protected.extend(
            [
                setting.source_report_path.parent,
                setting.skill_root,
                source_job_dir(read_json(setting.source_report_path)),
            ]
        )
    for path in protected:
        if paths_overlap(output_root, path):
            raise ValueError(
                "BoN output directory overlaps immutable source data: "
                f"output={output_root} protected={path}"
            )


def validate_source_report(report: dict[str, Any], path: Path) -> None:
    validate_source_aggregate(
        report,
        path,
        expected_benchmark_name="deepswe",
    )
    if report.get("complete") is not True:
        raise ValueError(f"Source report is incomplete: {path}")
    if report.get("infra_invalid_trials") not in (None, []):
        raise ValueError(f"Source report contains infra-invalid trials: {path}")
    rows = report_rows(report)
    names = [task_name(row) for row in rows]
    if len(names) != len(set(names)):
        raise ValueError(f"Source report contains duplicate tasks: {path}")


def combine_task_rows(
    first_rows: list[dict[str, Any]],
    second_rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    first_by_task = {task_name(row): row for row in first_rows}
    second_by_task = {task_name(row): row for row in second_rows}
    expected_second = {
        name for name, row in first_by_task.items() if not is_reward_one(row)
    }
    if set(second_by_task) != expected_second:
        missing = sorted(expected_second - set(second_by_task))
        extra = sorted(set(second_by_task) - expected_second)
        raise ValueError(
            "Sample-2 task set differs from reward!=1 source tasks: "
            f"missing={missing[:10]} extra={extra[:10]}"
        )

    combined: list[dict[str, Any]] = []
    for name in sorted(first_by_task):
        first = first_by_task[name]
        second = second_by_task.get(name)
        first_reward = reward_value(first)
        second_reward = reward_value(second) if second is not None else None
        rewards = [
            value for value in (first_reward, second_reward) if value is not None
        ]
        best_reward = max(rewards) if rewards else None
        combined.append(
            {
                "task_name": name,
                "sample_1": {
                    "trial_name": first.get("trial_name"),
                    "reward": first_reward,
                    "exception_type": first.get("exception_type"),
                    "result_path": first.get("result_path"),
                },
                "sample_2": (
                    {
                        "trial_name": second.get("trial_name"),
                        "reward": second_reward,
                        "exception_type": second.get("exception_type"),
                        "result_path": second.get("result_path"),
                    }
                    if second is not None
                    else None
                ),
                "best_reward": best_reward,
                "passed": best_reward == 1.0,
            }
        )
    return combined


def setting_report(
    *,
    setting: Setting,
    source_report: dict[str, Any],
    skill_tree_sha256: str,
    sample_2_report: dict[str, Any],
    sample_2_report_path: Path,
    task_file: Path,
) -> dict[str, Any]:
    second_summary = sample_2_report.get("evaluation") or {}
    combined = combine_task_rows(
        report_rows(source_report),
        list(second_summary.get("tasks") or []),
    )
    first_passed = sum(1 for row in report_rows(source_report) if is_reward_one(row))
    second_passed = sum(
        1 for row in second_summary.get("tasks") or [] if is_reward_one(row)
    )
    best_passed = sum(1 for row in combined if row["passed"])
    total = len(combined)
    retry_count = len(second_summary.get("tasks") or [])
    return {
        "schema_version": 1,
        "kind": "deepswe_same_setting_best_of_2",
        "created_at": utc_now(),
        "benchmark_name": "deepswe",
        "setting": setting.name,
        "gate_index": setting.gate_index,
        "complete": True,
        "sampling_policy": {
            "n": 2,
            "sample_2_subset": "sample_1_valid_reward_equal_to_0",
            "aggregation": "maximum reward per task within the same skill setting",
            "cross_gate_aggregation": False,
            "infra_retries_count_as_samples": False,
            "tts_feedback": False,
        },
        "source": {
            "report_path": str(setting.source_report_path),
            "report_sha256": sha256_file(setting.source_report_path),
            "job_dir": str(source_job_dir(source_report)),
        },
        "sample_2": {
            "report_path": str(sample_2_report_path),
            "report_sha256": sha256_file(sample_2_report_path),
            "job_dir": str(source_job_dir(sample_2_report)),
            "job_name": source_job_dir(sample_2_report).name,
            "task_file": str(task_file),
            "n_trials": retry_count,
            "n_errors": second_summary.get("n_errors"),
        },
        "skills": {
            "root": str(setting.skill_root),
            "tree_sha256": skill_tree_sha256,
        },
        "evaluation": {
            "n_tasks": total,
            "denominator": total,
            "cohort_scope": (
                "full_113_task_dataset"
                if setting.gate_index <= 1
                else "previous_gate_unresolved_subset"
            ),
            "is_full_dataset_rate": setting.gate_index <= 1,
            "sample_1_passed": first_passed,
            "sample_2_eligible": retry_count,
            "sample_2_passed": second_passed,
            "sample_2_new_passed": best_passed - first_passed,
            "best_of_2_passed": best_passed,
            "best_of_2_pass_rate": best_passed / total if total else 0.0,
        },
        "tasks": combined,
    }


def aggregate_sample_2(
    *,
    args: argparse.Namespace,
    job_dir: Path,
    output_dir: Path,
    expected_trials: int,
) -> tuple[dict[str, Any], Path]:
    report_path = output_dir / "score_report.json"
    command = [
        args.python,
        "scripts/aggregate_benchmark_job.py",
        "--run-id",
        job_dir.name,
        "--job-dir",
        str(job_dir),
        "--out-dir",
        str(output_dir),
        "--expected-trials",
        str(expected_trials),
    ]
    output_dir.mkdir(parents=True, exist_ok=True)
    with (output_dir / "aggregate.log").open("a", encoding="utf-8") as log:
        completed = subprocess.run(
            command,
            cwd=ROOT,
            stdout=log,
            stderr=subprocess.STDOUT,
            check=False,
        )
    if completed.returncode != 0 or not report_path.is_file():
        raise RuntimeError(
            f"Could not aggregate sample-2 job {job_dir.name}: rc={completed.returncode}"
        )
    report = read_json(report_path)
    validate_source_report(report, report_path)
    return report, report_path


def plan_rows(
    settings: list[Setting],
    *,
    args: argparse.Namespace | None = None,
) -> list[dict[str, Any]]:
    plan: list[dict[str, Any]] = []
    for setting in settings:
        report = read_json(setting.source_report_path)
        validate_source_report(report, setting.source_report_path)
        skill_hash = validate_skill_contract(report, setting.skill_root)
        if args is not None:
            validate_requested_execution(args, report, setting.skill_root)
        rows = report_rows(report)
        plan.append(
            {
                "setting": setting.name,
                "gate_index": setting.gate_index,
                "source_report": str(setting.source_report_path),
                "source_tasks": len(rows),
                "sample_1_passed": sum(1 for row in rows if is_reward_one(row)),
                "sample_2_tasks": len(retry_task_names(report)),
                "skill_root": str(setting.skill_root),
                "skill_tree_sha256": skill_hash,
            }
        )
    return plan


def wait_for_evolution(
    state_path: Path,
    launcher_dir: Path,
    poll_sec: float,
    timeout_sec: float,
) -> None:
    started = time.monotonic()
    while True:
        state = read_json(state_path)
        status = str(state.get("status") or "")
        if status == "complete":
            return
        if status == "failed":
            raise RuntimeError(
                "Source evolution failed before BoN sampling: "
                f"{state.get('error_type')}: {state.get('error')}"
            )
        if not job_is_running(launcher_dir):
            raise RuntimeError(
                "Source evolution is marked running but no process holds its run lock"
            )
        if time.monotonic() - started > timeout_sec:
            raise TimeoutError(
                f"Timed out waiting {timeout_sec:g}s for source evolution"
            )
        print(
            f"[{utc_now()}] waiting for source evolution: "
            f"status={status} step={state.get('current_step')}",
            flush=True,
        )
        time.sleep(poll_sec)


def wait_for_temp_space(
    temp_dir: Path,
    min_free_gb: float,
    poll_sec: float,
    timeout_sec: float,
) -> None:
    required = int(min_free_gb * 1024**3)
    started = time.monotonic()
    while shutil.disk_usage(temp_dir).free < required:
        free_gb = shutil.disk_usage(temp_dir).free / 1024**3
        print(
            f"[{utc_now()}] waiting for benchmark temp space: "
            f"free={free_gb:.2f}GiB required={min_free_gb:.2f}GiB",
            flush=True,
        )
        if time.monotonic() - started > timeout_sec:
            raise TimeoutError(
                f"Timed out waiting {timeout_sec:g}s for benchmark temp space"
            )
        time.sleep(poll_sec)


def run_setting(
    *,
    args: argparse.Namespace,
    setting: Setting,
    output_root: Path,
) -> dict[str, Any]:
    source_report = read_json(setting.source_report_path)
    validate_source_report(source_report, setting.source_report_path)
    skill_hash = validate_skill_contract(source_report, setting.skill_root)
    validate_requested_execution(args, source_report, setting.skill_root)
    logical_tasks = retry_task_names(source_report)
    if not logical_tasks:
        raise ValueError(f"Setting has no reward!=1 tasks: {setting.name}")
    dataset_filters = dataset_filter_task_names(str(args.dataset), logical_tasks)
    setting_dir = output_root / setting.name
    task_file = setting_dir / "sample_2_reward_ne_1_tasks.txt"
    task_file.parent.mkdir(parents=True, exist_ok=True)
    task_file.write_text("\n".join(sorted(dataset_filters)) + "\n", encoding="utf-8")

    identity = hashlib.sha256(
        (
            sha256_file(setting.source_report_path)
            + skill_hash
            + "\n".join(sorted(logical_tasks))
        ).encode("utf-8")
    ).hexdigest()[:12]
    source_name = source_job_dir(source_report).name
    job_name = safe_slug(
        f"{source_name}_same_setting_bon2_s2_{identity}",
        limit=150,
    )
    job_dir = run_subset_eval(
        args=args,
        gate_index=setting.gate_index,
        previous_gate_index=setting.gate_index,
        gate_root=setting.skill_root,
        task_file=task_file,
        expected_trials=len(logical_tasks),
        job_name=job_name,
        log_path=setting_dir / "sample_2.log",
    )
    sample_2_report, sample_2_report_path = aggregate_sample_2(
        args=args,
        job_dir=job_dir,
        output_dir=setting_dir / "sample_2_aggregate",
        expected_trials=len(logical_tasks),
    )
    validate_attempt_contract(source_report, sample_2_report)
    report = setting_report(
        setting=setting,
        source_report=source_report,
        skill_tree_sha256=skill_hash,
        sample_2_report=sample_2_report,
        sample_2_report_path=sample_2_report_path,
        task_file=task_file,
    )
    write_json_atomic(setting_dir / "best_of_2_report.json", report)
    return report


def summary_payload(reports: list[dict[str, Any]], *, status: str) -> dict[str, Any]:
    evo_successes: set[str] = set()
    settings: list[dict[str, Any]] = []
    for report in reports:
        passed = {
            str(row.get("task_name") or "")
            for row in report.get("tasks") or []
            if row.get("passed") is True
        }
        if int(report.get("gate_index") or 0) >= 1:
            evo_successes.update(passed)
        evaluation = report.get("evaluation") or {}
        settings.append(
            {
                "setting": report.get("setting"),
                "gate_index": report.get("gate_index"),
                **evaluation,
                "report_path": None,
                "evolution_cumulative_unique_passed": (
                    len(evo_successes)
                    if int(report.get("gate_index") or 0) >= 1
                    else None
                ),
            }
        )
    return {
        "schema_version": 1,
        "kind": "deepswe_same_setting_best_of_2_summary",
        "updated_at": utc_now(),
        "status": status,
        "cumulative_policy": (
            "adaptive cross-gate unique-success union; diagnostic only, not a "
            "same-setting full-dataset pass@2"
        ),
        "settings": settings,
        "evolution_cumulative_unique_passed": len(evo_successes),
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Run sample 2 only for reward!=1 tasks in each frozen DeepSWE skill "
            "setting, then report same-setting best-of-2."
        )
    )
    parser.add_argument("--launcher-dir", type=Path, required=True)
    parser.add_argument("--tts-run-dir", type=Path, required=True)
    parser.add_argument("--dataset", type=Path, required=True)
    parser.add_argument("--env-file", type=Path, required=True)
    parser.add_argument("--python", default=os.sys.executable)
    parser.add_argument("--provider", default="macaron")
    parser.add_argument("--provider-model", default="glm-5.2")
    parser.add_argument("--provider-base-url", default=None)
    parser.add_argument("--provider-anthropic-base-url", default=None)
    parser.add_argument("--provider-api", default=None)
    parser.add_argument("--harness", default="claude-code")
    parser.add_argument("--benchmark-name", default="deepswe")
    parser.add_argument("--claude-sdk-version", default="0.2.116")
    parser.add_argument("--pi-version", default="0.80.6")
    parser.add_argument("--claude-max-turns", type=int, default=None)
    parser.add_argument("--claude-max-budget-usd", type=float, default=None)
    parser.add_argument("--concurrency", type=int, default=8)
    parser.add_argument("--agent-timeout-sec", type=float, default=7200)
    parser.add_argument("--agent-setup-timeout-sec", type=float, default=1200)
    parser.add_argument("--e2b-sandbox-timeout-sec", type=int, default=14400)
    parser.add_argument("--recovery-rounds", type=int, default=4)
    parser.add_argument("--max-gate", type=int, default=4)
    parser.add_argument("--poll-sec", type=float, default=120)
    parser.add_argument("--min-temp-free-gb", type=float, default=5.0)
    parser.add_argument("--wait-timeout-sec", type=float, default=259200)
    parser.add_argument("--output-dir", type=Path, default=None)
    parser.add_argument("--plan-only", action="store_true")
    parser.add_argument("--no-wait", action="store_true")
    parser.add_argument(
        "--allow-paused-source",
        action="store_true",
        help=(
            "Allow --no-wait while the source launcher remains active. The operator "
            "must pause its evaluation coordinator so benchmark concurrency is not shared."
        ),
    )
    return parser.parse_args()


def run(args: argparse.Namespace) -> None:
    args.launcher_dir = args.launcher_dir.expanduser().resolve()
    args.tts_run_dir = args.tts_run_dir.expanduser().resolve()
    args.dataset = args.dataset.expanduser().resolve()
    args.env_file = args.env_file.expanduser().resolve()
    args.python = (
        str(Path(args.python).expanduser()) if "/" in args.python else args.python
    )
    output_root = (
        args.output_dir.expanduser().resolve()
        if args.output_dir is not None
        else args.launcher_dir / "bon_sampling"
    )
    state_path = args.launcher_dir / "state.json"
    temp_dir = args.launcher_dir / "tmp"
    temp_dir.mkdir(parents=True, exist_ok=True)
    for name in ("TMPDIR", "TMP", "TEMP"):
        os.environ[name] = str(temp_dir)

    if args.plan_only:
        settings = discover_settings(
            launcher_dir=args.launcher_dir,
            tts_run_dir=args.tts_run_dir,
            max_gate=args.max_gate,
        )
        source_state = read_json(state_path)
        print(
            json.dumps(
                {
                    "source_evolution_status": source_state.get("status"),
                    "settings": plan_rows(settings, args=args),
                },
                indent=2,
                sort_keys=True,
            )
        )
        return

    initial_settings = discover_settings(
        launcher_dir=args.launcher_dir,
        tts_run_dir=args.tts_run_dir,
        max_gate=args.max_gate,
    )
    validate_output_root(
        output_root=output_root,
        launcher_dir=args.launcher_dir,
        tts_run_dir=args.tts_run_dir,
        settings=initial_settings,
    )
    output_root.mkdir(parents=True, exist_ok=True)
    with exclusive_job_run(output_root):
        state_file = output_root / "state.json"
        try:
            source_state = read_json(state_path)
            if (
                args.no_wait
                and source_state.get("status") != "complete"
                and not args.allow_paused_source
            ):
                raise RuntimeError(
                    "--no-wait is allowed only after the source evolution is complete"
                )
            if args.allow_paused_source and not args.no_wait:
                raise RuntimeError("--allow-paused-source requires --no-wait")
            if args.allow_paused_source and source_state.get("status") != "running":
                raise RuntimeError(
                    "--allow-paused-source requires a source evolution marked running"
                )
            if not args.no_wait:
                write_json_atomic(
                    state_file,
                    {"status": "waiting_for_evolution", "updated_at": utc_now()},
                )
                wait_for_evolution(
                    state_path,
                    args.launcher_dir,
                    args.poll_sec,
                    args.wait_timeout_sec,
                )
            settings = discover_settings(
                launcher_dir=args.launcher_dir,
                tts_run_dir=args.tts_run_dir,
                max_gate=args.max_gate,
            )
            validate_output_root(
                output_root=output_root,
                launcher_dir=args.launcher_dir,
                tts_run_dir=args.tts_run_dir,
                settings=settings,
            )
            expected_names = ["frozen", "gate_001"] + [
                f"gate_{index:03d}" for index in range(2, args.max_gate + 1)
            ]
            observed_names = [setting.name for setting in settings]
            if observed_names != expected_names:
                raise RuntimeError(
                    "Source evolution did not produce every requested setting: "
                    f"expected={expected_names} observed={observed_names}"
                )
            write_json_atomic(
                output_root / "plan.json",
                {
                    "created_at": utc_now(),
                    "settings": plan_rows(settings, args=args),
                },
            )
            wait_for_temp_space(
                temp_dir,
                args.min_temp_free_gb,
                args.poll_sec,
                args.wait_timeout_sec,
            )
            reports: list[dict[str, Any]] = []
            for setting in settings:
                write_json_atomic(
                    state_file,
                    {
                        "status": "running",
                        "current_setting": setting.name,
                        "completed_settings": [row.get("setting") for row in reports],
                        "updated_at": utc_now(),
                    },
                )
                reports.append(
                    run_setting(args=args, setting=setting, output_root=output_root)
                )
                summary = summary_payload(reports, status="running")
                for row in summary["settings"]:
                    row["report_path"] = str(
                        output_root / str(row["setting"]) / "best_of_2_report.json"
                    )
                write_json_atomic(output_root / "summary.json", summary)
            summary = summary_payload(reports, status="complete")
            for row in summary["settings"]:
                row["report_path"] = str(
                    output_root / str(row["setting"]) / "best_of_2_report.json"
                )
            write_json_atomic(output_root / "summary.json", summary)
            write_json_atomic(
                state_file,
                {
                    "status": "complete",
                    "completed_settings": [row.get("setting") for row in reports],
                    "completed_at": utc_now(),
                    "summary": str(output_root / "summary.json"),
                },
            )
        except BaseException as exc:
            write_json_atomic(
                state_file,
                {
                    "status": "failed",
                    "failed_at": utc_now(),
                    "error_type": exc.__class__.__name__,
                    "error": str(exc),
                },
            )
            raise


def main() -> None:
    run(parse_args())


if __name__ == "__main__":
    main()
