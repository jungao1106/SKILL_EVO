#!/usr/bin/env python
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from evolution.tts_evolution import (  # noqa: E402
    collect_failed_trace_evidence,
    generate_test_time_decisions,
    materialize_gate_library,
    safe_slug,
    write_json,
    write_jsonl,
)
from scripts.materialize_swebench_tts_evolution_gates import (  # noqa: E402
    DEFAULT_POLICY_STATE,
    load_evaluator_policy,
)
from scripts.job_run_lock import exclusive_job_run, job_is_running  # noqa: E402


TASKWISE_OR_AGGREGATE_KIND = "deepswe_same_setting_or_aggregate"
TASKWISE_OR_SAMPLING_POLICY = {
    "n": 2,
    "sample_2_subset": "sample_1_valid_reward_equal_to_0",
    "aggregation": "maximum reward per task within the same skill setting",
    "cross_gate_aggregation": False,
    "infra_retries_count_as_samples": False,
    "tts_feedback": False,
}


def render_report_md(manifest: dict[str, Any]) -> str:
    summary = manifest["summary"]
    lines = [
        "# DeepSWE Test-Time Skill Evolution Gates",
        "",
        "## Summary",
        "",
        f"- Run id: `{manifest['run_id']}`",
        f"- Source direct run: `{manifest['source_run_id']}`",
        f"- Failed traces used for evolution: `{summary['task_evidence']}`",
        "- Evolution verifier access: `false`",
        "- Promotion source: `evaluator_only`",
        f"- Repo candidates: `{summary['repo_candidates']}`",
        f"- Failure-mode candidates: `{summary['failure_candidates']}`",
        f"- Promoted test-time skills: `{summary['promoted_skills']}`",
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


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def sha256_tree(root: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(item for item in root.rglob("*") if item.is_file()):
        relative = path.relative_to(root).as_posix().encode("utf-8")
        digest.update(len(relative).to_bytes(8, "big"))
        digest.update(relative)
        digest.update(bytes.fromhex(sha256_file(path)))
    return digest.hexdigest()


def _task_rows(report: dict[str, Any], *, label: str) -> list[dict[str, Any]]:
    rows = report.get("tasks") or (report.get("evaluation") or {}).get("tasks")
    if not isinstance(rows, list) or not all(isinstance(row, dict) for row in rows):
        raise ValueError(f"{label} has no valid task rows")
    names = [row.get("task_name") for row in rows]
    if not all(isinstance(name, str) and name for name in names):
        raise ValueError(f"{label} has an invalid task_name")
    if len(names) != len(set(names)):
        raise ValueError(f"{label} contains duplicate tasks")
    return rows


def _binary_attempt(row: dict[str, Any], *, label: str) -> dict[str, Any]:
    raw_reward = row.get("reward")
    reward = (
        float(raw_reward)
        if not isinstance(raw_reward, bool) and isinstance(raw_reward, (int, float))
        else None
    )
    if reward not in {0.0, 1.0}:
        raise ValueError(f"{label} has a non-binary reward: {raw_reward!r}")
    trial_name = row.get("trial_name")
    result_path = row.get("result_path")
    exception_type = row.get("exception_type")
    if not isinstance(trial_name, str) or not trial_name:
        raise ValueError(f"{label} has no trial_name")
    if not isinstance(result_path, str) or not result_path:
        raise ValueError(f"{label} has no result_path")
    if exception_type is not None and not isinstance(exception_type, str):
        raise ValueError(f"{label} has an invalid exception_type")
    return {
        "trial_name": trial_name,
        "reward": reward,
        "exception_type": exception_type,
        "result_path": result_path,
    }


def _task_map(
    report: dict[str, Any],
    *,
    label: str,
) -> dict[str, dict[str, Any]]:
    return {
        str(row["task_name"]): _binary_attempt(row, label=f"{label} {row['task_name']}")
        for row in _task_rows(report, label=label)
    }


def _normalized_same_setting_job_config(config: dict[str, Any]) -> dict[str, Any]:
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


def _config_resume_contract(config: dict[str, Any], *, label: str) -> dict[str, Any]:
    agents = config.get("agents")
    if not isinstance(agents, list) or not agents:
        raise ValueError(f"{label} has no agents")
    contracts: list[dict[str, Any]] = []
    for agent in agents:
        if not isinstance(agent, dict):
            raise ValueError(f"{label} has an invalid agent")
        contract = (agent.get("kwargs") or {}).get("resume_contract")
        if not isinstance(contract, dict):
            raise ValueError(f"{label} agent has no resume contract")
        contracts.append(contract)
    if any(contract != contracts[0] for contract in contracts[1:]):
        raise ValueError(f"{label} agents have inconsistent resume contracts")
    return contracts[0]


def _validate_expected_skill_root(
    report: dict[str, Any],
    expected_skill_root: Path,
) -> None:
    provenance = report.get("provenance") or {}
    contract = provenance.get("resume_contract")
    if not isinstance(contract, dict):
        raise ValueError("aggregate report has no resume contract")
    skills = contract.get("skills") or {}
    roots = skills.get("roots") or []
    hashes = skills.get("tree_sha256") or []
    expected_skill_root = expected_skill_root.expanduser().resolve()
    try:
        resolved_roots = [str(Path(str(root)).expanduser().resolve()) for root in roots]
    except (OSError, ValueError) as exc:
        raise ValueError("aggregate report has an invalid skill root") from exc
    if resolved_roots != [str(expected_skill_root)]:
        raise ValueError(
            "aggregate skill root differs from the requested base skill root"
        )
    current_hash = sha256_tree(expected_skill_root)
    if hashes != [current_hash]:
        raise ValueError("aggregate skill tree differs from the requested base skills")


def validate_taskwise_or_aggregate(
    report: dict[str, Any],
    path: Path,
    *,
    expected_skill_root: Path | None = None,
) -> list[tuple[dict[str, Any], Path, dict[str, Any]]]:
    """Validate a taskwise OR report against both immutable source reports."""

    if report.get("kind") != TASKWISE_OR_AGGREGATE_KIND:
        raise ValueError("report is not a taskwise OR aggregate")
    evaluation = report.get("evaluation")
    top_level_rows = report.get("tasks")
    evaluation_rows = evaluation.get("tasks") if isinstance(evaluation, dict) else None
    if (
        not isinstance(top_level_rows, list)
        or not top_level_rows
        or not all(isinstance(row, dict) for row in top_level_rows)
        or evaluation_rows != top_level_rows
    ):
        raise ValueError(
            "taskwise OR top-level tasks must be non-empty and equal evaluation.tasks"
        )
    ordered_task_names = [str(row.get("task_name") or "") for row in top_level_rows]
    if ordered_task_names != sorted(ordered_task_names):
        raise ValueError("taskwise OR tasks are not in canonical task-name order")
    if report.get("sampling_policy") != TASKWISE_OR_SAMPLING_POLICY:
        raise ValueError("taskwise OR sampling policy changed")
    lineage = report.get("lineage")
    if not isinstance(lineage, dict):
        raise ValueError("taskwise OR report has no lineage")
    if lineage.get("aggregation") != "taskwise_max_binary_reward":
        raise ValueError("taskwise OR report has an invalid aggregation policy")
    if lineage.get("tie_breaker") != "prefer_sample_1":
        raise ValueError("taskwise OR report has an invalid tie breaker")
    sources = lineage.get("sources")
    if not isinstance(sources, list) or len(sources) != 2:
        raise ValueError("taskwise OR report must have exactly two sources")
    if (
        not all(isinstance(source, dict) for source in sources)
        or [source.get("sample") for source in sources] != [1, 2]
        or any(type(source.get("sample")) is not int for source in sources)
    ):
        raise ValueError("taskwise OR sources must be ordered samples 1 and 2")

    loaded_sources: list[tuple[dict[str, Any], Path, dict[str, Any]]] = []
    for source in sources:
        if not isinstance(source, dict):
            raise ValueError("taskwise OR source metadata must be an object")
        source_path_value = source.get("report_path")
        if not isinstance(source_path_value, str) or not source_path_value:
            raise ValueError("taskwise OR source has no report_path")
        source_path = Path(source_path_value).expanduser().resolve()
        if source_path == path.expanduser().resolve():
            raise ValueError("taskwise OR report cannot reference itself")
        if not source_path.is_file():
            raise ValueError(f"taskwise OR source report is missing: {source_path}")
        if source.get("report_sha256") != sha256_file(source_path):
            raise ValueError(f"taskwise OR source report hash changed: {source_path}")
        try:
            source_report = json.loads(source_path.read_text(errors="strict"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise ValueError(
                f"invalid taskwise OR source report: {source_path}"
            ) from exc
        if not isinstance(source_report, dict):
            raise ValueError(
                f"taskwise OR source report is not an object: {source_path}"
            )
        if source_report.get("kind") == TASKWISE_OR_AGGREGATE_KIND:
            raise ValueError("nested taskwise OR source reports are unsupported")
        if source.get("run_id") != source_report.get("run_id"):
            raise ValueError("taskwise OR source run_id changed")
        source_rows = _task_rows(source_report, label=f"Source {source['sample']}")
        if type(source.get("n_tasks")) is not int or source.get("n_tasks") != len(
            source_rows
        ):
            raise ValueError("taskwise OR source task count changed")
        source_job_dir = (source_report.get("evaluation") or {}).get("job_dir")
        if not isinstance(source_job_dir, str) or not source_job_dir:
            raise ValueError("taskwise OR source has no evaluation.job_dir")
        if (
            Path(str(source.get("job_dir") or "")).expanduser().resolve()
            != Path(source_job_dir).expanduser().resolve()
        ):
            raise ValueError("taskwise OR source job_dir changed")
        validate_source_aggregate(
            source_report,
            source_path,
            expected_run_id=str(source["run_id"]),
            expected_benchmark_name="deepswe",
        )
        loaded_sources.append((source, source_path, source_report))

    expected_lineage_sha256 = hashlib.sha256(
        json.dumps(lineage, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    provenance = report.get("provenance")
    if not isinstance(provenance, dict):
        raise ValueError("taskwise OR report has no provenance")
    if provenance.get("benchmark_name") != "deepswe":
        raise ValueError("taskwise OR provenance benchmark changed")
    if provenance.get("lineage_sha256") != expected_lineage_sha256:
        raise ValueError("taskwise OR lineage hash mismatch")
    source_contracts = [
        (source_report.get("provenance") or {}).get("resume_contract")
        for _source, _source_path, source_report in loaded_sources
    ]
    if provenance.get("resume_contract") != source_contracts[0]:
        raise ValueError("taskwise OR provenance resume contract changed")
    if not all(isinstance(contract, dict) for contract in source_contracts):
        raise ValueError("taskwise OR source resume contract is missing")
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
        if source_contracts[1].get(key) != source_contracts[0].get(key):
            raise ValueError(f"taskwise OR source contract differs at {key}")
    first_dataset = source_contracts[0].get("dataset") or {}
    second_dataset = source_contracts[1].get("dataset") or {}
    for key in ("path", "tree_sha256"):
        if second_dataset.get(key) != first_dataset.get(key):
            raise ValueError(f"taskwise OR source dataset differs at {key}")
    contract_skills = source_contracts[0].get("skills") or {}
    skill_roots = contract_skills.get("roots") or []
    skill_hashes = contract_skills.get("tree_sha256") or []
    if (
        len(skill_roots) != 1
        or len(skill_hashes) != 1
        or report.get("skills")
        != {
            "root": skill_roots[0],
            "tree_sha256": skill_hashes[0],
        }
    ):
        raise ValueError("taskwise OR skill identity changed")
    skill_root = Path(str(skill_roots[0])).expanduser().resolve()
    if not skill_root.is_dir() or sha256_tree(skill_root) != skill_hashes[0]:
        raise ValueError("taskwise OR skill tree changed")
    if (
        expected_skill_root is not None
        and skill_root != expected_skill_root.expanduser().resolve()
    ):
        raise ValueError(
            "taskwise OR skill root differs from the requested base skill root"
        )

    source_configs: list[dict[str, Any]] = []
    for index, (source, _source_path, source_report) in enumerate(loaded_sources):
        job_dir = Path(str(source["job_dir"])).expanduser().resolve()
        config_path = job_dir / "config.json"
        if not config_path.is_file():
            raise ValueError(f"taskwise OR source config is missing: {config_path}")
        source_provenance = source_report.get("provenance") or {}
        if source_provenance.get("job_config_sha256") != sha256_file(config_path):
            raise ValueError(f"taskwise OR source config hash changed: {config_path}")
        try:
            config = json.loads(config_path.read_text(errors="strict"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise ValueError(
                f"invalid taskwise OR source config: {config_path}"
            ) from exc
        if not isinstance(config, dict):
            raise ValueError(
                f"taskwise OR source config is not an object: {config_path}"
            )
        if (
            _config_resume_contract(
                config,
                label=f"Taskwise OR source {source['sample']} config",
            )
            != source_contracts[index]
        ):
            raise ValueError(
                f"taskwise OR source {source['sample']} report contract "
                "differs from its job config"
            )
        source_configs.append(_normalized_same_setting_job_config(config))
    if source_configs[0] != source_configs[1]:
        raise ValueError("taskwise OR job configs differ beyond job/task identity")

    first = _task_map(loaded_sources[0][2], label="Sample 1")
    second = _task_map(loaded_sources[1][2], label="Sample 2")
    expected_second = {
        task_name for task_name, attempt in first.items() if attempt["reward"] == 0.0
    }
    if set(second) != expected_second:
        raise ValueError(
            "taskwise OR sample 2 task set is not sample 1 reward-zero set"
        )
    merged_rows = _task_rows(report, label="Taskwise OR report")
    if {str(row["task_name"]) for row in merged_rows} != set(first):
        raise ValueError("taskwise OR merged task set differs from sample 1")

    resolved = 0
    selected_exceptions = 0
    for row in merged_rows:
        task_name = str(row["task_name"])
        first_attempt = first[task_name]
        second_attempt = second.get(task_name)
        attempts = row.get("attempts")
        if not isinstance(attempts, dict):
            raise ValueError(f"taskwise OR row has no attempts: {task_name}")
        nested_first = attempts.get("sample_1")
        nested_second = attempts.get("sample_2")
        if (
            not isinstance(nested_first, dict)
            or _binary_attempt(nested_first, label=f"Merged sample 1 {task_name}")
            != first_attempt
        ):
            raise ValueError(f"taskwise OR sample 1 attempt changed: {task_name}")
        if nested_second is not None and not isinstance(nested_second, dict):
            raise ValueError(f"taskwise OR sample 2 attempt is invalid: {task_name}")
        normalized_second = (
            _binary_attempt(nested_second, label=f"Merged sample 2 {task_name}")
            if isinstance(nested_second, dict)
            else None
        )
        if normalized_second != second_attempt:
            raise ValueError(f"taskwise OR sample 2 attempt changed: {task_name}")
        selected_sample = (
            2
            if second_attempt is not None
            and second_attempt["reward"] > first_attempt["reward"]
            else 1
        )
        selected = second_attempt if selected_sample == 2 else first_attempt
        if (
            type(row.get("selected_sample")) is not int
            or row.get("selected_sample") != selected_sample
        ):
            raise ValueError(f"taskwise OR selected_sample changed: {task_name}")
        if _binary_attempt(row, label=f"Merged row {task_name}") != selected:
            raise ValueError(f"taskwise OR selected result changed: {task_name}")
        resolved += int(selected["reward"] == 1.0)
        selected_exceptions += int(selected["exception_type"] is not None)

    task_names = sorted(first)
    expected_task_hash = hashlib.sha256(
        ("\n".join(task_names) + "\n").encode("utf-8")
    ).hexdigest()
    if provenance.get("task_set_sha256") != expected_task_hash:
        raise ValueError("taskwise OR task set hash mismatch")
    if not isinstance(evaluation, dict):
        raise ValueError("taskwise OR report has no evaluation")
    total = len(first)
    expected_mean = resolved / total if total else 0.0
    if (
        type(evaluation.get("n_trials")) is not int
        or evaluation.get("n_trials") != total
        or type(evaluation.get("resolved")) is not int
        or evaluation.get("resolved") != resolved
        or type(evaluation.get("n_errors")) is not int
        or evaluation.get("n_errors") != selected_exceptions
        or isinstance(evaluation.get("mean_reward"), bool)
        or evaluation.get("mean_reward") != expected_mean
        or evaluation.get("tasks") != merged_rows
    ):
        raise ValueError("taskwise OR evaluation summary is inconsistent")
    if (
        evaluation.get("job_name") != report.get("run_id")
        or evaluation.get("job_dir_role") != "sample_1_config_anchor"
        or Path(str(evaluation.get("job_dir") or "")).expanduser().resolve()
        != Path(str(sources[0]["job_dir"])).expanduser().resolve()
    ):
        raise ValueError("taskwise OR evaluation source anchor changed")
    completeness = report.get("completeness")
    if not isinstance(completeness, dict) or (
        type(completeness.get("expected_trials")) is not int
        or completeness.get("expected_trials") != total
        or type(completeness.get("trial_result_files")) is not int
        or completeness.get("trial_result_files") != total
        or type(completeness.get("infra_invalid_trials")) is not int
        or completeness.get("infra_invalid_trials") != 0
        or not completeness.get("aggregated_at")
        or completeness.get("aggregated_at") != report.get("created_at")
    ):
        raise ValueError("taskwise OR completeness is inconsistent")
    return loaded_sources


def validate_source_aggregate(
    report: dict[str, Any],
    path: Path,
    *,
    expected_run_id: str | None = None,
    expected_benchmark_name: str | None = None,
    expected_skill_root: Path | None = None,
) -> None:
    invalid = list(report.get("infra_invalid_trials") or [])
    rows = report.get("tasks") or (report.get("evaluation") or {}).get("tasks") or []
    for row in rows:
        raw_reward = row.get("reward")
        try:
            reward = float(raw_reward) if not isinstance(raw_reward, bool) else None
        except (TypeError, ValueError):
            reward = None
        if reward not in {0.0, 1.0}:
            invalid.append(
                {
                    "trial_name": row.get("trial_name"),
                    "reason": f"invalid-reward:{raw_reward}",
                }
            )
    completeness = report.get("completeness") or {}
    expected = int(completeness.get("expected_trials") or 0)
    actual = int(
        completeness.get("trial_result_files")
        or (report.get("evaluation") or {}).get("n_trials")
        or 0
    )
    task_names = [str(row.get("task_name") or "") for row in rows]
    duplicate_tasks = len(task_names) != len(set(task_names))
    exact_count = not expected or actual == expected == len(rows)
    run_id_matches = expected_run_id is None or report.get("run_id") == expected_run_id
    benchmark_matches = (
        expected_benchmark_name is None
        or report.get("benchmark_name") == expected_benchmark_name
    )
    if (
        report.get("complete") is not True
        or invalid
        or not exact_count
        or duplicate_tasks
        or not run_id_matches
        or not benchmark_matches
    ):
        raise SystemExit(
            "Refusing to materialize skills from an incomplete or infra-invalid "
            f"aggregate: {path} complete={report.get('complete')} "
            f"trials={actual}/{expected or '?'} infra_invalid={len(invalid)} "
            f"duplicate_tasks={duplicate_tasks}"
            f" run_id={report.get('run_id')!r} expected_run_id={expected_run_id!r}"
            f" benchmark={report.get('benchmark_name')!r} "
            f"expected_benchmark={expected_benchmark_name!r}"
        )
    if report.get("kind") == TASKWISE_OR_AGGREGATE_KIND:
        try:
            validate_taskwise_or_aggregate(
                report,
                path,
                expected_skill_root=expected_skill_root,
            )
        except ValueError as exc:
            raise SystemExit(
                f"Refusing invalid taskwise OR aggregate {path}: {exc}"
            ) from exc
    elif expected_skill_root is not None:
        try:
            _validate_expected_skill_root(report, expected_skill_root)
        except ValueError as exc:
            raise SystemExit(
                f"Refusing aggregate with a different base skill setting {path}: {exc}"
            ) from exc


def materialization_is_reusable(
    run_dir: Path,
    skill_run_root: Path,
    *,
    expected_fingerprints: dict[str, Any] | None = None,
) -> bool:
    required = (
        run_dir / "manifest.json",
        run_dir / "promotion_decisions.jsonl",
        run_dir / "gates" / "gate_000" / "manifest.json",
        run_dir / "gates" / "gate_001" / "manifest.json",
        skill_run_root / "gate_000" / "gate_library_manifest.json",
        skill_run_root / "gate_001" / "gate_library_manifest.json",
    )
    if not all(path.is_file() for path in required):
        return False
    try:
        manifest = json.loads((run_dir / "manifest.json").read_text())
    except (OSError, json.JSONDecodeError):
        return False
    if (
        expected_fingerprints is not None
        and manifest.get("input_fingerprints") != expected_fingerprints
    ):
        return False
    output_fingerprints = manifest.get("output_fingerprints")
    if not isinstance(output_fingerprints, dict):
        return False
    return output_fingerprints == {
        "gate_000_tree_sha256": sha256_tree(skill_run_root / "gate_000"),
        "gate_001_tree_sha256": sha256_tree(skill_run_root / "gate_001"),
    }


def archive_existing_materialization(run_dir: Path, skill_run_root: Path) -> None:
    archive_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    for source, kind in ((run_dir, "run"), (skill_run_root, "skills")):
        if not source.exists():
            continue
        destination = (
            source.parent
            / ".rematerialization_archive"
            / source.name
            / archive_id
            / kind
        )
        destination.parent.mkdir(parents=True, exist_ok=True)
        os.replace(source, destination)


def active_jobs_referencing_skill_root(skill_run_root: Path) -> list[str]:
    target = skill_run_root.resolve()
    active: list[str] = []
    jobs_root = ROOT / "jobs"
    if not jobs_root.is_dir():
        return active
    for job_dir in sorted(path for path in jobs_root.iterdir() if path.is_dir()):
        if not job_is_running(job_dir):
            continue
        config_path = job_dir / "config.json"
        if not config_path.is_file():
            continue
        try:
            config = json.loads(config_path.read_text(errors="replace"))
        except (OSError, json.JSONDecodeError):
            continue
        roots: list[str] = []
        for agent in config.get("agents") or []:
            contract = ((agent.get("kwargs") or {}).get("resume_contract") or {})
            roots.extend((contract.get("skills") or {}).get("roots") or [])
        for root in roots:
            try:
                resolved = Path(root).expanduser().resolve()
                references_target = resolved == target or resolved.is_relative_to(target)
            except (OSError, ValueError):
                references_target = False
            if references_target:
                active.append(job_dir.name)
                break
    return active


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Materialize DeepSWE test-time skill evolution gate libraries."
    )
    parser.add_argument("--source-run-id", required=True)
    parser.add_argument("--source-aggregate", type=Path, required=True)
    parser.add_argument("--base-skill-root", type=Path, required=True)
    parser.add_argument("--policy-state", type=Path, default=DEFAULT_POLICY_STATE)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--out-root", type=Path, default=ROOT / "run_logs" / "deepswe_tts_evo")
    parser.add_argument("--skill-output-root", type=Path, default=ROOT / "skills" / "test_time")
    parser.add_argument("--benchmark-name", default="deepswe")
    parser.add_argument("--reward-threshold", type=float, default=1.0)
    parser.add_argument("--max-evidence", type=int, default=None)
    parser.add_argument("--repo-update-batch-size", type=int, default=5)
    parser.add_argument("--repo-min-support", type=int, default=2)
    parser.add_argument("--repo-min-positive-support", type=int, default=0)
    parser.add_argument("--failure-mode-min-repo-support", type=int, default=2)
    parser.add_argument("--max-repo-skills-per-gate", type=int, default=12)
    parser.add_argument("--max-failure-skills-per-gate", type=int, default=12)
    parser.add_argument(
        "--force-rematerialize",
        action="store_true",
        help="Replace an existing gate library. By default a complete materialization is reused.",
    )
    return parser.parse_args()


def run_materialization(args: argparse.Namespace) -> None:
    args.source_aggregate = args.source_aggregate.expanduser().resolve()
    args.base_skill_root = args.base_skill_root.expanduser().resolve()
    args.policy_state = args.policy_state.expanduser().resolve()
    args.out_root = args.out_root.expanduser().resolve()
    args.skill_output_root = args.skill_output_root.expanduser().resolve()
    if not args.source_aggregate.exists():
        raise SystemExit(f"Missing source aggregate report: {args.source_aggregate}")
    if not args.base_skill_root.exists():
        raise SystemExit(f"Missing base skill root: {args.base_skill_root}")
    if not args.policy_state.is_file():
        raise SystemExit(f"Missing evaluator policy state: {args.policy_state}")
    try:
        source_report = json.loads(args.source_aggregate.read_text(errors="replace"))
    except json.JSONDecodeError as exc:
        raise SystemExit(
            f"Invalid source aggregate JSON: {args.source_aggregate}: {exc}"
        ) from exc
    validate_source_aggregate(
        source_report,
        args.source_aggregate,
        expected_run_id=args.source_run_id,
        expected_benchmark_name=args.benchmark_name,
        expected_skill_root=args.base_skill_root,
    )

    input_fingerprints = {
        "source_aggregate_sha256": sha256_file(args.source_aggregate),
        "base_skill_tree_sha256": sha256_tree(args.base_skill_root),
        "policy_state_sha256": sha256_file(args.policy_state),
        "code_sha256": {
            str(path.relative_to(ROOT)): sha256_file(path)
            for path in (
                ROOT / "evolution" / "tts_evolution.py",
                ROOT / "scripts" / "materialize_deepswe_tts_evolution_gates.py",
                ROOT / "scripts" / "materialize_swebench_tts_evolution_gates.py",
            )
        },
        "source_run_id": args.source_run_id,
        "benchmark_name": args.benchmark_name,
        "reward_threshold": args.reward_threshold,
        "max_evidence": args.max_evidence,
        "repo_update_batch_size": args.repo_update_batch_size,
        "repo_min_support": args.repo_min_support,
        "repo_min_positive_support": args.repo_min_positive_support,
        "failure_mode_min_repo_support": args.failure_mode_min_repo_support,
        "max_repo_skills_per_gate": args.max_repo_skills_per_gate,
        "max_failure_skills_per_gate": args.max_failure_skills_per_gate,
    }

    run_id = safe_slug(args.run_id, limit=150)
    run_dir = args.out_root / run_id
    skill_run_root = args.skill_output_root / run_id
    if not args.force_rematerialize and materialization_is_reusable(
        run_dir,
        skill_run_root,
        expected_fingerprints=input_fingerprints,
    ):
        print(f"[deepswe-tts-evo] reuse existing run_dir={run_dir}")
        print(f"[deepswe-tts-evo] reuse existing skill_run_root={skill_run_root}")
        return
    if not args.force_rematerialize and (
        run_dir.exists() or skill_run_root.exists()
    ):
        raise SystemExit(
            "Existing DeepSWE gate materialization is incomplete; inspect it or rerun "
            "with --force-rematerialize: "
            f"run_dir={run_dir} skill_run_root={skill_run_root}"
        )
    if args.force_rematerialize:
        active_jobs = active_jobs_referencing_skill_root(skill_run_root)
        if active_jobs:
            raise SystemExit(
                "Refusing to rematerialize a skill root used by active jobs: "
                + ", ".join(active_jobs)
            )
        archive_existing_materialization(run_dir, skill_run_root)
    evaluator_policy = load_evaluator_policy(args.policy_state)

    evidence_rows = collect_failed_trace_evidence(
        aggregate_report_path=args.source_aggregate,
        reward_threshold=args.reward_threshold,
        max_evidence=args.max_evidence,
        benchmark_name=args.benchmark_name,
    )
    generated = generate_test_time_decisions(
        evidence_rows=evidence_rows,
        run_name=run_id,
        benchmark_name=args.benchmark_name,
        evaluator_policy=evaluator_policy,
        repo_update_batch_size=args.repo_update_batch_size,
        repo_min_support=args.repo_min_support,
        repo_min_positive_support=args.repo_min_positive_support,
        failure_mode_min_repo_support=args.failure_mode_min_repo_support,
        max_repo_skills_per_gate=args.max_repo_skills_per_gate,
        max_failure_skills_per_gate=args.max_failure_skills_per_gate,
    )

    gate0_root = skill_run_root / "gate_000"
    gate1_root = skill_run_root / "gate_001"
    if gate0_root.exists():
        shutil.rmtree(gate0_root)
    gate0_root.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(args.base_skill_root, gate0_root)
    base_count = len(list(args.base_skill_root.rglob("SKILL.md")))
    gate0_manifest = {
        "schema_version": 1,
        "kind": "test_time_skill_evolution_gate_library",
        "run_name": run_id,
        "gate_index": 0,
        "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "base_skill_root": str(args.base_skill_root),
        "output_root": str(gate0_root),
        "include_base": True,
        "copied_base": True,
        "promotion_source": "none",
        "verifier_access_for_evolution": False,
        "verifier_report": {"status": "not_run"},
        "skill_counts": {"base": base_count, "test_time_promoted": 0, "total": base_count},
        "skills": [],
    }
    write_json(gate0_root / "gate_library_manifest.json", gate0_manifest)

    gate1_manifest = materialize_gate_library(
        base_skill_root=args.base_skill_root,
        output_root=gate1_root,
        run_name=run_id,
        promotion_decisions=generated["promotion_decisions"],
        gate_index=1,
        include_base=True,
        clean=True,
    )
    promoted = sum(1 for row in generated["promotion_decisions"] if row.get("decision") == "promote")
    manifest: dict[str, Any] = {
        "schema_version": 1,
        "kind": "deepswe_test_time_skill_evolution",
        "run_id": run_id,
        "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "benchmark_name": args.benchmark_name,
        "source_run_id": args.source_run_id,
        "source_aggregate": str(args.source_aggregate),
        "base_skill_root": str(args.base_skill_root),
        "run_dir": str(run_dir),
        "skill_run_root": str(skill_run_root),
        "evaluator_policy": evaluator_policy,
        "input_fingerprints": input_fingerprints,
        "output_fingerprints": {
            "gate_000_tree_sha256": sha256_tree(gate0_root),
            "gate_001_tree_sha256": sha256_tree(gate1_root),
        },
        "evolution_contract": {
            "target_trace_filter": "source direct run reward != 1",
            "candidate_generation": "writer_from_public_trace_evidence",
            "promotion": "evaluator_only",
            "verifier_access_for_evolution": False,
            "verifier_metrics": "report_only_after_each_gate_eval",
        },
        "parameters": {
            "reward_threshold": args.reward_threshold,
            "repo_update_batch_size": args.repo_update_batch_size,
            "repo_min_support": args.repo_min_support,
            "repo_min_positive_support": args.repo_min_positive_support,
            "failure_mode_min_repo_support": args.failure_mode_min_repo_support,
            "max_repo_skills_per_gate": args.max_repo_skills_per_gate,
            "max_failure_skills_per_gate": args.max_failure_skills_per_gate,
        },
        "summary": {
            "task_evidence": len(evidence_rows),
            "repo_clusters": len(generated["repo_clusters"]),
            "failure_clusters": len(generated["failure_clusters"]),
            "repo_candidates": len(generated["repo_candidates"]),
            "failure_candidates": len(generated["failure_candidates"]),
            "evaluator_decisions": len(generated["evaluator_decisions"]),
            "promoted_skills": promoted,
        },
        "gates": [
            {
                "gate_index": 0,
                "skill_root": str(gate0_root),
                "skill_counts": gate0_manifest["skill_counts"],
                "promotion_source": "none",
                "verifier_report": gate0_manifest["verifier_report"],
            },
            {
                "gate_index": 1,
                "skill_root": str(gate1_root),
                "skill_counts": gate1_manifest["skill_counts"],
                "promotion_source": "evaluator_only",
                "verifier_report": {"status": "not_run"},
            },
        ],
    }
    for gate_index, gate_manifest in ((0, gate0_manifest), (1, gate1_manifest)):
        write_json(run_dir / "gates" / f"gate_{gate_index:03d}" / "manifest.json", gate_manifest)
    write_jsonl(run_dir / "evidence" / "task_evidence.jsonl", evidence_rows)
    write_jsonl(run_dir / "evidence" / "repo_clusters.jsonl", generated["repo_clusters"])
    write_jsonl(run_dir / "evidence" / "failure_clusters.jsonl", generated["failure_clusters"])
    write_jsonl(run_dir / "candidates" / "repo_candidates.jsonl", generated["repo_candidates"])
    write_jsonl(run_dir / "candidates" / "failure_mode_candidates.jsonl", generated["failure_candidates"])
    write_jsonl(run_dir / "evaluator" / "evaluator_decisions.jsonl", generated["evaluator_decisions"])
    write_jsonl(run_dir / "evaluator" / "evaluator_calibration.jsonl", generated["evaluator_calibration"])
    write_jsonl(run_dir / "promotion_decisions.jsonl", generated["promotion_decisions"])
    write_json(run_dir / "manifest.json", manifest)
    (run_dir / "report.md").write_text(render_report_md(manifest))
    print(f"[deepswe-tts-evo] run_dir={run_dir}")
    print(f"[deepswe-tts-evo] skill_run_root={skill_run_root}")
    print(
        "[deepswe-tts-evo] evidence={evidence} repo_candidates={repo_candidates} "
        "failure_candidates={failure_candidates} promoted={promoted}".format(
            evidence=len(evidence_rows),
            repo_candidates=len(generated["repo_candidates"]),
            failure_candidates=len(generated["failure_candidates"]),
            promoted=promoted,
        )
    )


def main() -> None:
    args = parse_args()
    lock_dir = (
        args.out_root.expanduser().resolve()
        / ".evolution-locks"
        / safe_slug(args.run_id, limit=150)
    )
    with exclusive_job_run(lock_dir):
        run_materialization(args)


if __name__ == "__main__":
    main()
