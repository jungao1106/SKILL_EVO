#!/usr/bin/env python
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import statistics
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Callable


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

GATE_INDEX = 3
PREVIOUS_GATE_INDEX = 2
STATE_FILENAME = "repair_state.json"
VALID_TERMINAL_EXCEPTIONS = {"AgentTimeoutError", "NonZeroAgentExitCodeError"}
START_PATTERN = re.compile(r"\bSTART trial=(?P<trial>\S+) task=(?P<task>\S+)")


class RepairError(RuntimeError):
    pass


@dataclass(frozen=True)
class TrialRecord:
    trial_dir: Path
    result_path: Path
    task_name: str
    task_filter: str
    trial_name: str
    reward: int | None
    exception_type: str | None
    result_sha256: str
    config: dict[str, Any]
    result: dict[str, Any]


@dataclass(frozen=True)
class RepairContext:
    job_dir: Path
    tts_run_dir: Path
    manifest_path: Path
    gate_manifest_path: Path
    loop_state_path: Path
    task_file: Path
    gate_root: Path
    run_id: str
    job_name: str
    job_config_semantic_sha256: str
    manifest_sha256: str
    gate_manifest_sha256: str
    loop_state_sha256: str
    gate_tree_sha256: str
    previous_success_count: int
    task_filters: set[str]
    resume_contract: dict[str, Any]


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def require(condition: bool, message: str) -> None:
    if not condition:
        raise RepairError(message)


