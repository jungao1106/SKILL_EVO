#!/usr/bin/env python
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.run_benchmark import _deepswe_result_infra_reason  # noqa: E402


class AggregationError(RuntimeError):
    """Raised when the baseline cannot be aggregated without guessing."""


@dataclass(frozen=True)
class DatasetTask:
    slug: str
    task_name: str
    checksum: str
    path: Path


def _read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(errors="replace"))
    except (OSError, json.JSONDecodeError) as exc:
        raise AggregationError(f"Cannot read JSON {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise AggregationError(f"Expected a JSON object: {path}")
    return value


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
    except OSError as exc:
        raise AggregationError(f"Cannot hash {path}: {exc}") from exc
    return digest.hexdigest()


def _write_text_atomic(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(text)
    os.replace(temporary, path)


def _write_json_atomic(path: Path, value: dict[str, Any]) -> None:
    _write_text_atomic(
        path,
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
    )


def _load_dataset(
    dataset_dir: Path,
    *,
    expected_tasks: int,
) -> tuple[dict[str, DatasetTask], dict[str, DatasetTask], str]:
    from harbor.models.task.task import Task

    if not dataset_dir.is_dir():
        raise AggregationError(f"Dataset directory does not exist: {dataset_dir}")

    by_slug: dict[str, DatasetTask] = {}
    by_alias: dict[str, DatasetTask] = {}
    for task_dir in sorted(
        path
        for path in dataset_dir.iterdir()
        if path.is_dir() and (path / "task.toml").is_file()
    ):
        try:
            task = Task(task_dir=task_dir)
            row = DatasetTask(
                slug=task_dir.name,
                task_name=str(task.name),
                checksum=str(task.checksum),
                path=task_dir.resolve(),
            )
        except Exception as exc:
            raise AggregationError(
                f"Cannot load dataset task {task_dir}: {type(exc).__name__}: {exc}"
            ) from exc
        if row.slug in by_slug:
            raise AggregationError(f"Duplicate dataset task slug: {row.slug}")
        by_slug[row.slug] = row
        for alias in {row.slug, row.task_name}:
            previous = by_alias.get(alias)
            if previous is not None and previous.slug != row.slug:
                raise AggregationError(
                    f"Ambiguous dataset task alias {alias}: {previous.slug}, {row.slug}"
                )
            by_alias[alias] = row

    if len(by_slug) != expected_tasks:
        raise AggregationError(
            f"Dataset task count mismatch: expected={expected_tasks}, "
            f"observed={len(by_slug)}"
        )
    task_set_sha256 = hashlib.sha256(
        "".join(
            f"{row.slug}\0{row.task_name}\0{row.checksum}\n"
            for row in sorted(by_slug.values(), key=lambda item: item.slug)
        ).encode("utf-8")
    ).hexdigest()
    return by_slug, by_alias, task_set_sha256


def _normalize_task(
    value: Any,
    *,
    aliases: dict[str, DatasetTask],
    context: str,
) -> DatasetTask:
    if not isinstance(value, str) or not value:
        raise AggregationError(f"Missing task name in {context}")
    task = aliases.get(value)
    if task is None:
        raise AggregationError(f"Unknown dataset task {value!r} in {context}")
    return task


def _load_legacy_index(
    path: Path,
    *,
    aliases: dict[str, DatasetTask],
    expected_slugs: set[str],
) -> dict[str, tuple[int, dict[str, Any]]]:
    rows: dict[str, tuple[int, dict[str, Any]]] = {}
    try:
        lines = path.read_text(errors="replace").splitlines()
    except OSError as exc:
        raise AggregationError(
            f"Cannot read legacy per-task index {path}: {exc}"
        ) from exc
    for line_number, line in enumerate(lines, start=1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError as exc:
            raise AggregationError(
                f"Invalid JSON in {path}:{line_number}: {exc}"
            ) from exc
        if not isinstance(row, dict):
            raise AggregationError(f"Expected object in {path}:{line_number}")
        task = _normalize_task(
            row.get("task"),
            aliases=aliases,
            context=f"{path}:{line_number}",
        )
        if task.slug in rows:
            raise AggregationError(
                f"Duplicate legacy per-task row for {task.slug}: "
                f"lines {rows[task.slug][0]} and {line_number}"
            )
        rows[task.slug] = (line_number, row)

    observed = set(rows)
    if observed != expected_slugs:
        missing = sorted(expected_slugs - observed)
        extra = sorted(observed - expected_slugs)
        raise AggregationError(
            "Legacy per-task index does not exactly cover the dataset: "
            f"missing={missing}, extra={extra}"
        )
    return rows


def _legacy_result_path(
    *,
    trace_repo_root: Path,
    summary: dict[str, Any],
    task: DatasetTask,
    trial: dict[str, Any],
) -> Path:
    trace_path = trial.get("trace_path")
    trial_name = trial.get("trial")
    if not isinstance(trace_path, str) or not trace_path:
        raise AggregationError(f"Legacy trace_path is missing for {task.slug}")
    if not isinstance(trial_name, str) or not trial_name:
        raise AggregationError(f"Legacy trial name is missing for {task.slug}")
    relative = Path(trace_path)
    if relative.is_absolute() or ".." in relative.parts:
        raise AggregationError(
            f"Unsafe legacy trace_path for {task.slug}: {trace_path}"
        )

    candidates = [
        trace_repo_root / relative / "result.json",
        trace_repo_root / "results" / relative / "result.json",
    ]
    declared_trace_dir = summary.get("trace_dir")
    if isinstance(declared_trace_dir, str) and declared_trace_dir:
        declared = Path(declared_trace_dir)
        if not declared.is_absolute() and ".." not in declared.parts:
            candidates.append(
                trace_repo_root / declared / task.slug / trial_name / "result.json"
            )
    candidates.append(trace_repo_root / task.slug / trial_name / "result.json")

    existing = sorted(
        {candidate.resolve() for candidate in candidates if candidate.is_file()},
        key=str,
    )
    if not existing:
        raise AggregationError(
            f"Cannot resolve legacy result.json for {task.slug} from {trace_path} "
            f"under {trace_repo_root}"
        )
    if len(existing) != 1:
        raise AggregationError(
            f"Ambiguous legacy result.json for {task.slug}: "
            + ", ".join(str(path) for path in existing)
        )
    return existing[0]


def _execution_profile(
    *,
    agent_info: dict[str, Any],
    result_config: dict[str, Any],
    result_agent: dict[str, Any],
    agent_kwargs: dict[str, Any],
) -> dict[str, Any]:
    """Extract the recorded, non-secret runtime knobs needed to compare lineages."""

    environment = result_config.get("environment")
    if not isinstance(environment, dict):
        environment = {}
    environment_kwargs = environment.get("kwargs")
    if not isinstance(environment_kwargs, dict):
        environment_kwargs = {}
    resume_contract = agent_kwargs.get("resume_contract")
    if not isinstance(resume_contract, dict):
        resume_contract = {}
    runtime_knobs = resume_contract.get("runtime_knobs")
    if not isinstance(runtime_knobs, dict):
        runtime_knobs = {}
    dependencies = resume_contract.get("dependency_versions")
    if not isinstance(dependencies, dict):
        dependencies = {}

    return {
        "agent": {
            "name": agent_info.get("name"),
            "version": agent_info.get("version"),
            "import_path": result_agent.get("import_path"),
            "configured_sdk_version": agent_kwargs.get("claude_sdk_version"),
            "override_timeout_sec": result_agent.get("override_timeout_sec"),
            "override_setup_timeout_sec": result_agent.get(
                "override_setup_timeout_sec"
            ),
            "max_turns": agent_kwargs.get("max_turns"),
            "max_budget_usd": agent_kwargs.get("max_budget_usd"),
        },
        "environment": {
            "import_path": environment.get("import_path"),
            "override_cpus": environment.get("override_cpus"),
            "override_memory_mb": environment.get("override_memory_mb"),
            "override_storage_mb": environment.get("override_storage_mb"),
            "sandbox_timeout_sec": environment_kwargs.get("sandbox_timeout_sec"),
            "force_allow_internet": environment_kwargs.get("force_allow_internet"),
        },
        "timeouts": {
            "timeout_multiplier": result_config.get("timeout_multiplier"),
            "verifier_timeout_multiplier": result_config.get(
                "verifier_timeout_multiplier"
            ),
        },
        "recorded_runtime_knobs": {
            key: runtime_knobs.get(key)
            for key in (
                "FORCE_DISABLE_THINKING",
                "NOVITA_REASONING_EFFORT",
                "NOVITA_ENABLE_THINKING",
            )
            if key in runtime_knobs
        },
        "recorded_dependencies": {
            key: dependencies.get(key)
            for key in ("harbor", "e2b", "claude-agent-sdk")
            if key in dependencies
        },
        "artifact_hook_version": resume_contract.get("artifact_hook_version"),
    }


def _validated_result_row(
    result_path: Path,
    *,
    expected_task: DatasetTask,
    aliases: dict[str, DatasetTask],
    source: str,
    provenance: dict[str, Any],
    expected_provider: str,
    expected_model: str,
    expected_trial_name: str | None = None,
) -> dict[str, Any]:
    result = _read_json(result_path)
    result_task = _normalize_task(
        result.get("task_name"),
        aliases=aliases,
        context=str(result_path),
    )
    if result_task.slug != expected_task.slug:
        raise AggregationError(
            f"Task identity mismatch in {result_path}: expected={expected_task.slug}, "
            f"observed={result_task.slug}"
        )
    result_trial_name = result.get("trial_name")
    if not isinstance(result_trial_name, str) or not result_trial_name:
        raise AggregationError(f"Missing trial_name in {result_path}")
    if expected_trial_name is not None and result_trial_name != expected_trial_name:
        raise AggregationError(
            f"Trial identity mismatch in {result_path}: expected={expected_trial_name}, "
            f"observed={result_trial_name}"
        )
    observed_checksum = result.get("task_checksum")
    if observed_checksum != expected_task.checksum:
        raise AggregationError(
            f"Task checksum mismatch for {expected_task.slug} in {result_path}: "
            f"expected={expected_task.checksum}, observed={observed_checksum}"
        )

    agent_info = result.get("agent_info")
    model_info = agent_info.get("model_info") if isinstance(agent_info, dict) else None
    observed_provider = (
        str(model_info.get("provider") or "") if isinstance(model_info, dict) else ""
    )
    observed_model = (
        str(model_info.get("name") or "") if isinstance(model_info, dict) else ""
    )
    if observed_provider != expected_provider or observed_model != expected_model:
        raise AggregationError(
            f"Provider/model mismatch in {result_path}: "
            f"expected={expected_provider}/{expected_model}, "
            f"observed={observed_provider}/{observed_model}"
        )
    result_config = result.get("config")
    result_agent = (
        result_config.get("agent") if isinstance(result_config, dict) else None
    )
    if not isinstance(result_agent, dict):
        raise AggregationError(f"Missing agent config in {result_path}")
    configured_model = str(result_agent.get("model_name") or "")
    if configured_model not in {
        expected_model,
        f"{expected_provider}/{expected_model}",
    }:
        raise AggregationError(
            f"Configured model mismatch in {result_path}: {configured_model!r}"
        )
    agent_kwargs = result_agent.get("kwargs") or {}
    if (
        result_agent.get("skills")
        or not isinstance(agent_kwargs, dict)
        or agent_kwargs.get("use_skills") is True
        or result_config.get("extra_instruction_paths")
    ):
        raise AggregationError(f"Result is not a no-skills trial: {result_path}")

    exception_info = result.get("exception_info")
    if exception_info is not None and not isinstance(exception_info, dict):
        raise AggregationError(f"Malformed exception_info in {result_path}")
    infra_reason = _deepswe_result_infra_reason(result)
    if infra_reason is not None:
        raise AggregationError(
            f"Infra-invalid result for {expected_task.slug} in {result_path}: "
            f"{infra_reason}"
        )
    rewards = (result.get("verifier_result") or {}).get("rewards") or {}
    reward = rewards.get("reward")
    if isinstance(reward, bool) or not isinstance(reward, (int, float)):
        raise AggregationError(f"Non-numeric reward in {result_path}: {reward!r}")
    normalized_reward = int(reward)
    if float(reward) not in {0.0, 1.0}:
        raise AggregationError(f"Out-of-domain reward in {result_path}: {reward!r}")

    execution_profile = _execution_profile(
        agent_info=agent_info,
        result_config=result_config,
        result_agent=result_agent,
        agent_kwargs=agent_kwargs,
    )
    execution_profile_sha256 = hashlib.sha256(
        json.dumps(
            execution_profile,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    return {
        "task": expected_task.slug,
        "task_name": expected_task.task_name,
        "task_checksum": expected_task.checksum,
        "source": source,
        "source_provenance": provenance,
        "trial_name": result_trial_name,
        "reward": normalized_reward,
        "passed": normalized_reward == 1,
        "exception_type": (
            str(exception_info.get("exception_type") or "") or None
            if exception_info is not None
            else None
        ),
        "exception_info": exception_info,
        "execution_profile": execution_profile,
        "execution_profile_sha256": execution_profile_sha256,
        "result_path": str(result_path.resolve()),
        "result_sha256": _sha256_file(result_path),
    }


def _supplement_task_names(
    config: dict[str, Any],
    *,
    aliases: dict[str, DatasetTask],
    expected_supplement_tasks: int,
    expected_provider: str,
    expected_model: str,
) -> dict[str, DatasetTask]:
    if int(config.get("n_attempts") or 0) != 1:
        raise AggregationError("Supplement job must use exactly one attempt")
    agents = config.get("agents")
    if not isinstance(agents, list) or len(agents) != 1:
        raise AggregationError("Supplement job must configure exactly one agent")
    agent = agents[0]
    if not isinstance(agent, dict):
        raise AggregationError("Malformed supplement agent config")
    if agent.get("model_name") != f"{expected_provider}/{expected_model}":
        raise AggregationError(
            "Supplement job provider/model mismatch: "
            f"expected={expected_provider}/{expected_model}, "
            f"observed={agent.get('model_name')}"
        )
    if agent.get("skills"):
        raise AggregationError(
            "Supplement job is not a no-skills run: agent.skills is set"
        )
    kwargs = agent.get("kwargs") or {}
    if not isinstance(kwargs, dict) or kwargs.get("use_skills") is not False:
        raise AggregationError(
            "Supplement job is not explicitly no-skills: kwargs.use_skills must be false"
        )
    if kwargs.get("provider_name") != expected_provider:
        raise AggregationError(
            "Supplement job provider mismatch: "
            f"expected={expected_provider}, observed={kwargs.get('provider_name')}"
        )
    if kwargs.get("benchmark_name") != "deepswe":
        raise AggregationError("Supplement job is not configured for DeepSWE")
    contract = kwargs.get("resume_contract") or {}
    contract_skills = contract.get("skills") if isinstance(contract, dict) else None
    if (
        isinstance(contract_skills, dict)
        and contract_skills.get("enabled") is not False
    ):
        raise AggregationError(
            "Supplement job resume contract does not explicitly disable skills"
        )

    declared: dict[str, DatasetTask] = {}
    datasets = config.get("datasets")
    if not isinstance(datasets, list):
        raise AggregationError("Supplement config has no datasets list")
    raw_names: list[Any] = []
    for dataset in datasets:
        if not isinstance(dataset, dict):
            raise AggregationError("Malformed supplement dataset config")
        names = dataset.get("task_names")
        if names is None:
            continue
        if not isinstance(names, list):
            raise AggregationError("Supplement dataset task_names must be a list")
        raw_names.extend(names)
    for index, raw_name in enumerate(raw_names):
        task = _normalize_task(
            raw_name,
            aliases=aliases,
            context=f"supplement config task_names[{index}]",
        )
        if task.slug in declared:
            raise AggregationError(f"Duplicate task in supplement config: {task.slug}")
        declared[task.slug] = task
    if len(declared) != expected_supplement_tasks:
        raise AggregationError(
            "Supplement task count mismatch: "
            f"expected={expected_supplement_tasks}, observed={len(declared)}"
        )
    return declared


def _load_supplement_rows(
    job_dir: Path,
    *,
    aliases: dict[str, DatasetTask],
    expected_supplement_tasks: int,
    expected_provider: str,
    expected_model: str,
) -> tuple[dict[str, dict[str, Any]], dict[str, Any]]:
    if not job_dir.is_dir():
        raise AggregationError(f"Supplement job directory does not exist: {job_dir}")
    config_path = job_dir / "config.json"
    root_result_path = job_dir / "result.json"
    config = _read_json(config_path)
    root_result = _read_json(root_result_path)
    if not root_result.get("finished_at"):
        raise AggregationError(f"Supplement job is unfinished: {root_result_path}")
    try:
        root_total = int(root_result.get("n_total_trials"))
    except (TypeError, ValueError) as exc:
        raise AggregationError(
            f"Supplement root result has invalid n_total_trials: {root_result_path}"
        ) from exc
    if root_total != expected_supplement_tasks:
        raise AggregationError(
            "Supplement root trial count mismatch: "
            f"expected={expected_supplement_tasks}, observed={root_total}"
        )

    declared = _supplement_task_names(
        config,
        aliases=aliases,
        expected_supplement_tasks=expected_supplement_tasks,
        expected_provider=expected_provider,
        expected_model=expected_model,
    )
    observed: dict[str, dict[str, Any]] = {}
    result_paths = sorted(
        path
        for path in job_dir.glob("*/result.json")
        if path.resolve() != root_result_path.resolve()
    )
    for result_path in result_paths:
        raw_result = _read_json(result_path)
        task = _normalize_task(
            raw_result.get("task_name"),
            aliases=aliases,
            context=str(result_path),
        )
        if task.slug not in declared:
            raise AggregationError(
                f"Unexpected task result in supplement job: {task.slug} ({result_path})"
            )
        if task.slug in observed:
            raise AggregationError(
                f"Duplicate supplement result for {task.slug}: "
                f"{observed[task.slug]['result_path']}, {result_path}"
            )
        observed[task.slug] = _validated_result_row(
            result_path,
            expected_task=task,
            aliases=aliases,
            source="supplement_job",
            provenance={
                "job_dir": str(job_dir.resolve()),
                "job_config_path": str(config_path.resolve()),
                "job_config_sha256": _sha256_file(config_path),
                "root_result_path": str(root_result_path.resolve()),
                "root_result_sha256": _sha256_file(root_result_path),
            },
            expected_provider=expected_provider,
            expected_model=expected_model,
            expected_trial_name=result_path.parent.name,
        )
    if set(observed) != set(declared):
        missing = sorted(set(declared) - set(observed))
        extra = sorted(set(observed) - set(declared))
        raise AggregationError(
            "Supplement results do not exactly match configured tasks: "
            f"missing={missing}, extra={extra}"
        )
    return observed, {
        "job_dir": str(job_dir.resolve()),
        "config_path": str(config_path.resolve()),
        "config_sha256": _sha256_file(config_path),
        "root_result_path": str(root_result_path.resolve()),
        "root_result_sha256": _sha256_file(root_result_path),
        "configured_tasks": sorted(declared),
    }


def aggregate(
    *,
    legacy_per_task_path: Path,
    legacy_summary_path: Path,
    legacy_trace_repo_root: Path,
    supplement_job_dir: Path,
    dataset_dir: Path,
    expected_tasks: int = 113,
    expected_supplement_tasks: int = 29,
    expected_provider: str = "novita",
    expected_model: str = "zai-org/glm-5.2",
) -> dict[str, Any]:
    legacy_per_task_path = legacy_per_task_path.expanduser().resolve()
    legacy_summary_path = legacy_summary_path.expanduser().resolve()
    legacy_trace_repo_root = legacy_trace_repo_root.expanduser().resolve()
    supplement_job_dir = supplement_job_dir.expanduser().resolve()
    dataset_dir = dataset_dir.expanduser().resolve()

    dataset, aliases, task_set_sha256 = _load_dataset(
        dataset_dir,
        expected_tasks=expected_tasks,
    )
    summary = _read_json(legacy_summary_path)
    try:
        summary_full_set_size = int(summary.get("full_set_size"))
    except (TypeError, ValueError) as exc:
        raise AggregationError("Legacy summary has invalid full_set_size") from exc
    if summary_full_set_size != expected_tasks:
        raise AggregationError(
            "Legacy summary full_set_size mismatch: "
            f"expected={expected_tasks}, observed={summary_full_set_size}"
        )
    legacy_index = _load_legacy_index(
        legacy_per_task_path,
        aliases=aliases,
        expected_slugs=set(dataset),
    )
    supplement_rows, supplement_provenance = _load_supplement_rows(
        supplement_job_dir,
        aliases=aliases,
        expected_supplement_tasks=expected_supplement_tasks,
        expected_provider=expected_provider,
        expected_model=expected_model,
    )

    rows: list[dict[str, Any]] = []
    for task in sorted(dataset.values(), key=lambda item: item.slug):
        if task.slug in supplement_rows:
            rows.append(supplement_rows[task.slug])
            continue
        line_number, index_row = legacy_index[task.slug]
        trials = index_row.get("trials")
        if not isinstance(trials, list):
            raise AggregationError(
                f"Legacy trials must be a list for {task.slug} at "
                f"{legacy_per_task_path}:{line_number}"
            )
        if len(trials) != 1:
            detail = "missing" if not trials else "multiple"
            raise AggregationError(
                f"Legacy task {task.slug} has {detail} raw trials ({len(trials)}); "
                "best-of-N and inferred rewards are forbidden"
            )
        trial = trials[0]
        if not isinstance(trial, dict):
            raise AggregationError(f"Malformed legacy trial pointer for {task.slug}")
        result_path = _legacy_result_path(
            trace_repo_root=legacy_trace_repo_root,
            summary=summary,
            task=task,
            trial=trial,
        )
        rows.append(
            _validated_result_row(
                result_path,
                expected_task=task,
                aliases=aliases,
                source="legacy_trace",
                provenance={
                    "per_task_path": str(legacy_per_task_path),
                    "per_task_line": line_number,
                    "declared_trace_path": trial.get("trace_path"),
                    "declared_trial_name": trial.get("trial"),
                    "declared_job": trial.get("job"),
                },
                expected_provider=expected_provider,
                expected_model=expected_model,
                expected_trial_name=str(trial.get("trial") or ""),
            )
        )

    task_counts = Counter(row["task"] for row in rows)
    duplicates = sorted(task for task, count in task_counts.items() if count != 1)
    observed_slugs = set(task_counts)
    if len(rows) != expected_tasks or observed_slugs != set(dataset) or duplicates:
        raise AggregationError(
            "Combined baseline is not a unique complete task set: "
            f"rows={len(rows)}, expected={expected_tasks}, "
            f"missing={sorted(set(dataset) - observed_slugs)}, duplicates={duplicates}"
        )

    n_passed = sum(int(row["reward"] == 1) for row in rows)
    exception_counts = Counter(
        str(row["exception_type"]) for row in rows if row.get("exception_type")
    )
    source_counts = Counter(str(row["source"]) for row in rows)
    execution_profile_counts = Counter(
        str(row["execution_profile_sha256"]) for row in rows
    )
    execution_profiles = []
    for profile_sha256, count in sorted(execution_profile_counts.items()):
        representative = next(
            row for row in rows if row["execution_profile_sha256"] == profile_sha256
        )
        execution_profiles.append(
            {
                "sha256": profile_sha256,
                "n_tasks": count,
                "sources": sorted(
                    {
                        str(row["source"])
                        for row in rows
                        if row["execution_profile_sha256"] == profile_sha256
                    }
                ),
                "profile": representative["execution_profile"],
            }
        )
    return {
        "schema_version": 1,
        "benchmark_name": "deepswe",
        "complete": True,
        "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "aggregation_policy": {
            "reward_authority": "verifier_result.rewards.reward in source result.json",
            "legacy_per_task_reward_used": False,
            "legacy_summary_score_used": False,
            "supplement_overrides_legacy": True,
            "best_of_n": False,
            "duration_heuristics": False,
            "valid_rewards": [0, 1],
            "infra_results_allowed": False,
            "provider": expected_provider,
            "model": expected_model,
            "skills_enabled": False,
            "execution_profile_homogeneous": len(execution_profiles) == 1,
            "execution_profile_differences_recorded": True,
        },
        "overall": {
            "expected_tasks": expected_tasks,
            "n_tasks": len(rows),
            "n_passed": n_passed,
            "n_failed": len(rows) - n_passed,
            "pass_rate": n_passed / len(rows),
            "pass_rate_percent": 100.0 * n_passed / len(rows),
            "source_counts": dict(sorted(source_counts.items())),
            "exception_counts": dict(sorted(exception_counts.items())),
            "n_execution_profiles": len(execution_profiles),
            "execution_profiles": execution_profiles,
        },
        "provenance": {
            "legacy_per_task_path": str(legacy_per_task_path),
            "legacy_per_task_sha256": _sha256_file(legacy_per_task_path),
            "legacy_summary_path": str(legacy_summary_path),
            "legacy_summary_sha256": _sha256_file(legacy_summary_path),
            "legacy_trace_repo_root": str(legacy_trace_repo_root),
            "supplement": supplement_provenance,
            "dataset_dir": str(dataset_dir),
            "dataset_tasks": len(dataset),
            "dataset_task_set_sha256": task_set_sha256,
        },
        "tasks": rows,
    }


def write_report(report: dict[str, Any], out_dir: Path) -> dict[str, str]:
    out_dir = out_dir.expanduser().resolve()
    score_report_path = out_dir / "score_report.json"
    summary_path = out_dir / "summary.json"
    per_task_path = out_dir / "per_task.jsonl"
    _write_json_atomic(score_report_path, report)
    summary = {key: value for key, value in report.items() if key != "tasks"}
    _write_json_atomic(summary_path, summary)
    _write_text_atomic(
        per_task_path,
        "".join(
            json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n"
            for row in report["tasks"]
        ),
    )
    return {
        "score_report": str(score_report_path),
        "summary": str(summary_path),
        "per_task": str(per_task_path),
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Build an auditable 113-task DeepSWE no-skills baseline by replacing "
            "declared legacy tasks with a completed supplement job."
        )
    )
    parser.add_argument("--legacy-per-task", type=Path, required=True)
    parser.add_argument("--legacy-summary", type=Path, required=True)
    parser.add_argument("--legacy-trace-repo-root", type=Path, required=True)
    parser.add_argument("--supplement-job-dir", type=Path, required=True)
    parser.add_argument("--dataset", type=Path, required=True)
    parser.add_argument("--out-dir", type=Path, required=True)
    parser.add_argument("--expected-tasks", type=int, default=113)
    parser.add_argument("--expected-supplement-tasks", type=int, default=29)
    parser.add_argument("--expected-provider", default="novita")
    parser.add_argument("--expected-model", default="zai-org/glm-5.2")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    try:
        report = aggregate(
            legacy_per_task_path=args.legacy_per_task,
            legacy_summary_path=args.legacy_summary,
            legacy_trace_repo_root=args.legacy_trace_repo_root,
            supplement_job_dir=args.supplement_job_dir,
            dataset_dir=args.dataset,
            expected_tasks=args.expected_tasks,
            expected_supplement_tasks=args.expected_supplement_tasks,
            expected_provider=args.expected_provider,
            expected_model=args.expected_model,
        )
        outputs = write_report(report, args.out_dir)
    except AggregationError as exc:
        raise SystemExit(f"DeepSWE baseline aggregation failed closed: {exc}") from exc
    print(
        json.dumps(
            {
                "complete": report["complete"],
                **report["overall"],
                "outputs": outputs,
            },
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
