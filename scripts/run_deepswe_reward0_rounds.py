#!/usr/bin/env python
"""Run sequential, same-setting DeepSWE samples on strict reward-zero cohorts."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in os.sys.path:
    os.sys.path.insert(0, str(ROOT))

from scripts.job_run_lock import exclusive_job_run, job_is_running  # noqa: E402
from scripts.materialize_deepswe_tts_evolution_gates import (  # noqa: E402
    sha256_file,
    sha256_tree,
)
from scripts.run_deepswe_setting_bon import (  # noqa: E402
    Setting,
    aggregate_sample_2,
    discover_settings,
    normalized_job_config,
    read_json,
    report_rows,
    source_job_dir,
    source_resume_contract,
    task_name,
    utc_now,
    validate_attempt_contract,
    validate_output_root,
    validate_requested_execution,
    validate_skill_contract,
    validate_source_report,
    wait_for_temp_space,
    write_json_atomic,
)
from scripts.run_swebench_tts_subset_evo_loop import (  # noqa: E402
    dataset_filter_task_names,
    run_subset_eval,
    safe_slug,
    subset_execution_payload,
)


PROTECTED_CODE_PATHS = frozenset(
    {
        "agents/claude_sdk_agent.py",
        "agents/pi_agent.py",
        "agents/skill_harness_memory.py",
        "environments/e2b_swebench.py",
        "providers/__init__.py",
        "providers/specs.py",
        "scripts/run_benchmark.py",
    }
)
ORCHESTRATION_CODE_PATHS = frozenset(
    {
        "scripts/job_run_lock.py",
        "scripts/materialize_deepswe_tts_evolution_gates.py",
        "scripts/run_deepswe_reward0_rounds.py",
        "scripts/run_deepswe_setting_bon.py",
        "scripts/run_swebench_tts_subset_evo_loop.py",
    }
)


@dataclass
class Campaign:
    setting: Setting
    source_report_path: Path
    report_paths: list[Path]
    report_sha256: list[str]
    initial_samples: int
    skill_tree_sha256: str
    execution_audit: dict[str, Any]
    new_rounds: list[dict[str, Any]] = field(default_factory=list)


def canonical_json_sha256(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def names_sha256(names: set[str]) -> str:
    return hashlib.sha256(("\n".join(sorted(names)) + "\n").encode()).hexdigest()


def sampling_job_name(
    parent_job_name: str, setting_name: str, sample_index: int, cohort_id: str
) -> str:
    suffix = f"_{setting_name}_s{sample_index}_r0_{cohort_id[:12]}"
    return safe_slug(parent_job_name, limit=150 - len(suffix)) + suffix


def orchestration_code_sha256() -> dict[str, str]:
    return {path: sha256_file(ROOT / path) for path in sorted(ORCHESTRATION_CODE_PATHS)}


def protected_code_sha256(
    args: argparse.Namespace, skill_root: Path
) -> dict[str, str]:
    code = subset_execution_payload(args, skill_root).get("code_sha256") or {}
    if any(path not in code for path in PROTECTED_CODE_PATHS):
        raise ValueError("Prospective execution is missing protected code hashes")
    return {path: code[path] for path in sorted(PROTECTED_CODE_PATHS)}


def strict_binary_rows(
    report: dict[str, Any], *, label: str
) -> dict[str, dict[str, Any]]:
    by_name: dict[str, dict[str, Any]] = {}
    for row in report_rows(report):
        name = task_name(row)
        if name in by_name:
            raise ValueError(f"{label} contains duplicate task {name}")
        reward = row.get("reward")
        if isinstance(reward, bool) or not isinstance(reward, (int, float)):
            raise ValueError(
                f"{label} requires numeric binary rewards: task={name} "
                f"reward={reward!r}"
            )
        if float(reward) not in {0.0, 1.0}:
            raise ValueError(
                f"{label} requires numeric binary rewards: task={name} "
                f"reward={reward!r}"
            )
        by_name[name] = row
    return by_name


def strict_reward_zero_names(report: dict[str, Any], *, label: str) -> set[str]:
    return {
        name
        for name, row in strict_binary_rows(report, label=label).items()
        if float(row["reward"]) == 0.0
    }


def validate_raw_report(
    report: dict[str, Any], path: Path, *, label: str
) -> dict[str, dict[str, Any]]:
    validate_source_report(report, path)
    evaluation = report.get("evaluation") or {}
    if evaluation.get("n_errors") != 0:
        raise ValueError(f"{label} has evaluation errors: {path}")
    rows = strict_binary_rows(report, label=label)
    exceptions = [
        name for name, row in rows.items() if row.get("exception_type") is not None
    ]
    if exceptions:
        raise ValueError(
            f"{label} contains task exceptions and is not a valid sample: "
            f"{exceptions[:10]}"
        )
    job_dir = source_job_dir(report)
    config_path = job_dir / "config.json"
    provenance = report.get("provenance") or {}
    recorded_config_hash = provenance.get("job_config_sha256")
    if recorded_config_hash is not None and recorded_config_hash != sha256_file(
        config_path
    ):
        raise ValueError(f"{label} job config changed after aggregation")
    for name, row in rows.items():
        result_value = row.get("result_path")
        if not isinstance(result_value, str) or not result_value:
            raise ValueError(f"{label} task {name} has no result_path")
        result_path = Path(result_value).expanduser().resolve()
        if result_path.parent.parent != job_dir or not result_path.is_file():
            raise ValueError(f"{label} task {name} result is outside/missing from job")
        raw = read_json(result_path)
        raw_reward = ((raw.get("verifier_result") or {}).get("rewards") or {}).get(
            "reward"
        )
        if (
            raw.get("task_name") != name
            or raw.get("trial_name") != row.get("trial_name")
            or isinstance(raw_reward, bool)
            or not isinstance(raw_reward, (int, float))
            or float(raw_reward) != float(row["reward"])
            or raw.get("exception_info") is not None
        ):
            raise ValueError(f"{label} task {name} differs from its physical result")
    return rows


def validate_transition(
    parent: dict[str, Any],
    child: dict[str, Any],
    *,
    parent_label: str,
    child_label: str,
) -> set[str]:
    expected = strict_reward_zero_names(parent, label=parent_label)
    observed = set(strict_binary_rows(child, label=child_label))
    if observed != expected:
        missing = sorted(expected - observed)
        extra = sorted(observed - expected)
        raise ValueError(
            f"{child_label} task set is not exactly {parent_label} reward=0: "
            f"missing={missing[:10]} extra={extra[:10]}"
        )
    return expected


def normalized_config_allowing_code(
    config: dict[str, Any], allowed_code_changes: frozenset[str]
) -> dict[str, Any]:
    normalized = normalized_job_config(config)
    for agent in normalized.get("agents") or []:
        contract = (agent.get("kwargs") or {}).get("resume_contract") or {}
        code = contract.get("code_sha256") or {}
        for path in allowed_code_changes:
            if path not in code:
                raise ValueError(f"Job config has no protected code hash for {path}")
            code[path] = "<explicitly-allowed-code-change>"
    return normalized


def validate_child_contract(
    *,
    args: argparse.Namespace,
    parent_report: dict[str, Any],
    child_report: dict[str, Any],
    skill_root: Path,
    pinned_skill_hash: str,
    expected_logical_tasks: set[str],
    expected_filter_tasks: set[str],
    allowed_code_changes: frozenset[str],
    pinned_protected_code: dict[str, str],
) -> None:
    if validate_skill_contract(child_report, skill_root) != pinned_skill_hash:
        raise ValueError("Child report did not use the pinned setting skill tree")

    child_contract = source_resume_contract(child_report)
    requested = subset_execution_payload(args, skill_root)
    expected_contract_fields = {
        "provider": requested["provider"],
        "agent_parameters": requested["agent_parameters"],
        "runtime_knobs": requested["runtime_knobs"],
    }
    for key, expected in expected_contract_fields.items():
        if child_contract.get(key) != expected:
            raise ValueError(f"Child execution contract differs at {key}")
    child_code = child_contract.get("code_sha256") or {}
    if set(child_code) != PROTECTED_CODE_PATHS or any(
        child_code.get(path) != pinned_protected_code.get(path)
        for path in PROTECTED_CODE_PATHS
    ):
        raise ValueError("Child execution contract has different protected code")
    child_dataset = child_contract.get("dataset") or {}
    if child_dataset.get("path") != requested["dataset"]:
        raise ValueError("Child execution contract differs at dataset path")
    if child_dataset.get("tree_sha256") != requested["dataset_tree_sha256"]:
        raise ValueError("Child execution contract differs at dataset tree")
    if set(child_dataset.get("task_names") or []) != expected_filter_tasks:
        raise ValueError("Child execution contract contains a different task cohort")

    dependency = (
        "claude-agent-sdk" if args.harness == "claude-code" else "pi-coding-agent"
    )
    if (child_contract.get("dependency_versions") or {}).get(dependency) != (
        requested.get("dependency_versions") or {}
    ).get(dependency):
        raise ValueError(f"Child execution contract differs at {dependency}")

    child_job = source_job_dir(child_report)
    child_config = read_json(child_job / "config.json")
    if child_config.get("n_attempts") != 1:
        raise ValueError("Each sampling cohort must use n_attempts=1")
    if child_config.get("n_concurrent_trials") != args.concurrency:
        raise ValueError("Child job concurrency differs from the requested setting")
    datasets = child_config.get("datasets") or []
    if len(datasets) != 1 or set((datasets[0] or {}).get("task_names") or []) != (
        expected_filter_tasks
    ):
        raise ValueError("Child job config contains a different task cohort")
    agents = child_config.get("agents") or []
    if len(agents) != 1:
        raise ValueError("Each sampling cohort must use exactly one agent")

    parent_config = read_json(source_job_dir(parent_report) / "config.json")
    if normalized_config_allowing_code(
        parent_config, allowed_code_changes
    ) != normalized_config_allowing_code(child_config, allowed_code_changes):
        raise ValueError(
            "Child job config differs from its predecessor beyond job/task identity "
            "and the explicitly allowed code hash"
        )

    child_names = set(strict_binary_rows(child_report, label="child report"))
    if child_names != expected_logical_tasks:
        raise ValueError("Child report contains a different logical task cohort")


def write_text_once(path: Path, value: str) -> None:
    if path.exists():
        if path.read_text(encoding="utf-8") != value:
            raise ValueError(f"Refusing to overwrite changed immutable file: {path}")
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(value, encoding="utf-8")
    os.replace(temporary, path)


def write_json_once(path: Path, value: dict[str, Any]) -> None:
    if path.exists():
        if read_json(path) != value:
            raise ValueError(f"Refusing to overwrite changed immutable file: {path}")
        return
    write_json_atomic(path, value)


def check_snapshot(path: Path, expected_sha256: str, *, label: str) -> None:
    if not path.is_file() or sha256_file(path) != expected_sha256:
        raise ValueError(f"Immutable {label} changed during sampling: {path}")


def validate_campaign_snapshot(
    *,
    args: argparse.Namespace,
    campaign: Campaign,
    snapshots: dict[str, str],
    stage: str,
) -> None:
    for key in ("launcher_state", "tts_manifest"):
        check_snapshot(
            Path(snapshots[f"{key}_path"]),
            snapshots[f"{key}_sha256"],
            label=f"{stage} {key}",
        )
    lock_dirs = [
        args.launcher_dir,
        Path(snapshots["tts_lock_dir"]),
        Path(snapshots["existing_bon_lock_dir"]),
    ]
    if any(job_is_running(path) for path in lock_dirs):
        raise ValueError(f"A source coordinator became active during {stage}")
    for path, expected_hash in zip(
        campaign.report_paths, campaign.report_sha256, strict=True
    ):
        check_snapshot(path, expected_hash, label=f"{stage} campaign report")
        report = read_json(path)
        validate_raw_report(report, path, label=f"{stage} campaign report")
        if job_is_running(source_job_dir(report)):
            raise ValueError(f"A raw source job became active during {stage}")
    if sha256_tree(campaign.setting.skill_root) != campaign.skill_tree_sha256:
        raise ValueError(f"{campaign.setting.name} skills changed during {stage}")
    if protected_code_sha256(args, campaign.setting.skill_root) != (
        campaign.execution_audit["pinned_protected_code_sha256"]
    ):
        raise ValueError(f"Protected benchmark code changed during {stage}")
    if orchestration_code_sha256() != campaign.execution_audit[
        "pinned_orchestration_code_sha256"
    ]:
        raise ValueError(f"Sampling orchestration code changed during {stage}")


def initial_report_paths(
    setting: Setting, existing_bon_dir: Path
) -> list[Path]:
    paths = [setting.source_report_path]
    if setting.name in {"frozen", "gate_001"}:
        existing = (
            existing_bon_dir
            / setting.name
            / "sample_2_aggregate"
            / "score_report.json"
        ).resolve()
        if not existing.is_file():
            raise ValueError(f"Missing existing same-setting sample 2: {existing}")
        paths.append(existing)
    return paths


def apply_source_runtime_knobs(args: argparse.Namespace) -> dict[str, str | None]:
    state = read_json(args.launcher_dir / "state.json")
    frozen_value = state.get("frozen_report")
    if not isinstance(frozen_value, str) or not frozen_value:
        raise ValueError("Source launcher state has no frozen report")
    frozen_path = Path(frozen_value).expanduser().resolve()
    runtime_knobs = source_resume_contract(read_json(frozen_path)).get(
        "runtime_knobs"
    )
    if not isinstance(runtime_knobs, dict):
        raise ValueError("Frozen source contract has no runtime knobs")
    pinned: dict[str, str | None] = {}
    for name, value in runtime_knobs.items():
        if not isinstance(name, str) or not name:
            raise ValueError("Frozen source contract has an invalid runtime knob name")
        if value is None:
            os.environ.pop(name, None)
            pinned[name] = None
        elif isinstance(value, str):
            os.environ[name] = value
            pinned[name] = value
        else:
            raise ValueError(f"Frozen source runtime knob {name} is not a string/null")
    return pinned


def build_campaigns(
    args: argparse.Namespace,
) -> tuple[list[Campaign], dict[str, str]]:
    state_path = args.launcher_dir / "state.json"
    manifest_path = args.tts_run_dir / "manifest.json"
    state = read_json(state_path)
    status = state.get("status")
    if status == "failed" and not args.allow_failed_source_snapshot:
        raise ValueError(
            "Source launcher is failed; pass --allow-failed-source-snapshot only "
            "after validating its immutable completed reports"
        )
    if status not in {"complete", "failed"}:
        raise ValueError(f"Source launcher is not a stable snapshot: status={status!r}")
    if job_is_running(args.launcher_dir):
        raise ValueError("Source launcher still has an active run lock")
    tts_lock_dir = (
        args.tts_run_dir.parent
        / ".evolution-locks"
        / safe_slug(args.tts_run_dir.name, limit=150)
    )
    if job_is_running(tts_lock_dir):
        raise ValueError("Source TTS evolution still has an active run lock")
    if job_is_running(args.existing_bon_dir):
        raise ValueError("Existing same-setting BoN source still has an active run lock")

    settings = discover_settings(
        launcher_dir=args.launcher_dir,
        tts_run_dir=args.tts_run_dir,
        max_gate=args.max_gate,
    )
    expected_names = ["frozen"] + [
        f"gate_{gate:03d}" for gate in range(1, args.max_gate + 1)
    ]
    if [setting.name for setting in settings] != expected_names:
        raise ValueError(
            "Source snapshot does not contain the requested settings: "
            f"expected={expected_names} observed={[s.name for s in settings]}"
        )
    validate_output_root(
        output_root=args.output_dir,
        launcher_dir=args.launcher_dir,
        tts_run_dir=args.tts_run_dir,
        settings=settings,
    )

    allowed = frozenset(args.allow_code_change)
    if allowed != frozenset({"scripts/run_benchmark.py"}):
        raise ValueError(
            "This campaign requires the exact audited allowlist "
            "{'scripts/run_benchmark.py'}"
        )
    campaigns: list[Campaign] = []
    for setting in settings:
        paths = initial_report_paths(setting, args.existing_bon_dir)
        reports: list[dict[str, Any]] = []
        for index, path in enumerate(paths, 1):
            report = read_json(path)
            validate_raw_report(
                report, path, label=f"{setting.name} sample {index}"
            )
            if job_is_running(source_job_dir(report)):
                raise ValueError(f"Raw source job is still active: {path}")
            reports.append(report)
        if len(reports) == 2:
            validate_transition(
                reports[0],
                reports[1],
                parent_label=f"{setting.name} sample 1",
                child_label=f"{setting.name} sample 2",
            )
            validate_attempt_contract(reports[0], reports[1])

        skill_hash = validate_skill_contract(reports[0], setting.skill_root)
        for report in reports[1:]:
            if validate_skill_contract(report, setting.skill_root) != skill_hash:
                raise ValueError(f"{setting.name} changed skills within its lineage")
        audit = validate_requested_execution(
            args,
            reports[-1],
            setting.skill_root,
            allowed_code_changes=allowed,
        )
        audit["pinned_protected_code_sha256"] = protected_code_sha256(
            args, setting.skill_root
        )
        audit["pinned_orchestration_code_sha256"] = orchestration_code_sha256()
        campaigns.append(
            Campaign(
                setting=setting,
                source_report_path=paths[0],
                report_paths=paths,
                report_sha256=[sha256_file(path) for path in paths],
                initial_samples=len(paths),
                skill_tree_sha256=skill_hash,
                execution_audit=audit,
            )
        )
    return campaigns, {
        "launcher_state_path": str(state_path),
        "launcher_state_sha256": sha256_file(state_path),
        "tts_manifest_path": str(manifest_path),
        "tts_manifest_sha256": sha256_file(manifest_path),
        "tts_lock_dir": str(tts_lock_dir),
        "existing_bon_lock_dir": str(args.existing_bon_dir),
    }


def campaign_plan(
    campaigns: list[Campaign],
    snapshots: dict[str, str],
    args: argparse.Namespace,
) -> dict[str, Any]:
    setting_rows: list[dict[str, Any]] = []
    for campaign in campaigns:
        predecessor = read_json(campaign.report_paths[-1])
        logical_tasks = strict_reward_zero_names(
            predecessor, label=f"{campaign.setting.name} predecessor"
        )
        filter_tasks = dataset_filter_task_names(str(args.dataset), logical_tasks)
        setting_rows.append(
            {
                "setting": campaign.setting.name,
                "gate_index": campaign.setting.gate_index,
                "skill_root": str(campaign.setting.skill_root),
                "skill_tree_sha256": campaign.skill_tree_sha256,
                "existing_samples": len(campaign.report_paths),
                "current_predecessor_report": str(campaign.report_paths[-1]),
                "current_predecessor_report_sha256": sha256_file(
                    campaign.report_paths[-1]
                ),
                "first_new_sample_index": len(campaign.report_paths) + 1,
                "first_new_round_reward0_tasks": len(logical_tasks),
                "first_new_round_logical_task_set_sha256": names_sha256(
                    logical_tasks
                ),
                "first_new_round_filter_task_set_sha256": names_sha256(filter_tasks),
                "source_reports": [
                    {"path": str(path), "sha256": sha256_file(path)}
                    for path in campaign.report_paths
                ],
                "execution_audit": campaign.execution_audit,
            }
        )
    return {
        "schema_version": 1,
        "kind": "deepswe_sequential_reward0_sampling_plan",
        "round_order": "round-major",
        "additional_rounds": args.additional_rounds,
        "max_gate_index": args.max_gate,
        "global_max_concurrency": args.concurrency,
        "allowed_code_changes": sorted(args.allow_code_change),
        "snapshots": snapshots,
        "settings": setting_rows,
    }


def validate_round_manifest(
    manifest: dict[str, Any],
    *,
    expected_plan: dict[str, Any],
) -> Path | None:
    if manifest.get("plan") != expected_plan:
        raise ValueError("Completed round manifest differs from the requested cohort")
    if manifest.get("status") == "no_op_empty_reward0":
        return None
    if manifest.get("status") != "complete":
        raise ValueError("Existing round manifest is not complete")
    report_info = manifest.get("report") or {}
    path = Path(str(report_info.get("path") or "")).expanduser().resolve()
    expected_hash = str(report_info.get("sha256") or "")
    check_snapshot(path, expected_hash, label="completed round report")
    return path


def validate_completed_round_artifacts(
    manifest: dict[str, Any], report: dict[str, Any]
) -> None:
    job_info = manifest.get("job") or {}
    job_dir = Path(str(job_info.get("dir") or "")).expanduser().resolve()
    if source_job_dir(report) != job_dir:
        raise ValueError("Completed round report points at a different job directory")
    check_snapshot(
        job_dir / "config.json",
        str(job_info.get("config_sha256") or ""),
        label="completed round job config",
    )
    check_snapshot(
        job_dir / "result.json",
        str(job_info.get("root_result_sha256") or ""),
        label="completed round root result",
    )


def run_round(
    *,
    args: argparse.Namespace,
    campaign: Campaign,
    additional_round: int,
    snapshots: dict[str, str],
) -> dict[str, Any]:
    setting = campaign.setting
    predecessor_path = campaign.report_paths[-1]
    predecessor = read_json(predecessor_path)
    validate_raw_report(
        predecessor,
        predecessor_path,
        label=f"{setting.name} predecessor",
    )
    sample_index = campaign.initial_samples + additional_round
    logical_tasks = strict_reward_zero_names(
        predecessor, label=f"{setting.name} sample {sample_index - 1}"
    )
    filter_tasks = dataset_filter_task_names(str(args.dataset), logical_tasks)
    predecessor_run_id = str(
        predecessor.get("run_id")
        or (predecessor.get("evaluation") or {}).get("job_name")
        or source_job_dir(predecessor).name
    )
    cohort_id = canonical_json_sha256(
        {
            "setting": setting.name,
            "gate_index": setting.gate_index,
            "sample_index": sample_index,
            "original_source_report_sha256": campaign.report_sha256[0],
            "predecessor_report_sha256": sha256_file(predecessor_path),
            "predecessor_run_id": predecessor_run_id,
            "logical_task_set_sha256": names_sha256(logical_tasks),
            "filter_task_set_sha256": names_sha256(filter_tasks),
            "skill_tree_sha256": campaign.skill_tree_sha256,
            "execution_contract_sha256": canonical_json_sha256(
                campaign.execution_audit
            ),
        }
    )
    round_dir = args.output_dir / setting.name / f"sample_{sample_index}"
    task_file = round_dir / "reward0_tasks.txt"
    task_text = "\n".join(sorted(filter_tasks)) + ("\n" if filter_tasks else "")
    job_name: str | None = None
    if logical_tasks:
        job_name = sampling_job_name(
            source_job_dir(predecessor).name,
            setting.name,
            sample_index,
            cohort_id,
        )
    round_plan = {
        "setting": setting.name,
        "gate_index": setting.gate_index,
        "additional_round": additional_round,
        "sample_index": sample_index,
        "cohort_id": cohort_id,
        "original_source_report_sha256": campaign.report_sha256[0],
        "predecessor_report": str(predecessor_path),
        "predecessor_report_sha256": sha256_file(predecessor_path),
        "predecessor_run_id": predecessor_run_id,
        "logical_tasks": len(logical_tasks),
        "logical_task_set_sha256": names_sha256(logical_tasks),
        "filter_tasks": len(filter_tasks),
        "filter_task_set_sha256": names_sha256(filter_tasks),
        "task_file": str(task_file),
        "job_name": job_name,
        "skill_root": str(setting.skill_root),
        "skill_tree_sha256": campaign.skill_tree_sha256,
        "execution_contract_sha256": canonical_json_sha256(
            campaign.execution_audit
        ),
        "pinned_protected_code_sha256": campaign.execution_audit[
            "pinned_protected_code_sha256"
        ],
        "pinned_orchestration_code_sha256": campaign.execution_audit[
            "pinned_orchestration_code_sha256"
        ],
        "sampling_rule": "exact numeric reward=0 rows from predecessor raw report",
    }
    validate_campaign_snapshot(
        args=args,
        campaign=campaign,
        snapshots=snapshots,
        stage=f"before {setting.name} sample {sample_index}",
    )
    validate_requested_execution(
        args,
        read_json(campaign.source_report_path),
        setting.skill_root,
        allowed_code_changes=frozenset(args.allow_code_change),
    )
    write_text_once(task_file, task_text)
    write_json_once(round_dir / "plan.json", round_plan)

    manifest_path = round_dir / "manifest.json"
    if manifest_path.is_file():
        report_path = validate_round_manifest(
            read_json(manifest_path), expected_plan=round_plan
        )
        if report_path is not None:
            report = read_json(report_path)
            validate_raw_report(
                report,
                report_path,
                label=f"{setting.name} completed sample {sample_index}",
            )
            validate_completed_round_artifacts(read_json(manifest_path), report)
            validate_transition(
                predecessor,
                report,
                parent_label=f"{setting.name} sample {sample_index - 1}",
                child_label=f"{setting.name} sample {sample_index}",
            )
            validate_child_contract(
                args=args,
                parent_report=predecessor,
                child_report=report,
                skill_root=setting.skill_root,
                pinned_skill_hash=campaign.skill_tree_sha256,
                expected_logical_tasks=logical_tasks,
                expected_filter_tasks=filter_tasks,
                allowed_code_changes=frozenset(args.allow_code_change),
                pinned_protected_code=campaign.execution_audit[
                    "pinned_protected_code_sha256"
                ],
            )
            campaign.report_paths.append(report_path)
            campaign.report_sha256.append(sha256_file(report_path))
        return read_json(manifest_path)

    if not logical_tasks:
        manifest = {
            "schema_version": 1,
            "kind": "deepswe_sequential_reward0_round",
            "status": "no_op_empty_reward0",
            "plan": round_plan,
            "report": None,
        }
        write_json_once(manifest_path, manifest)
        return manifest

    if job_name is None:
        raise AssertionError("Non-empty reward-zero cohort has no job name")
    job_dir = run_subset_eval(
        args=args,
        gate_index=setting.gate_index,
        previous_gate_index=setting.gate_index,
        gate_root=setting.skill_root,
        task_file=task_file,
        expected_trials=len(logical_tasks),
        job_name=job_name,
        log_path=round_dir / "benchmark.log",
    )
    validate_campaign_snapshot(
        args=args,
        campaign=campaign,
        snapshots=snapshots,
        stage=f"after {setting.name} sample {sample_index}",
    )
    aggregate_dir = round_dir / "aggregate"
    report_path = aggregate_dir / "score_report.json"
    if report_path.is_file():
        report = read_json(report_path)
        validate_raw_report(report, report_path, label="existing round aggregate")
    else:
        report, report_path = aggregate_sample_2(
            args=args,
            job_dir=job_dir,
            output_dir=aggregate_dir,
            expected_trials=len(logical_tasks),
        )
    if source_job_dir(report) != job_dir.resolve():
        raise ValueError("Round aggregate points at a different benchmark job")
    validate_raw_report(report, report_path, label="completed round aggregate")
    validate_transition(
        predecessor,
        report,
        parent_label=f"{setting.name} sample {sample_index - 1}",
        child_label=f"{setting.name} sample {sample_index}",
    )
    validate_child_contract(
        args=args,
        parent_report=predecessor,
        child_report=report,
        skill_root=setting.skill_root,
        pinned_skill_hash=campaign.skill_tree_sha256,
        expected_logical_tasks=logical_tasks,
        expected_filter_tasks=filter_tasks,
        allowed_code_changes=frozenset(args.allow_code_change),
        pinned_protected_code=campaign.execution_audit[
            "pinned_protected_code_sha256"
        ],
    )
    manifest = {
        "schema_version": 1,
        "kind": "deepswe_sequential_reward0_round",
        "status": "complete",
        "plan": round_plan,
        "job": {
            "name": job_name,
            "dir": str(job_dir),
            "config_sha256": sha256_file(job_dir / "config.json"),
            "root_result_sha256": sha256_file(job_dir / "result.json"),
        },
        "report": {"path": str(report_path), "sha256": sha256_file(report_path)},
        "reward_1": sum(
            float(row["reward"]) == 1.0
            for row in strict_binary_rows(report, label="completed round").values()
        ),
        "reward_0": len(strict_reward_zero_names(report, label="completed round")),
    }
    write_json_once(manifest_path, manifest)
    campaign.report_paths.append(report_path)
    campaign.report_sha256.append(sha256_file(report_path))
    return manifest


def build_best_report(campaign: Campaign) -> dict[str, Any]:
    for path, expected_hash in zip(
        campaign.report_paths, campaign.report_sha256, strict=True
    ):
        check_snapshot(path, expected_hash, label="campaign report")
    reports = [read_json(path) for path in campaign.report_paths]
    for index, report in enumerate(reports, 1):
        validate_raw_report(
            report,
            campaign.report_paths[index - 1],
            label=f"{campaign.setting.name} sample {index}",
        )
        if index > 1:
            validate_transition(
                reports[index - 2],
                report,
                parent_label=f"{campaign.setting.name} sample {index - 1}",
                child_label=f"{campaign.setting.name} sample {index}",
            )

    first = strict_binary_rows(reports[0], label="sample 1")
    rows: list[dict[str, Any]] = []
    for name in sorted(first):
        attempts: list[dict[str, Any]] = []
        for sample_index, report in enumerate(reports, 1):
            row = strict_binary_rows(report, label=f"sample {sample_index}").get(name)
            if row is None:
                continue
            attempts.append(
                {
                    "sample_index": sample_index,
                    "trial_name": row.get("trial_name"),
                    "reward": float(row["reward"]),
                    "exception_type": row.get("exception_type"),
                    "result_path": row.get("result_path"),
                }
            )
        selected = max(attempts, key=lambda item: (item["reward"], -item["sample_index"]))
        rows.append(
            {
                "task_name": name,
                "reward": selected["reward"],
                "trial_name": selected["trial_name"],
                "exception_type": selected["exception_type"],
                "result_path": selected["result_path"],
                "selected_sample": selected["sample_index"],
                "sample_count": len(attempts),
                "attempts": attempts,
            }
        )
    passed = sum(row["reward"] == 1.0 for row in rows)
    sources = [
        {
            "sample_index": index,
            "run_id": report.get("run_id"),
            "report_path": str(path),
            "report_sha256": sha256_file(path),
            "job_dir": str(source_job_dir(report)),
            "job_config_sha256": sha256_file(
                source_job_dir(report) / "config.json"
            ),
            "n_tasks": len(strict_binary_rows(report, label=f"sample {index}")),
        }
        for index, (path, report) in enumerate(
            zip(campaign.report_paths, reports, strict=True), 1
        )
    ]
    lineage = {
        "aggregation": "taskwise_max_binary_reward",
        "tie_breaker": "prefer_earliest_sample",
        "sources": sources,
    }
    lineage_sha256 = canonical_json_sha256(lineage)
    run_id = safe_slug(
        f"{campaign.setting.name}_same_setting_reward0_bon_{lineage_sha256[:12]}"
    )
    created_at = str(
        reports[-1].get("created_at")
        or (reports[-1].get("completeness") or {}).get("aggregated_at")
        or (reports[-1].get("evaluation") or {}).get("finished_at")
        or "derived-from-immutable-lineage"
    )
    report = {
        "schema_version": 1,
        "kind": "deepswe_same_setting_best_of_up_to_n",
        "run_id": run_id,
        "benchmark_name": "deepswe",
        "created_at": created_at,
        "complete": True,
        "infra_invalid_trials": [],
        "setting": campaign.setting.name,
        "gate_index": campaign.setting.gate_index,
        "raw_sampling_predecessor": False,
        "sampling_policy": {
            "candidate_rule": "each sample reruns only predecessor reward=0",
            "selection": "maximum binary reward; earliest sample wins ties",
            "cross_setting_skill_transfer": False,
            "infra_recovery": (
                "resume/retry within one cohort; never increments sample_count"
            ),
        },
        "skills": {
            "root": str(campaign.setting.skill_root),
            "tree_sha256": campaign.skill_tree_sha256,
        },
        "lineage": lineage,
        "provenance": {
            "benchmark_name": "deepswe",
            "task_set_sha256": names_sha256(set(first)),
            "lineage_sha256": lineage_sha256,
            "resume_contract": source_resume_contract(reports[0]),
            "execution_audit": campaign.execution_audit,
        },
        "evaluation": {
            "n_tasks": len(rows),
            "n_trials": len(rows),
            "n_errors": 0,
            "resolved": passed,
            "mean_reward": passed / len(rows) if rows else 0.0,
            "max_samples": len(campaign.report_paths),
            "job_name": run_id,
            "job_dir": str(source_job_dir(reports[0])),
            "job_dir_role": "sample_1_config_anchor",
            "tasks": rows,
        },
        "completeness": {
            "expected_trials": len(rows),
            "trial_result_files": len(rows),
            "infra_invalid_trials": 0,
            "aggregated_at": created_at,
        },
        "tasks": rows,
    }
    return report


def validate_best_report(
    report: dict[str, Any], campaign: Campaign, output_path: Path
) -> None:
    validate_source_report(report, output_path)
    if report.get("raw_sampling_predecessor") is not False:
        raise ValueError("Best report must not be used as a sequential raw predecessor")
    if report.get("skills") != {
        "root": str(campaign.setting.skill_root),
        "tree_sha256": campaign.skill_tree_sha256,
    }:
        raise ValueError("Best report skill identity changed")
    lineage = report.get("lineage") or {}
    if canonical_json_sha256(lineage) != (report.get("provenance") or {}).get(
        "lineage_sha256"
    ):
        raise ValueError("Best report lineage hash is invalid")
    sources = lineage.get("sources") or []
    if len(sources) != len(campaign.report_paths):
        raise ValueError("Best report has a different number of source samples")
    for index, (source, path, expected_hash) in enumerate(
        zip(sources, campaign.report_paths, campaign.report_sha256, strict=True), 1
    ):
        if (
            source.get("sample_index") != index
            or Path(str(source.get("report_path") or "")).expanduser().resolve()
            != path
            or source.get("report_sha256") != expected_hash
        ):
            raise ValueError(f"Best report source sample {index} changed")
        check_snapshot(path, expected_hash, label=f"best report source {index}")
    evaluation = report.get("evaluation") or {}
    if evaluation.get("tasks") != report.get("tasks"):
        raise ValueError("Best report top-level and evaluation tasks differ")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Run additional same-setting DeepSWE samples, where every cohort is "
            "exactly the predecessor raw report's numeric reward-zero set."
        )
    )
    parser.add_argument("--launcher-dir", type=Path, required=True)
    parser.add_argument("--tts-run-dir", type=Path, required=True)
    parser.add_argument("--existing-bon-dir", type=Path, default=None)
    parser.add_argument("--output-dir", type=Path, required=True)
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
    parser.add_argument("--additional-rounds", type=int, default=2)
    parser.add_argument("--max-gate", type=int, default=3)
    parser.add_argument("--poll-sec", type=float, default=120)
    parser.add_argument("--min-temp-free-gb", type=float, default=5.0)
    parser.add_argument("--wait-timeout-sec", type=float, default=259200)
    parser.add_argument("--active-job-wait-timeout-sec", type=float, default=259200)
    parser.add_argument("--job-wall-timeout-sec", type=float, default=259200)
    parser.add_argument("--allow-failed-source-snapshot", action="store_true")
    parser.add_argument("--allow-code-change", action="append", default=[])
    parser.add_argument("--plan-only", action="store_true")
    return parser.parse_args()


def normalize_args(args: argparse.Namespace) -> None:
    for field_name in (
        "launcher_dir",
        "tts_run_dir",
        "output_dir",
        "dataset",
        "env_file",
    ):
        setattr(args, field_name, getattr(args, field_name).expanduser().resolve())
    args.existing_bon_dir = (
        args.existing_bon_dir.expanduser().resolve()
        if args.existing_bon_dir is not None
        else args.launcher_dir / "bon_sampling"
    )
    args.python = (
        str(Path(args.python).expanduser()) if "/" in args.python else args.python
    )
    if args.max_gate != 3:
        raise ValueError("This completed DeepSWE snapshot contains gates 0..3 only")
    if args.additional_rounds not in {1, 2}:
        raise ValueError("--additional-rounds must be 1 or 2")
    if args.concurrency != 8:
        raise ValueError("This same-setting resume contract requires concurrency=8")


def run(args: argparse.Namespace) -> None:
    normalize_args(args)
    apply_source_runtime_knobs(args)
    campaigns, snapshots = build_campaigns(args)
    plan = campaign_plan(campaigns, snapshots, args)
    if args.plan_only:
        print(json.dumps(plan, indent=2, sort_keys=True))
        return

    args.output_dir.mkdir(parents=True, exist_ok=True)
    temp_dir = args.output_dir / "tmp"
    temp_dir.mkdir(parents=True, exist_ok=True)
    for name in ("TMPDIR", "TMP", "TEMP"):
        os.environ[name] = str(temp_dir)
    with exclusive_job_run(args.output_dir):
        wait_for_temp_space(
            temp_dir,
            args.min_temp_free_gb,
            args.poll_sec,
            args.wait_timeout_sec,
        )
        plan_path = args.output_dir / "plan.json"
        write_json_once(plan_path, plan)
        state_path = args.output_dir / "state.json"
        try:
            completed: list[dict[str, Any]] = []
            for additional_round in range(1, args.additional_rounds + 1):
                for campaign in campaigns:
                    write_json_atomic(
                        state_path,
                        {
                            "status": "running",
                            "additional_round": additional_round,
                            "setting": campaign.setting.name,
                            "completed_rounds": completed,
                            "updated_at": utc_now(),
                        },
                    )
                    manifest = run_round(
                        args=args,
                        campaign=campaign,
                        additional_round=additional_round,
                        snapshots=snapshots,
                    )
                    item = {
                        "setting": campaign.setting.name,
                        "additional_round": additional_round,
                        "sample_index": manifest["plan"]["sample_index"],
                        "status": manifest["status"],
                        "manifest": str(
                            args.output_dir
                            / campaign.setting.name
                            / f"sample_{manifest['plan']['sample_index']}"
                            / "manifest.json"
                        ),
                    }
                    completed.append(item)
                    campaign.new_rounds.append(item)

            summaries: list[dict[str, Any]] = []
            for campaign in campaigns:
                best = build_best_report(campaign)
                best_path = (
                    args.output_dir / campaign.setting.name / "best_report.json"
                )
                validate_best_report(best, campaign, best_path)
                write_json_once(best_path, best)
                summaries.append(
                    {
                        "setting": campaign.setting.name,
                        "gate_index": campaign.setting.gate_index,
                        "skills": best["skills"],
                        "samples": best["evaluation"]["max_samples"],
                        "resolved": best["evaluation"]["resolved"],
                        "n_tasks": best["evaluation"]["n_tasks"],
                        "best_report": str(best_path),
                        "best_report_sha256": sha256_file(best_path),
                    }
                )
            summary = {
                "schema_version": 1,
                "kind": "deepswe_sequential_reward0_sampling_summary",
                "status": "complete",
                "completed_at": utc_now(),
                "settings": summaries,
            }
            write_json_atomic(args.output_dir / "summary.json", summary)
            write_json_atomic(
                state_path,
                {
                    "status": "complete",
                    "completed_at": utc_now(),
                    "completed_rounds": completed,
                    "summary": str(args.output_dir / "summary.json"),
                },
            )
        except BaseException as exc:
            write_json_atomic(
                state_path,
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