def read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(errors="replace"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RepairError(f"Invalid JSON file {path}: {exc}") from exc
    require(isinstance(value, dict), f"Expected a JSON object: {path}")
    return value


def json_bytes(value: dict[str, Any]) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")


def atomic_write(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        temporary.write_bytes(content)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def atomic_write_json(path: Path, value: dict[str, Any]) -> None:
    atomic_write(path, json_bytes(value))


def sha256_bytes(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def job_config_semantic_sha256(path: Path) -> str:
    """Hash the saved job contract without set-serialization ordering noise."""

    config = read_json(path)
    retry = config.get("retry")
    if isinstance(retry, dict):
        for key in ("include_exceptions", "exclude_exceptions"):
            values = retry.get(key)
            if isinstance(values, list):
                require(
                    len(values) == len(set(values)),
                    f"Duplicate retry exception in {path}: {key}",
                )
                retry[key] = sorted(values)
    canonical = json.dumps(
        config,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return sha256_bytes(canonical)


def sha256_tree(root: Path) -> str:
    require(root.is_dir(), f"Missing skill tree: {root}")
    digest = hashlib.sha256()
    for path in sorted(item for item in root.rglob("*") if item.is_file()):
        relative = path.relative_to(root).as_posix().encode("utf-8")
        digest.update(len(relative).to_bytes(8, "big"))
        digest.update(relative)
        digest.update(bytes.fromhex(sha256_file(path)))
    return digest.hexdigest()


def read_name_file(path: Path) -> list[str]:
    require(path.is_file(), f"Missing task-name file: {path}")
    names = [
        line.strip()
        for line in path.read_text(errors="replace").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]
    require(names, f"Task-name file is empty: {path}")
    require(len(names) == len(set(names)), f"Duplicate task names in {path}")
    return names


def binary_reward(result: dict[str, Any]) -> int | None:
    verifier = result.get("verifier_result")
    if not isinstance(verifier, dict):
        return None
    rewards = verifier.get("rewards")
    if not isinstance(rewards, dict):
        return None
    value = rewards.get("reward")
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    if float(value) not in {0.0, 1.0}:
        return None
    return int(value)


def exception_type(result: dict[str, Any]) -> str | None:
    info = result.get("exception_info")
    if not isinstance(info, dict):
        return None
    value = str(info.get("exception_type") or "")
    return value or None


def valid_binary_result(record: TrialRecord) -> bool:
    return record.reward in {0, 1} and (
        record.exception_type is None
        or record.exception_type in VALID_TERMINAL_EXCEPTIONS
    )


def trial_resume_incompatibility(
    record: TrialRecord,
    *,
    expected_contract: dict[str, Any],
) -> str | None:
    """Mirror the benchmark resume checks that could reschedule a valid trial."""

    from scripts.run_benchmark import (
        DEEPSWE_MIN_CPUS,
        DEEPSWE_MIN_MEMORY_MB,
        DEEPSWE_MIN_SANDBOX_TIMEOUT_SEC,
        DEEPSWE_MIN_STORAGE_MB,
        _deepswe_result_infra_reason,
    )

    agent = record.config.get("agent") or {}
    trial_contract = (agent.get("kwargs") or {}).get("resume_contract")
    if trial_contract != expected_contract:
        return "resume-contract"
    environment = record.config.get("environment") or {}
    environment_kwargs = environment.get("kwargs") or {}
    if int(environment.get("override_cpus") or 0) < DEEPSWE_MIN_CPUS:
        return "cpus"
    if int(environment.get("override_memory_mb") or 0) < DEEPSWE_MIN_MEMORY_MB:
        return "memory"
    if int(environment.get("override_storage_mb") or 0) < DEEPSWE_MIN_STORAGE_MB:
        return "storage"
    if (
        int(environment_kwargs.get("sandbox_timeout_sec") or 0)
        < DEEPSWE_MIN_SANDBOX_TIMEOUT_SEC
    ):
        return "sandbox-timeout"
    if environment_kwargs.get("force_allow_internet") is not True:
        return "agent-internet"
    reason = _deepswe_result_infra_reason(record.result)
    if reason is not None:
        return reason
    return None


def interrupted_cancelled_result(record: TrialRecord) -> bool:
    metadata = (record.result.get("agent_result") or {}).get("metadata") or {}
    return (
        record.exception_type == "CancelledError"
        and record.reward is None
        and metadata.get("completed") is False
        and metadata.get("termination") == "interrupted_or_timeout"
    )


def scan_trials(job_dir: Path) -> list[TrialRecord]:
    records: list[TrialRecord] = []
    for trial_dir in sorted(path for path in job_dir.iterdir() if path.is_dir()):
        require(
            not trial_dir.name.startswith("."),
            f"Unexpected hidden directory inside job: {trial_dir}",
        )
        config_path = trial_dir / "config.json"
        result_path = trial_dir / "result.json"
        require(config_path.is_file(), f"Missing trial config: {config_path}")
        require(result_path.is_file(), f"Missing trial result: {result_path}")
        config = read_json(config_path)
        result = read_json(result_path)
        task_name = str(result.get("task_name") or "")
        trial_name = str(result.get("trial_name") or "")
        require(task_name, f"Missing task_name in {result_path}")
        require(trial_name == trial_dir.name, f"Trial name mismatch in {result_path}")
        task_id = result.get("task_id")
        task_path = task_id.get("path") if isinstance(task_id, dict) else None
        require(
            isinstance(task_path, str) and task_path,
            f"Missing task path in {result_path}",
        )
        task_filter = Path(task_path).name
        embedded_config = result.get("config")
        require(
            isinstance(embedded_config, dict),
            f"Missing embedded config in {result_path}",
        )
        require(
            Path(str(embedded_config.get("trials_dir") or "")).resolve()
            == job_dir.resolve(),
            f"Embedded trials_dir mismatch in {result_path}",
        )
        records.append(
            TrialRecord(
                trial_dir=trial_dir,
                result_path=result_path,
                task_name=task_name,
                task_filter=task_filter,
                trial_name=trial_name,
                reward=binary_reward(result),
                exception_type=exception_type(result),
                result_sha256=sha256_file(result_path),
                config=config,
                result=result,
            )
        )
    task_names = [record.task_name for record in records]
    filters = [record.task_filter for record in records]
    require(len(task_names) == len(set(task_names)), "Duplicate logical task results")
    require(len(filters) == len(set(filters)), "Duplicate dataset task results")
    return records


def gate_entry(manifest: dict[str, Any], gate_index: int) -> dict[str, Any]:
    matches = [
        gate
        for gate in manifest.get("gates") or []
        if isinstance(gate, dict) and int(gate.get("gate_index") or 0) == gate_index
    ]
    require(len(matches) == 1, f"Expected exactly one gate_{gate_index:03d}")
    return matches[0]


def validate_no_gate4(
    tts_run_dir: Path, manifest: dict[str, Any], gate_root: Path
) -> None:
    indexes = [int(gate.get("gate_index") or 0) for gate in manifest.get("gates") or []]
    require(
        not any(index > GATE_INDEX for index in indexes),
        "Gate 4 or later already exists",
    )
    require(
        not (tts_run_dir / "gates" / "gate_004").exists(),
        "Gate 4 run directory already exists",
    )
    require(
        not (gate_root.parent / "gate_004").exists(),
        "Gate 4 skill tree already exists",
    )


def validate_context(
    *,
    job_dir: Path,
    tts_run_dir: Path,
    expected_total: int,
    require_pending_gate: bool,
) -> RepairContext:
    job_dir = job_dir.expanduser().resolve()
    tts_run_dir = tts_run_dir.expanduser().resolve()
    require(job_dir.is_dir(), f"Missing job directory: {job_dir}")
    require(tts_run_dir.is_dir(), f"Missing TTS run directory: {tts_run_dir}")

    job_config_path = job_dir / "config.json"
    root_result_path = job_dir / "result.json"
    manifest_path = tts_run_dir / "manifest.json"
    gate_manifest_path = tts_run_dir / "gates" / "gate_003" / "manifest.json"
    loop_state_path = tts_run_dir / "subset_eval" / "loop_state.json"
    task_file = tts_run_dir / "subsets" / "gate002_unresolved_tasks.txt"
    for path in (
        job_config_path,
        root_result_path,
        manifest_path,
        gate_manifest_path,
        loop_state_path,
        task_file,
    ):
        require(path.is_file(), f"Missing required repair input: {path}")

    job_config = read_json(job_config_path)
    root_result = read_json(root_result_path)
    manifest = read_json(manifest_path)
    gate_manifest = read_json(gate_manifest_path)
    loop_state = read_json(loop_state_path)
    run_id = str(manifest.get("run_id") or "")
    require(run_id, f"Missing run_id in {manifest_path}")
    require(
        job_config.get("job_name") == job_dir.name, "Job name does not match directory"
    )
    configured_job_dir = (
        Path(str(job_config.get("jobs_dir") or ""))
        / str(job_config.get("job_name") or "")
    ).resolve()
    require(
        configured_job_dir == job_dir, "Saved job config points to another directory"
    )
    require(job_config.get("n_attempts") == 1, "Repair supports exactly one attempt")
    require(
        root_result.get("n_total_trials") == expected_total,
        "Root job total does not match expected count",
    )

    datasets = job_config.get("datasets") or []
    agents = job_config.get("agents") or []
    require(
        len(datasets) == 1 and isinstance(datasets[0], dict), "Expected one dataset"
    )
    require(len(agents) == 1 and isinstance(agents[0], dict), "Expected one agent")
    configured_tasks = datasets[0].get("task_names") or []
    require(
        len(configured_tasks) == expected_total
        and len(configured_tasks) == len(set(configured_tasks)),
        "Saved dataset task set has the wrong size or duplicates",
    )
    task_filters = set(read_name_file(task_file))
    require(
        task_filters == set(configured_tasks),
        "Subset file differs from saved job task set",
    )

    agent_kwargs = agents[0].get("kwargs") or {}
    require(agent_kwargs.get("benchmark_name") == "deepswe", "Job is not a DeepSWE run")
    require(agent_kwargs.get("use_skills") is True, "Gate job did not enable skills")
    contract = agent_kwargs.get("resume_contract")
    require(isinstance(contract, dict), "Missing DeepSWE resume contract")
    contract_tasks = (contract.get("dataset") or {}).get("task_names") or []
    require(set(contract_tasks) == task_filters, "Resume contract task set mismatch")

    gate = gate_entry(manifest, GATE_INDEX)
    require(
        int(gate.get("source_previous_gate") or 0) == PREVIOUS_GATE_INDEX,
        "Gate 3 does not descend from Gate 2",
    )
    if require_pending_gate:
        require(
            (gate.get("verifier_report") or {}).get("status") == "subset_pending",
            "Gate 3 is not pending subset evaluation",
        )
    gate_root = Path(str(gate.get("skill_root") or "")).expanduser().resolve()
    gate_tree_sha256 = sha256_tree(gate_root)
    require(
        gate_manifest.get("output_tree_sha256") == gate_tree_sha256,
        "Frozen Gate 3 tree differs from its manifest",
    )
    skills_contract = contract.get("skills") or {}
    contract_roots = [
        Path(str(path)).expanduser().resolve()
        for path in skills_contract.get("roots") or []
    ]
    require(
        contract_roots == [gate_root], "Job resume contract uses another skill root"
    )
    require(
        skills_contract.get("tree_sha256") == [gate_tree_sha256],
        "Job resume contract uses another Gate 3 skill tree",
    )
    validate_no_gate4(tts_run_dir, manifest, gate_root)

    source_report_path = (
        Path(str(gate.get("source_aggregate") or "")).expanduser().resolve()
    )
    require(source_report_path.is_file(), "Missing Gate 2 source report")
    source_report = read_json(source_report_path)
    require(source_report.get("complete") is True, "Gate 2 source report is incomplete")
    source_rows = source_report.get("tasks")
    if not isinstance(source_rows, list):
        source_rows = (source_report.get("evaluation") or {}).get("tasks")
    require(
        isinstance(source_rows, list)
        and all(isinstance(row, dict) for row in source_rows),
        "Gate 2 source report has no task rows",
    )
    source_rewards: dict[str, int] = {}
    for row in source_rows:
        name = str(row.get("task_name") or "")
        reward = row.get("reward")
        require(name, "Gate 2 source report contains a task without a name")
        require(name not in source_rewards, f"Duplicate Gate 2 task: {name}")
        require(
            not isinstance(reward, bool)
            and isinstance(reward, (int, float))
            and float(reward) in {0.0, 1.0},
            f"Gate 2 source report contains invalid reward: {name}",
        )
        source_rewards[name] = int(reward)
    unresolved_logical_tasks = {
        name for name, reward in source_rewards.items() if reward == 0
    }
    require(
        {name.rsplit("/", 1)[-1] for name in unresolved_logical_tasks} == task_filters,
        "Gate 3 task set differs from Gate 2 unresolved tasks",
    )
    composition = source_report.get("composition") or {}
    previous_success_count = composition.get("cumulative_success_count")
    require(
        isinstance(previous_success_count, int),
        "Gate 2 report lacks cumulative success count",
    )
    subset_recovered_count = sum(source_rewards.values())
    require(
        composition.get("subset_recovered_count") == subset_recovered_count,
        "Gate 2 recovered count differs from its task rewards",
    )
    require(
        composition.get("remaining_unresolved_count") == len(unresolved_logical_tasks),
        "Gate 2 unresolved count differs from its task rewards",
    )
    prior_count = composition.get("previous_success_count")
    require(
        isinstance(prior_count, int)
        and prior_count + subset_recovered_count == previous_success_count,
        "Gate 2 cumulative composition is inconsistent",
    )
    require(
        loop_state.get("latest_gate") == PREVIOUS_GATE_INDEX,
        "Loop state is not at Gate 2",
    )
    require(
        loop_state.get("combined_resolved") == previous_success_count,
        "Loop state and Gate 2 success counts differ",
    )
    require(
        loop_state.get("remaining_unresolved") == expected_total,
        "Loop state unresolved count differs from Gate 3 job",
    )

    return RepairContext(
        job_dir=job_dir,
        tts_run_dir=tts_run_dir,
        manifest_path=manifest_path,
        gate_manifest_path=gate_manifest_path,
        loop_state_path=loop_state_path,
        task_file=task_file,
        gate_root=gate_root,
        run_id=run_id,
        job_name=job_dir.name,
        job_config_semantic_sha256=job_config_semantic_sha256(job_config_path),
        manifest_sha256=sha256_file(manifest_path),
        gate_manifest_sha256=sha256_file(gate_manifest_path),
        loop_state_sha256=sha256_file(loop_state_path),
        gate_tree_sha256=gate_tree_sha256,
        previous_success_count=previous_success_count,
        task_filters=task_filters,
        resume_contract=contract,
    )


def log_snapshot(path: Path) -> dict[str, Any]:
    path = path.expanduser().resolve()
    content = path.read_bytes() if path.exists() else b""
    return {
        "path": str(path),
        "size": len(content),
        "prefix_sha256": sha256_bytes(content),
    }


def record_payload(record: TrialRecord, job_dir: Path) -> dict[str, Any]:
    return {
        "task_name": record.task_name,
        "task_filter": record.task_filter,
        "trial_name": record.trial_name,
        "result_path": str(record.result_path.relative_to(job_dir)),
        "result_sha256": record.result_sha256,
        "reward": record.reward,
    }


def prepare_repair(
    *,
    job_dir: Path,
    tts_run_dir: Path,
    allowlist_file: Path,
    repair_dir: Path,
    resume_log: Path,
    expected_error_count: int,
    expected_valid_count: int,
    execute: bool,
) -> dict[str, Any]:
    require(expected_error_count > 0, "Expected error count must be positive")
    require(expected_valid_count > 0, "Expected valid count must be positive")
    expected_total = expected_error_count + expected_valid_count
    allowlist = read_name_file(allowlist_file.expanduser().resolve())
    require(
        len(allowlist) == expected_error_count,
        "Allowlist size differs from expected error count",
    )
    repair_dir = repair_dir.expanduser().resolve()
    job_dir = job_dir.expanduser().resolve()
    require(
        job_dir not in repair_dir.parents and repair_dir != job_dir,
        "Repair archive must be outside the Harbor job directory",
    )
    require(not repair_dir.exists(), f"Repair directory already exists: {repair_dir}")

    from scripts.job_run_lock import exclusive_job_run

    with exclusive_job_run(job_dir):
        context = validate_context(
            job_dir=job_dir,
            tts_run_dir=tts_run_dir,
            expected_total=expected_total,
            require_pending_gate=True,
        )
        dataset_root = (
            Path(str((context.resume_contract.get("dataset") or {}).get("path") or ""))
            .expanduser()
            .resolve()
        )
        for frozen_root, label in (
            (context.gate_root, "Gate 3 skill tree"),
            (dataset_root, "dataset tree"),
        ):
            require(
                repair_dir != frozen_root and frozen_root not in repair_dir.parents,
                f"Repair archive must not be inside the frozen {label}",
            )
        root_result_path = context.job_dir / "result.json"
        root_result = read_json(root_result_path)
        require(
            root_result.get("finished_at") is not None, "Source job is not finished"
        )
        records = scan_trials(context.job_dir)
        require(
            len(records) == expected_total,
            "Job trial count differs from expected total",
        )
        require(
            {record.task_filter for record in records} == context.task_filters,
            "Trial results differ from configured task filters",
        )
        cancelled = [
            record for record in records if record.exception_type == "CancelledError"
        ]
        valid = [
            record for record in records if record.exception_type != "CancelledError"
        ]
        require(
            len(cancelled) == expected_error_count,
            "CancelledError count differs from expected error count",
        )
        require(
            len(valid) == expected_valid_count,
            "Valid result count differs from expectation",
        )
        for record in cancelled:
            require(
                interrupted_cancelled_result(record),
                f"Cancelled task is not a verifier-free interrupted result: {record.task_name}",
            )
        for record in valid:
            require(
                valid_binary_result(record),
                f"Non-allowlisted result is invalid: {record.task_name}",
            )
            incompatibility = trial_resume_incompatibility(
                record,
                expected_contract=context.resume_contract,
            )
            require(
                incompatibility is None,
                "A valid result would be rescheduled by benchmark resume: "
                f"{record.task_name} ({incompatibility})",
            )
        cancelled_tasks = {record.task_name for record in cancelled}
        require(
            cancelled_tasks == set(allowlist),
            "Allowlist does not exactly match CancelledError tasks",
        )

        state = {
            "schema_version": 1,
            "kind": "deepswe_gate3_cancelled_error_only_repair",
            "status": "planned" if not execute else "preparing",
            "created_at": utc_now(),
            "gate_index": GATE_INDEX,
            "previous_gate_index": PREVIOUS_GATE_INDEX,
            "run_id": context.run_id,
            "job_name": context.job_name,
            "job_dir": str(context.job_dir),
            "tts_run_dir": str(context.tts_run_dir),
            "repair_dir": str(repair_dir),
            "allowlist_file": str(allowlist_file.expanduser().resolve()),
            "expected_error_count": expected_error_count,
            "expected_valid_count": expected_valid_count,
            "expected_total": expected_total,
            "expected_error_tasks": sorted(cancelled_tasks),
            "expected_all_tasks": sorted(record.task_name for record in records),
            "valid_results_before": [
                record_payload(record, context.job_dir)
                for record in sorted(valid, key=lambda item: item.task_name)
            ],
            "cancelled_results_before": [
                record_payload(record, context.job_dir)
                for record in sorted(cancelled, key=lambda item: item.task_name)
            ],
            "resume_log": log_snapshot(resume_log),
            "frozen_inputs": {
                "job_config_semantic_sha256": context.job_config_semantic_sha256,
                "manifest_sha256": context.manifest_sha256,
                "gate_manifest_sha256": context.gate_manifest_sha256,
                "loop_state_sha256": context.loop_state_sha256,
                "gate_tree_sha256": context.gate_tree_sha256,
                "previous_success_count": context.previous_success_count,
            },
        }
        if not execute:
            return state

        repair_dir.mkdir(parents=True)
        archive_root = repair_dir / "archive" / "trials"
        archive_root.mkdir(parents=True)
        root_backup_path = repair_dir / "root_result.before.json"
        root_bytes = root_result_path.read_bytes()
        root_backup_path.write_bytes(root_bytes)
        moved: list[tuple[Path, Path]] = []
        try:
            for record in sorted(cancelled, key=lambda item: item.trial_name):
                destination = archive_root / record.trial_name
                require(not destination.exists(), f"Archive collision: {destination}")
                os.replace(record.trial_dir, destination)
                moved.append((record.trial_dir, destination))
            root_result["finished_at"] = None
            atomic_write_json(root_result_path, root_result)
            state["status"] = "prepared"
            state["prepared_at"] = utc_now()
            state["root_result_before_sha256"] = sha256_bytes(root_bytes)
            state["root_result_backup"] = str(root_backup_path)
            state["archive_root"] = str(archive_root)
            for row in state["cancelled_results_before"]:
                row["archive_path"] = str(archive_root / row["trial_name"])
            atomic_write_json(repair_dir / STATE_FILENAME, state)
        except Exception:
            atomic_write(root_result_path, root_bytes)
            for source, destination in reversed(moved):
                if destination.exists() and not source.exists():
                    os.replace(destination, source)
            raise

        remaining = scan_trials(context.job_dir)
        require(
            len(remaining) == expected_valid_count,
            "Unexpected trial count after archive",
        )
        require(
            all(valid_binary_result(record) for record in remaining),
            "Invalid result remained after archive",
        )
        return state


def validate_log_suffix(
    snapshot: dict[str, Any], expected_tasks: set[str]
) -> list[dict[str, str]]:
    path = Path(str(snapshot.get("path") or "")).expanduser().resolve()
    require(path.is_file(), f"Resume log was not created: {path}")
    content = path.read_bytes()
    size = snapshot.get("size")
    require(isinstance(size, int) and size >= 0, "Invalid stored log offset")
    require(len(content) >= size, "Resume log was truncated")
    require(
        sha256_bytes(content[:size]) == snapshot.get("prefix_sha256"),
        "Resume log prefix changed after prepare",
    )
    starts = [
        match.groupdict()
        for match in START_PATTERN.finditer(
            content[size:].decode("utf-8", errors="replace")
        )
    ]
    started_tasks = {row["task"] for row in starts}
    require(starts, "Resume log contains no new START events")
    require(
        started_tasks == expected_tasks,
        "Resume START task set differs from error allowlist",
    )
    return starts


def evaluation_summary(job_dir: Path, records: list[TrialRecord]) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    rewards: list[int] = []
    errors = 0
    for record in sorted(records, key=lambda item: item.task_name):
        require(record.reward is not None, f"Missing binary reward: {record.task_name}")
        rewards.append(record.reward)
        if record.exception_type is not None:
            errors += 1
        rows.append(
            {
                "task_name": record.task_name,
                "trial_name": record.trial_name,
                "reward": float(record.reward),
                "exception_type": record.exception_type,
                "result_path": str(record.result_path),
                "source_job": job_dir.name,
            }
        )
    return {
        "job_dir": str(job_dir),
        "job_name": job_dir.name,
        "n_trials": len(rows),
        "n_errors": errors,
        "resolved": sum(rewards),
        "mean_reward": statistics.fmean(rewards),
        "tasks": rows,
    }


def render_report_md(manifest: dict[str, Any]) -> str:
    summary = manifest.get("summary") or {}
    lines = [
        "# DeepSWE Test-Time Skill Evolution Gates",
        "",
        "## Summary",
        "",
        f"- Run id: `{manifest.get('run_id')}`",
        f"- Source direct run: `{manifest.get('source_run_id')}`",
        f"- Failed traces used for evolution: `{summary.get('task_evidence')}`",
        "- Evolution verifier access: `false`",
        "- Promotion source: `evaluator_only`",
        f"- Repo candidates: `{summary.get('repo_candidates')}`",
        f"- Failure-mode candidates: `{summary.get('failure_candidates')}`",
        f"- Promoted test-time skills: `{summary.get('promoted_skills')}`",
        "",
        "## Gates",
        "",
        "| Gate | Skill Root | Base Skills | Test-Time Skills | Verifier Report |",
        "| ---: | --- | ---: | ---: | --- |",
    ]
    for gate in manifest.get("gates") or []:
        counts = gate.get("skill_counts") or {}
        lines.append(
            "| {idx} | `{root}` | {base} | {tts} | {report} |".format(
                idx=gate.get("gate_index"),
                root=gate.get("skill_root"),
                base=counts.get("base"),
                tts=counts.get("test_time_promoted"),
                report=(gate.get("verifier_report") or {}).get("status"),
            )
        )
    return "\n".join(lines) + "\n"


def write_transaction(payloads: dict[Path, bytes]) -> None:
    originals = {
        path: path.read_bytes() if path.exists() else None for path in payloads
    }
    written: list[Path] = []
    try:
        for path, content in payloads.items():
            atomic_write(path, content)
            written.append(path)
    except Exception:
        for path in reversed(written):
            original = originals[path]
            if original is None:
                path.unlink(missing_ok=True)
            else:
                atomic_write(path, original)
        raise


def finalize_repair(*, repair_dir: Path, execute: bool) -> dict[str, Any]:
    repair_dir = repair_dir.expanduser().resolve()
    state_path = repair_dir / STATE_FILENAME
    state = read_json(state_path)
    require(state.get("status") == "prepared", "Repair is not in prepared state")
    require(state.get("gate_index") == GATE_INDEX, "Repair state is not for Gate 3")
    job_dir = Path(str(state.get("job_dir") or "")).expanduser().resolve()
    tts_run_dir = Path(str(state.get("tts_run_dir") or "")).expanduser().resolve()
    expected_total = int(state.get("expected_total") or 0)
    expected_valid_count = int(state.get("expected_valid_count") or 0)
    expected_error_tasks = set(state.get("expected_error_tasks") or [])
    require(
        len(expected_error_tasks) == state.get("expected_error_count"),
        "Invalid repair allowlist",
    )

    from scripts.job_run_lock import exclusive_job_run

    with exclusive_job_run(job_dir):
        context = validate_context(
            job_dir=job_dir,
            tts_run_dir=tts_run_dir,
            expected_total=expected_total,
            require_pending_gate=True,
        )
        frozen = state.get("frozen_inputs") or {}
        for key, current in (
            (
                "job_config_semantic_sha256",
                context.job_config_semantic_sha256,
            ),
            ("manifest_sha256", context.manifest_sha256),
            ("gate_manifest_sha256", context.gate_manifest_sha256),
            ("loop_state_sha256", context.loop_state_sha256),
            ("gate_tree_sha256", context.gate_tree_sha256),
        ):
            require(frozen.get(key) == current, f"Frozen repair input changed: {key}")

        root_result = read_json(job_dir / "result.json")
        require(
            root_result.get("finished_at") is not None, "Resumed job is not finished"
        )
        require(
            root_result.get("n_total_trials") == expected_total,
            "Resumed job total changed",
        )
        records = scan_trials(job_dir)
        require(len(records) == expected_total, "Resumed job has the wrong trial count")
        require(
            {record.task_name for record in records}
            == set(state.get("expected_all_tasks") or []),
            "Resumed task set changed",
        )
        require(
            all(valid_binary_result(record) for record in records),
            "Resumed job still contains infra-invalid results",
        )

        by_task = {record.task_name: record for record in records}
        for row in state.get("valid_results_before") or []:
            task_name = str(row.get("task_name") or "")
            record = by_task.get(task_name)
            require(
                record is not None, f"Previously valid task disappeared: {task_name}"
            )
            require(
                str(record.result_path.relative_to(job_dir)) == row.get("result_path"),
                f"Previously valid result path changed: {task_name}",
            )
            require(
                record.result_sha256 == row.get("result_sha256"),
                f"Previously valid result changed: {task_name}",
            )
        require(
            set(by_task)
            - {row["task_name"] for row in state.get("valid_results_before") or []}
            == expected_error_tasks,
            "Tasks replaced by resume differ from error allowlist",
        )
        require(
            len(state.get("valid_results_before") or []) == expected_valid_count,
            "Stored valid-result count changed",
        )

        for row in state.get("cancelled_results_before") or []:
            archive_path = Path(str(row.get("archive_path") or ""))
            archived_result = archive_path / "result.json"
            require(
                archived_result.is_file(),
                f"Archived evidence disappeared: {archive_path}",
            )
            require(
                sha256_file(archived_result) == row.get("result_sha256"),
                f"Archived evidence changed: {row.get('task_name')}",
            )
        starts = validate_log_suffix(
            state.get("resume_log") or {}, expected_error_tasks
        )

        summary = evaluation_summary(job_dir, records)
        recovered = {record.task_name for record in records if record.reward == 1}
        previous_success_count = int(frozen.get("previous_success_count") or 0)
        cumulative_success_count = previous_success_count + len(recovered)
        remaining = expected_total - len(recovered)
        created_at = utc_now()
        subset_report_path = tts_run_dir / "subset_eval" / "gate003_subset_report.json"
        require(not subset_report_path.exists(), "Gate 3 subset report already exists")
        subset_report = {
            "schema_version": 1,
            "kind": "tts_subset_eval_report",
            "run_id": context.run_id,
            "gate_index": GATE_INDEX,
            "previous_gate_index": PREVIOUS_GATE_INDEX,
            "created_at": created_at,
            "job_name": context.job_name,
            "job_dir": str(job_dir),
            "task_file": str(context.task_file),
            "expected_trials": expected_total,
            "complete": True,
            "benchmark_name": "deepswe",
            "infra_invalid_trials": [],
            "evaluation": summary,
            "tasks": summary["tasks"],
            "completeness": {
                "expected_trials": expected_total,
                "trial_result_files": len(records),
            },
            "composition": {
                "previous_success_count": previous_success_count,
                "subset_recovered_count": len(recovered),
                "cumulative_success_count": cumulative_success_count,
                "remaining_unresolved_count": remaining,
            },
            "error_only_repair": {
                "repair_state": str(state_path),
                "rerun_task_count": len(expected_error_tasks),
                "resume_start_events": len(starts),
                "preserved_valid_results": expected_valid_count,
            },
        }
        verifier_report = {
            "status": "available_subset",
            "eval_run_id": context.job_name,
            "aggregate_path": str(subset_report_path),
            "complete": True,
            "n_trials": summary["n_trials"],
            "n_errors": summary["n_errors"],
            "resolved": len(recovered),
            "cumulative_resolved": cumulative_success_count,
            "remaining_unresolved": remaining,
            "mean_reward": summary["mean_reward"],
        }

        manifest = read_json(context.manifest_path)
        gate = gate_entry(manifest, GATE_INDEX)
        gate["verifier_report"] = verifier_report
        gate_manifest = read_json(context.gate_manifest_path)
        gate_manifest["verifier_report"] = verifier_report
        loop_state = read_json(context.loop_state_path)
        loop_state.update(
            {
                "updated_at": created_at,
                "run_id": context.run_id,
                "latest_gate": GATE_INDEX,
                "combined_resolved": cumulative_success_count,
                "remaining_unresolved": remaining,
                "latest_subset_report": str(subset_report_path),
                "latest_job_name": context.job_name,
                "latest_subset_recovered": len(recovered),
            }
        )
        validate_no_gate4(tts_run_dir, manifest, context.gate_root)

        result = {
            "status": "validated" if not execute else "finalized",
            "gate_index": GATE_INDEX,
            "job_name": context.job_name,
            "n_trials": summary["n_trials"],
            "gate_resolved": len(recovered),
            "cumulative_resolved": cumulative_success_count,
            "remaining_unresolved": remaining,
            "preserved_valid_results": expected_valid_count,
            "resume_start_tasks": sorted({row["task"] for row in starts}),
            "subset_report": str(subset_report_path),
        }
        if not execute:
            return result

        final_state = dict(state)
        final_state.update(
            {
                "status": "finalized",
                "finalized_at": created_at,
                "final_validation": result,
            }
        )
        payloads = {
            subset_report_path: json_bytes(subset_report),
            context.gate_manifest_path: json_bytes(gate_manifest),
            context.manifest_path: json_bytes(manifest),
            tts_run_dir / "report.md": render_report_md(manifest).encode("utf-8"),
            context.loop_state_path: json_bytes(loop_state),
            state_path: json_bytes(final_state),
        }
        write_transaction(payloads)
        return result


def resume_subset_job(
    *,
    state: dict[str, Any],
    env_file: Path,
    python: str,
    poll_sec: float,
    recovery_rounds: int,
    active_job_wait_timeout_sec: float,
    job_wall_timeout_sec: float,
) -> None:
    from scripts.run_swebench_tts_subset_evo_loop import run_subset_eval

    job_dir = Path(str(state.get("job_dir") or "")).expanduser().resolve()
    tts_run_dir = Path(str(state.get("tts_run_dir") or "")).expanduser().resolve()
    config = read_json(job_dir / "config.json")
    agents = config.get("agents") or []
    require(len(agents) == 1 and isinstance(agents[0], dict), "Expected one agent")
    agent = agents[0]
    agent_kwargs = agent.get("kwargs") or {}
    contract = agent_kwargs.get("resume_contract")
    require(isinstance(contract, dict), "Missing resume contract")
    provider = contract.get("provider") or {}
    agent_parameters = contract.get("agent_parameters") or {}
    dependencies = contract.get("dependency_versions") or {}
    environment = config.get("environment") or {}
    environment_kwargs = environment.get("kwargs") or {}
    harness = str(provider.get("agent") or "")
    require(harness in {"claude-code", "pi"}, "Unsupported saved agent harness")
    endpoint = str(provider.get("endpoint") or "")
    require(endpoint, "Saved provider endpoint is missing")
    args = SimpleNamespace(
        provider=str(provider.get("name") or ""),
        harness=harness,
        env_file=env_file.expanduser().resolve(),
        provider_model=str(provider.get("model") or ""),
        provider_base_url=endpoint if harness == "pi" else None,
        provider_anthropic_base_url=endpoint if harness == "claude-code" else None,
        provider_api=provider.get("provider_api"),
        claude_sdk_version=dependencies.get("claude-agent-sdk"),
        pi_version=dependencies.get("pi-coding-agent") or "0.80.6",
        claude_max_turns=agent_parameters.get("max_turns"),
        claude_max_budget_usd=agent_parameters.get("max_budget_usd"),
        dataset=str((contract.get("dataset") or {}).get("path") or ""),
        benchmark_name="deepswe",
        agent_timeout_sec=float(agent.get("override_timeout_sec") or 0),
        agent_setup_timeout_sec=float(agent.get("override_setup_timeout_sec") or 0),
        e2b_sandbox_timeout_sec=int(environment_kwargs.get("sandbox_timeout_sec") or 0),
        concurrency=int(config.get("n_concurrent_trials") or 0),
        python=python,
        poll_sec=poll_sec,
        recovery_rounds=recovery_rounds,
        active_job_wait_timeout_sec=active_job_wait_timeout_sec,
        job_wall_timeout_sec=job_wall_timeout_sec,
    )
    require(args.provider, "Saved provider name is missing")
    require(args.provider_model, "Saved provider model is missing")
    require(args.dataset, "Saved dataset path is missing")
    require(args.concurrency > 0, "Saved concurrency is invalid")
    require(args.agent_timeout_sec > 0, "Saved agent timeout is invalid")
    require(args.agent_setup_timeout_sec > 0, "Saved setup timeout is invalid")
    require(args.e2b_sandbox_timeout_sec > 0, "Saved sandbox timeout is invalid")
    gate_root = (
        Path(str((contract.get("skills") or {}).get("roots", [""])[0]))
        .expanduser()
        .resolve()
    )
    resume_log = (
        Path(str((state.get("resume_log") or {}).get("path") or ""))
        .expanduser()
        .resolve()
    )
    run_subset_eval(
        args=args,
        gate_index=GATE_INDEX,
        previous_gate_index=PREVIOUS_GATE_INDEX,
        gate_root=gate_root,
        task_file=tts_run_dir / "subsets" / "gate002_unresolved_tasks.txt",
        expected_trials=int(state.get("expected_total") or 0),
        job_name=str(state.get("job_name") or ""),
        log_path=resume_log,
    )


def validate_orchestration_state_inputs(
    state: dict[str, Any],
    *,
    job_dir: Path,
    tts_run_dir: Path,
    allowlist_file: Path,
    repair_dir: Path,
    resume_log: Path,
    expected_error_count: int,
    expected_valid_count: int,
) -> None:
    resume_log_state = state.get("resume_log") or {}
    comparisons = (
        ("job directory", state.get("job_dir"), job_dir),
        ("TTS run directory", state.get("tts_run_dir"), tts_run_dir),
        ("allowlist", state.get("allowlist_file"), allowlist_file),
        ("repair directory", state.get("repair_dir"), repair_dir),
        ("resume log", resume_log_state.get("path"), resume_log),
    )
    for label, saved, requested in comparisons:
        require(isinstance(saved, str) and saved, f"Saved {label} is missing")
        require(
            Path(saved).expanduser().resolve() == requested.expanduser().resolve(),
            f"Requested {label} differs from prepared repair state",
        )
    require(
        state.get("expected_error_count") == expected_error_count,
        "Requested error count differs from prepared repair state",
    )
    require(
        state.get("expected_valid_count") == expected_valid_count,
        "Requested valid count differs from prepared repair state",
    )


def orchestrate_repair(
    *,
    job_dir: Path,
    tts_run_dir: Path,
    allowlist_file: Path,
    repair_dir: Path,
    resume_log: Path,
    expected_error_count: int,
    expected_valid_count: int,
    env_file: Path,
    python: str,
    poll_sec: float,
    recovery_rounds: int,
    active_job_wait_timeout_sec: float,
    job_wall_timeout_sec: float,
    resume_runner: Callable[[dict[str, Any]], None] | None = None,
) -> dict[str, Any]:
    from scripts.job_run_lock import exclusive_job_run
    from scripts.run_swebench_tts_subset_evo_loop import safe_slug

    job_dir = job_dir.expanduser().resolve()
    tts_run_dir = tts_run_dir.expanduser().resolve()
    allowlist_file = allowlist_file.expanduser().resolve()
    repair_dir = repair_dir.expanduser().resolve()
    resume_log = resume_log.expanduser().resolve()
    state_path = repair_dir / STATE_FILENAME
    if state_path.is_file():
        state = read_json(state_path)
        require(state.get("status") == "prepared", "Repair cannot be resumed")
        validate_orchestration_state_inputs(
            state,
            job_dir=job_dir,
            tts_run_dir=tts_run_dir,
            allowlist_file=allowlist_file,
            repair_dir=repair_dir,
            resume_log=resume_log,
            expected_error_count=expected_error_count,
            expected_valid_count=expected_valid_count,
        )
    manifest = read_json(tts_run_dir / "manifest.json")
    run_id = str(manifest.get("run_id") or "")
    require(run_id, "TTS manifest has no run_id")
    if state_path.is_file():
        require(
            state.get("run_id") == run_id,
            "Prepared repair run_id differs from the current TTS manifest",
        )
    evolution_lock_dir = (
        tts_run_dir.parent / ".evolution-locks" / safe_slug(run_id, limit=150)
    )
    with exclusive_job_run(evolution_lock_dir):
        if state_path.is_file():
            state = read_json(state_path)
            require(state.get("status") == "prepared", "Repair cannot be resumed")
            validate_orchestration_state_inputs(
                state,
                job_dir=job_dir,
                tts_run_dir=tts_run_dir,
                allowlist_file=allowlist_file,
                repair_dir=repair_dir,
                resume_log=resume_log,
                expected_error_count=expected_error_count,
                expected_valid_count=expected_valid_count,
            )
        else:
            require(not repair_dir.exists(), f"Invalid partial repair: {repair_dir}")
            state = prepare_repair(
                job_dir=job_dir,
                tts_run_dir=tts_run_dir,
                allowlist_file=allowlist_file,
                repair_dir=repair_dir,
                resume_log=resume_log,
                expected_error_count=expected_error_count,
                expected_valid_count=expected_valid_count,
                execute=True,
            )
        if resume_runner is None:
            resume_subset_job(
                state=state,
                env_file=env_file,
                python=python,
                poll_sec=poll_sec,
                recovery_rounds=recovery_rounds,
                active_job_wait_timeout_sec=active_job_wait_timeout_sec,
                job_wall_timeout_sec=job_wall_timeout_sec,
            )
        else:
            resume_runner(state)
        return finalize_repair(repair_dir=repair_dir, execute=True)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Quarantine only allowlisted interrupted Gate 3 trials, then validate "
            "and finalize the resumed job without materializing Gate 4."
        )
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    prepare = subparsers.add_parser("prepare")
    prepare.add_argument("--job-dir", type=Path, required=True)
    prepare.add_argument("--tts-run-dir", type=Path, required=True)
    prepare.add_argument("--allowlist-file", type=Path, required=True)
    prepare.add_argument("--repair-dir", type=Path, required=True)
    prepare.add_argument("--resume-log", type=Path, required=True)
    prepare.add_argument("--expected-error-count", type=int, required=True)
    prepare.add_argument("--expected-valid-count", type=int, required=True)
    prepare.add_argument("--execute", action="store_true")
    finalize = subparsers.add_parser("finalize")
    finalize.add_argument("--repair-dir", type=Path, required=True)
    finalize.add_argument("--execute", action="store_true")
    run = subparsers.add_parser(
        "run",
        help="Prepare, resume the saved job, and finalize under one evolution lock.",
    )
    run.add_argument("--job-dir", type=Path, required=True)
    run.add_argument("--tts-run-dir", type=Path, required=True)
    run.add_argument("--allowlist-file", type=Path, required=True)
    run.add_argument("--repair-dir", type=Path, required=True)
    run.add_argument("--resume-log", type=Path, required=True)
    run.add_argument("--expected-error-count", type=int, required=True)
    run.add_argument("--expected-valid-count", type=int, required=True)
    run.add_argument("--env-file", type=Path, required=True)
    run.add_argument("--python", default=sys.executable)
    run.add_argument("--poll-sec", type=float, default=60)
    run.add_argument("--recovery-rounds", type=int, default=4)
    run.add_argument("--active-job-wait-timeout-sec", type=float, default=259200)
    run.add_argument("--job-wall-timeout-sec", type=float, default=259200)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    try:
        if args.command == "prepare":
            result = prepare_repair(
                job_dir=args.job_dir,
                tts_run_dir=args.tts_run_dir,
                allowlist_file=args.allowlist_file,
                repair_dir=args.repair_dir,
                resume_log=args.resume_log,
                expected_error_count=args.expected_error_count,
                expected_valid_count=args.expected_valid_count,
                execute=args.execute,
            )
        elif args.command == "finalize":
            result = finalize_repair(repair_dir=args.repair_dir, execute=args.execute)
        else:
            result = orchestrate_repair(
                job_dir=args.job_dir,
                tts_run_dir=args.tts_run_dir,
                allowlist_file=args.allowlist_file,
                repair_dir=args.repair_dir,
                resume_log=args.resume_log,
                expected_error_count=args.expected_error_count,
                expected_valid_count=args.expected_valid_count,
                env_file=args.env_file,
                python=args.python,
                poll_sec=args.poll_sec,
                recovery_rounds=args.recovery_rounds,
                active_job_wait_timeout_sec=args.active_job_wait_timeout_sec,
                job_wall_timeout_sec=args.job_wall_timeout_sec,
            )
    except RepairError as exc:
        raise SystemExit(str(exc)) from exc
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
