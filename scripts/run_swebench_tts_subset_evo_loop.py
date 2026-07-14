#!/usr/bin/env python
from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import time
import tomllib
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = ROOT
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from evolution.score import summarize_job, trial_result_paths  # noqa: E402
from evolution.tts_evolution import (  # noqa: E402
    collect_failed_trace_evidence,
    generate_test_time_decisions,
    materialize_gate_library,
    safe_slug,
    write_json,
    write_jsonl,
)
from providers import normalize_provider_name, resolve_provider  # noqa: E402
from scripts.job_run_lock import exclusive_job_run, job_is_running  # noqa: E402
from scripts.run_benchmark import _deepswe_result_infra_reason  # noqa: E402
from scripts.materialize_swebench_tts_evolution_gates import (  # noqa: E402
    DEFAULT_PYTHON,
    load_evaluator_policy,
)
from scripts.materialize_deepswe_tts_evolution_gates import (  # noqa: E402
    render_report_md,
    sha256_file,
    sha256_tree,
    validate_source_aggregate,
)


DEFAULT_POLICY_STATE = (
    ROOT
    / "run_logs"
    / "swegym_skill_evo"
    / "swegym_novita_glm52_c15_resume_merged_20260630_071956"
    / "training"
    / "policy_state.json"
)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(errors="replace"))


def write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


def load_env_file(path: Path) -> dict[str, str]:
    env: dict[str, str] = {}
    if not path.exists():
        return env
    for raw_line in path.read_text(errors="replace").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, raw_value = line.split("=", 1)
        key = key.strip()
        value = raw_value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        env[key] = value
    return env


def reward_value(row: dict[str, Any]) -> float | None:
    value = row.get("reward")
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def successful_tasks(report: dict[str, Any]) -> set[str]:
    evaluation = report.get("evaluation") if isinstance(report.get("evaluation"), dict) else {}
    rows = evaluation.get("tasks") or report.get("tasks") or []
    return {
        str(row.get("task_name") or "")
        for row in rows
        if str(row.get("task_name") or "") and (reward_value(row) or 0.0) >= 1.0
    }


def all_report_tasks(report: dict[str, Any]) -> set[str]:
    evaluation = report.get("evaluation") if isinstance(report.get("evaluation"), dict) else {}
    rows = evaluation.get("tasks") or report.get("tasks") or []
    return {
        str(row.get("task_name") or "")
        for row in rows
        if str(row.get("task_name") or "")
    }


def dataset_filter_task_names(
    dataset: str,
    logical_task_names: set[str],
) -> set[str]:
    dataset_path = Path(dataset).expanduser()
    if not dataset_path.is_dir() or not (dataset_path / "dataset.toml").is_file():
        return set(logical_task_names)
    logical_to_filter: dict[str, str] = {}
    for task_dir in sorted(path for path in dataset_path.iterdir() if path.is_dir()):
        task_toml = task_dir / "task.toml"
        if not task_toml.is_file():
            continue
        try:
            config = tomllib.loads(task_toml.read_text(encoding="utf-8"))
        except (OSError, tomllib.TOMLDecodeError):
            continue
        logical_name = str((config.get("task") or {}).get("name") or task_dir.name)
        logical_to_filter[logical_name] = task_dir.name
    missing = sorted(logical_task_names - set(logical_to_filter))
    if missing:
        raise SystemExit(
            "Could not map logical task names to local dataset filters: "
            + ", ".join(missing[:10])
        )
    return {logical_to_filter[name] for name in logical_task_names}


def job_progress(job_dir: Path) -> tuple[int, int | None, str | None]:
    root_result = job_dir / "result.json"
    if not root_result.exists():
        return 0, None, None
    try:
        root = read_json(root_result)
    except json.JSONDecodeError:
        return len(trial_result_paths(job_dir)), None, None
    total = root.get("n_total_trials")
    try:
        total_int = int(total) if total is not None else None
    except (TypeError, ValueError):
        total_int = None
    stats = root.get("stats") if isinstance(root.get("stats"), dict) else {}
    n_trials = stats.get("n_trials")
    try:
        current = int(n_trials)
    except (TypeError, ValueError):
        current = len(trial_result_paths(job_dir))
    return current, total_int, root.get("finished_at")


def job_is_complete(job_dir: Path, expected_trials: int) -> bool:
    current, total, finished_at = job_progress(job_dir)
    target = total or expected_trials
    actual_results = len(trial_result_paths(job_dir))
    if not (
        finished_at
        and current >= target
        and current >= expected_trials
        and actual_results >= target
        and actual_results >= expected_trials
    ):
        return False
    for trial_dir in (path for path in job_dir.iterdir() if path.is_dir()):
        result_path = trial_dir / "result.json"
        if not result_path.is_file():
            return False
        try:
            result = read_json(result_path)
        except (OSError, json.JSONDecodeError):
            return False
        if _deepswe_result_infra_reason(result) is not None:
            return False
    return True


def gate_row(manifest: dict[str, Any], gate_index: int) -> dict[str, Any]:
    for gate in manifest.get("gates") or []:
        if int(gate.get("gate_index") or 0) == gate_index:
            return gate
    raise SystemExit(f"Missing gate_{gate_index:03d} in manifest")


def subset_job_name(
    run_id: str,
    gate_index: int,
    previous_gate_index: int,
    task_names: set[str] | None = None,
    execution_identity: str | None = None,
) -> str:
    suffix_parts: list[str] = []
    if task_names is not None:
        digest = hashlib.sha256(
            ("\n".join(sorted(task_names)) + "\n").encode("utf-8")
        ).hexdigest()[:10]
        suffix_parts.append(digest)
    if execution_identity:
        suffix_parts.append(execution_identity[:10])
    suffix = "_" + "_".join(suffix_parts) if suffix_parts else ""
    prefix = safe_slug(
        f"{run_id}_gate{gate_index:03d}_on_gate{previous_gate_index:03d}"
        "_unresolved_subset_eval",
        limit=max(1, 150 - len(suffix)),
    )
    return prefix + suffix


def subset_execution_payload(
    args: argparse.Namespace, gate_root: Path
) -> dict[str, Any]:
    configured_env = os.environ.copy()
    configured_env.update(load_env_file(args.env_file))
    configured_env.update(
        {
            "FORCE_DISABLE_THINKING": "1",
            "PI_SKILL_RETRIEVAL_SCOPE": "transfer",
            "PI_USE_SKILL_HARNESS_MEMORY": "false",
            "CLAUDE_USE_SKILL_HARNESS_MEMORY": "false",
        }
    )
    provider_name = normalize_provider_name(args.provider)
    provider = resolve_provider(provider_name)
    model = (
        args.provider_model
        or configured_env.get(provider.model_env)
        or provider.default_model
    )
    if args.harness == "claude-code":
        endpoint = (
            args.provider_anthropic_base_url
            or (
                configured_env.get(provider.anthropic_base_url_env)
                if provider.anthropic_base_url_env
                else None
            )
            or provider.default_anthropic_base_url
        )
        provider_api = None
    else:
        endpoint = (
            args.provider_base_url
            or configured_env.get(provider.base_url_env)
            or provider.default_base_url
        )
        provider_api = (
            args.provider_api
            or configured_env.get(provider.provider_api_env)
            or provider.default_provider_api
        )
    claude_sdk_version = (
        getattr(args, "claude_sdk_version", None)
        or configured_env.get("CLAUDE_AGENT_SDK_VERSION")
        or "0.2.116"
    )
    pi_version = (
        getattr(args, "pi_version", None)
        or configured_env.get("PI_CODING_AGENT_VERSION")
        or "0.80.6"
    )
    runtime_knob_names = (
        "FORCE_DISABLE_THINKING",
        "HARBOR_CLAUDE_DISABLE_THINKING_PROXY",
        "HARBOR_CLAUDE_KEEP_PROXY",
        "CLAUDE_CODE_MAX_OUTPUT_TOKENS",
        "TIMEOUT_MULTIPLIER",
        "AGENT_TIMEOUT_MULTIPLIER",
        "VERIFIER_TIMEOUT_MULTIPLIER",
        "AGENT_SETUP_TIMEOUT_MULTIPLIER",
        "ENVIRONMENT_BUILD_TIMEOUT_MULTIPLIER",
        "PI_SKILL_RETRIEVAL_SCOPE",
        "PI_MIN_ACTIVE_SKILL_QUALITY",
        "PI_USE_SKILL_HARNESS_MEMORY",
        "CLAUDE_USE_SKILL_HARNESS_MEMORY",
        "PI_SKILL_HARNESS_MEMORY_PATH",
        "PI_SKILL_HARNESS_MEMORY_MAX_ENTRIES",
        "PI_SKILL_HARNESS_MEMORY_MAX_CHARS",
        "PI_MIN_MEMORY_SKILL_QUALITY",
        "CLAUDE_SKILL_PROMPT_MAX_CHARS_PER_SKILL",
        f"{provider.env_prefix}_REASONING_EFFORT",
        f"{provider.env_prefix}_ENABLE_THINKING",
    )
    configured_env[f"{provider.env_prefix}_REASONING_EFFORT"] = "none"
    configured_env[f"{provider.env_prefix}_ENABLE_THINKING"] = "false"
    return {
        "gate_tree_sha256": sha256_tree(gate_root),
        "benchmark_name": args.benchmark_name,
        "dataset": str(Path(args.dataset).expanduser().resolve()),
        "dataset_tree_sha256": sha256_tree(
            Path(args.dataset).expanduser().resolve()
        ),
        "provider": {
            "name": provider_name,
            "model": model,
            "endpoint": endpoint,
            "agent": args.harness,
            "provider_api": provider_api,
        },
        "agent_parameters": {
            "max_turns": args.claude_max_turns,
            "max_budget_usd": args.claude_max_budget_usd,
        }
        if args.harness == "claude-code"
        else {},
        "agent_timeout_sec": args.agent_timeout_sec,
        "agent_setup_timeout_sec": args.agent_setup_timeout_sec,
        "e2b_sandbox_timeout_sec": args.e2b_sandbox_timeout_sec,
        "dependency_versions": {
            "claude-agent-sdk": claude_sdk_version,
            "pi-coding-agent": pi_version,
        },
        "runtime_knobs": {
            name: configured_env.get(name) for name in runtime_knob_names
        },
        "code_sha256": {
            str(path.relative_to(SOURCE_ROOT)): sha256_file(path)
            for path in (
                SOURCE_ROOT / "agents" / "claude_sdk_agent.py",
                SOURCE_ROOT / "agents" / "pi_agent.py",
                SOURCE_ROOT / "agents" / "skill_harness_memory.py",
                SOURCE_ROOT / "environments" / "e2b_swebench.py",
                SOURCE_ROOT / "providers" / "__init__.py",
                SOURCE_ROOT / "providers" / "specs.py",
                SOURCE_ROOT / "scripts" / "run_benchmark.py",
                SOURCE_ROOT / "scripts" / "run_swebench_tts_subset_evo_loop.py",
            )
        },
    }


def subset_execution_identity(args: argparse.Namespace, gate_root: Path) -> str:
    payload = subset_execution_payload(args, gate_root)
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def write_subset_report(
    *,
    path: Path,
    run_id: str,
    gate_index: int,
    previous_gate_index: int,
    job_name: str,
    job_dir: Path,
    task_file: Path,
    expected_tasks: set[str],
    previous_success_count: int,
    cumulative_success_count: int,
    benchmark_name: str,
) -> dict[str, Any]:
    summary = summarize_job(job_dir)
    for row in summary.get("tasks") or []:
        row.setdefault("source_job", job_name)
    recovered = successful_tasks({"evaluation": summary})
    observed_tasks = {
        str(row.get("task_name") or "")
        for row in summary.get("tasks") or []
        if str(row.get("task_name") or "")
    }
    complete = (
        observed_tasks == expected_tasks
        and len(summary.get("tasks") or []) == len(expected_tasks)
    )
    report = {
        "schema_version": 1,
        "kind": "tts_subset_eval_report",
        "run_id": run_id,
        "gate_index": gate_index,
        "previous_gate_index": previous_gate_index,
        "created_at": utc_now(),
        "job_name": job_name,
        "job_dir": str(job_dir),
        "task_file": str(task_file),
        "expected_trials": len(expected_tasks),
        "complete": complete,
        "benchmark_name": benchmark_name,
        "infra_invalid_trials": [],
        "evaluation": summary,
        "tasks": summary.get("tasks") or [],
        "completeness": {
            "expected_trials": len(expected_tasks),
            "trial_result_files": len(summary.get("tasks") or []),
        },
        "composition": {
            "previous_success_count": previous_success_count,
            "subset_recovered_count": len(recovered),
            "cumulative_success_count": cumulative_success_count,
            "remaining_unresolved_count": max(0, len(expected_tasks) - len(recovered)),
        },
    }
    write_json(path, report)
    return report


def materialize_next_gate(
    *,
    args: argparse.Namespace,
    run_dir: Path,
    manifest: dict[str, Any],
    gate_index: int,
    previous_gate_index: int,
    previous_gate_root: Path,
    source_report_path: Path,
    evaluator_policy: dict[str, Any],
) -> dict[str, Any]:
    validate_source_aggregate(
        read_json(source_report_path),
        source_report_path,
        expected_benchmark_name=args.benchmark_name,
    )
    parameter_payload = {
        "benchmark_name": args.benchmark_name,
        "reward_threshold": args.reward_threshold,
        "repo_update_batch_size": args.repo_update_batch_size,
        "repo_min_support": args.repo_min_support,
        "repo_min_positive_support": args.repo_min_positive_support,
        "failure_mode_min_repo_support": args.failure_mode_min_repo_support,
        "max_repo_skills_per_gate": args.max_repo_skills_per_gate,
        "max_failure_skills_per_gate": args.max_failure_skills_per_gate,
    }
    input_fingerprints = {
        "source_report_sha256": sha256_file(source_report_path),
        "previous_gate_tree_sha256": sha256_tree(previous_gate_root),
        "code_sha256": {
            str(path.relative_to(ROOT)): sha256_file(path)
            for path in (
                ROOT / "evolution" / "tts_evolution.py",
                ROOT / "scripts" / "run_swebench_tts_subset_evo_loop.py",
                ROOT / "scripts" / "materialize_deepswe_tts_evolution_gates.py",
                ROOT / "scripts" / "materialize_swebench_tts_evolution_gates.py",
            )
        },
        "evaluator_policy_sha256": hashlib.sha256(
            json.dumps(evaluator_policy, sort_keys=True).encode("utf-8")
        ).hexdigest(),
        "parameters_sha256": hashlib.sha256(
            json.dumps(parameter_payload, sort_keys=True).encode("utf-8")
        ).hexdigest(),
    }
    gate_root = args.skill_output_root / args.run_id / f"gate_{gate_index:03d}"
    gate_dir = run_dir / "gates" / f"gate_{gate_index:03d}"
    manifest_has_gate = any(
        int(gate.get("gate_index") or 0) == gate_index
        for gate in manifest.get("gates") or []
    )
    reusable = (
        (gate_root / "gate_library_manifest.json").is_file()
        and (gate_dir / "manifest.json").is_file()
        and manifest_has_gate
    )
    if reusable:
        stored_gate = read_json(gate_dir / "manifest.json")
        reusable = (
            stored_gate.get("evolution_input_fingerprints") == input_fingerprints
            and stored_gate.get("output_tree_sha256") == sha256_tree(gate_root)
        )
    if reusable:
        return read_json(gate_root / "gate_library_manifest.json")
    if gate_root.exists():
        archive = (
            gate_root.parent
            / ".partial_gate_archive"
            / gate_root.name
            / datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        )
        archive.parent.mkdir(parents=True, exist_ok=True)
        os.replace(gate_root, archive)
    if gate_dir.exists():
        archive = (
            gate_dir.parent
            / ".partial_gate_archive"
            / gate_dir.name
            / datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        )
        archive.parent.mkdir(parents=True, exist_ok=True)
        os.replace(gate_dir, archive)

    evidence_rows = collect_failed_trace_evidence(
        aggregate_report_path=source_report_path,
        reward_threshold=args.reward_threshold,
        benchmark_name=args.benchmark_name,
    )
    generated = generate_test_time_decisions(
        evidence_rows=evidence_rows,
        run_name=f"{args.run_id}_gate{gate_index:03d}",
        benchmark_name=args.benchmark_name,
        evaluator_policy=evaluator_policy,
        repo_update_batch_size=args.repo_update_batch_size,
        repo_min_support=args.repo_min_support,
        repo_min_positive_support=args.repo_min_positive_support,
        failure_mode_min_repo_support=args.failure_mode_min_repo_support,
        max_repo_skills_per_gate=args.max_repo_skills_per_gate,
        max_failure_skills_per_gate=args.max_failure_skills_per_gate,
    )
    gate_manifest = materialize_gate_library(
        base_skill_root=previous_gate_root,
        output_root=gate_root,
        run_name=f"{args.run_id}_gate{gate_index:03d}",
        promotion_decisions=generated["promotion_decisions"],
        gate_index=gate_index,
        include_base=True,
        clean=True,
    )
    write_jsonl(gate_dir / "promotion_decisions.jsonl", generated["promotion_decisions"])
    write_jsonl(run_dir / "evidence" / f"gate_{gate_index:03d}" / "task_evidence.jsonl", evidence_rows)
    write_jsonl(run_dir / "evidence" / f"gate_{gate_index:03d}" / "repo_clusters.jsonl", generated["repo_clusters"])
    write_jsonl(run_dir / "evidence" / f"gate_{gate_index:03d}" / "failure_clusters.jsonl", generated["failure_clusters"])
    write_jsonl(run_dir / "candidates" / f"gate_{gate_index:03d}" / "repo_candidates.jsonl", generated["repo_candidates"])
    write_jsonl(run_dir / "candidates" / f"gate_{gate_index:03d}" / "failure_mode_candidates.jsonl", generated["failure_candidates"])
    write_jsonl(run_dir / "evaluator" / f"gate_{gate_index:03d}" / "evaluator_decisions.jsonl", generated["evaluator_decisions"])

    promoted_count = sum(1 for row in generated["promotion_decisions"] if row.get("decision") == "promote")
    gate_entry = {
        "gate_index": gate_index,
        "skill_root": str(gate_root),
        "skill_counts": gate_manifest["skill_counts"],
        "promotion_source": "evaluator_only",
        "source_previous_gate": previous_gate_index,
        "source_aggregate": str(source_report_path),
        "verifier_report": {
            "status": "subset_pending",
            "note": (
                "This gate is evaluated only on the previous unresolved subset; "
                "compose with earlier successes."
            ),
        },
        "evolution_summary": {
            "task_evidence": len(evidence_rows),
            "repo_candidates": len(generated["repo_candidates"]),
            "failure_candidates": len(generated["failure_candidates"]),
            "evaluator_decisions": len(generated["evaluator_decisions"]),
            "promoted_skills": promoted_count,
        },
    }
    manifest["gates"] = [
        gate
        for gate in manifest.get("gates") or []
        if int(gate.get("gate_index") or 0) != gate_index
    ]
    manifest.setdefault("gates", []).append(gate_entry)
    run_gate_manifest = {
        **gate_manifest,
        "evolution_input_fingerprints": input_fingerprints,
        "output_tree_sha256": sha256_tree(gate_root),
    }
    write_json(gate_dir / "manifest.json", run_gate_manifest)
    write_json(run_dir / "manifest.json", manifest)
    return gate_manifest


def run_subset_eval(
    *,
    args: argparse.Namespace,
    gate_index: int,
    previous_gate_index: int,
    gate_root: Path,
    task_file: Path,
    expected_trials: int,
    job_name: str,
    log_path: Path,
) -> Path:
    job_dir = ROOT / "jobs" / job_name
    requested_tasks = {
        line.strip()
        for line in task_file.read_text(errors="replace").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    }
    config_path = job_dir / "config.json"
    if config_path.is_file():
        try:
            saved = read_json(config_path)
            datasets = saved.get("datasets") or []
            saved_tasks = set((datasets[0] or {}).get("task_names") or [])
        except (OSError, json.JSONDecodeError, IndexError, TypeError) as exc:
            raise SystemExit(f"Invalid subset job config {config_path}: {exc}") from exc
        if saved_tasks != requested_tasks:
            raise SystemExit(
                "Refusing to resume subset job with a different task set: "
                f"job={job_name} saved={len(saved_tasks)} requested={len(requested_tasks)}"
            )
        agents = saved.get("agents") or []
        saved_agent = agents[0] if agents else {}
        saved_kwargs = saved_agent.get("kwargs") or {}
        contract = saved_kwargs.get("resume_contract")
        if not isinstance(contract, dict):
            raise SystemExit(
                f"Refusing to reuse subset job without a resume contract: {job_name}"
            )
        saved_skill_trees = ((contract.get("skills") or {}).get("tree_sha256") or [])
        expected_skill_tree = sha256_tree(gate_root)
        if saved_skill_trees != [expected_skill_tree]:
            raise SystemExit(
                "Refusing to reuse subset job with a different gate skill tree: "
                f"job={job_name} saved={saved_skill_trees} "
                f"expected={[expected_skill_tree]}"
            )
        saved_provider = contract.get("provider") or {}
        expected_execution = subset_execution_payload(args, gate_root)
        expected_provider = expected_execution["provider"]
        if saved_provider != expected_provider:
            raise SystemExit(
                "Refusing to reuse subset job with a different provider contract: "
                f"job={job_name} saved={saved_provider} "
                f"expected={expected_provider}"
            )
        if contract.get("agent_parameters") != expected_execution[
            "agent_parameters"
        ]:
            raise SystemExit(
                f"Refusing to reuse subset job with different agent parameters: {job_name}"
            )
        saved_dataset = contract.get("dataset") or {}
        if (
            saved_dataset.get("tree_sha256")
            != expected_execution["dataset_tree_sha256"]
        ):
            raise SystemExit(
                f"Refusing to reuse subset job with a different dataset tree: {job_name}"
            )
        saved_dependencies = contract.get("dependency_versions") or {}
        dependency_name = (
            "claude-agent-sdk"
            if args.harness == "claude-code"
            else "pi-coding-agent"
        )
        expected_dependency = expected_execution["dependency_versions"][
            dependency_name
        ]
        if saved_dependencies.get(dependency_name) != expected_dependency:
            raise SystemExit(
                "Refusing to reuse subset job with a different agent dependency: "
                f"job={job_name} saved={saved_dependencies.get(dependency_name)} "
                f"expected={expected_dependency}"
            )
        if contract.get("runtime_knobs") != expected_execution["runtime_knobs"]:
            raise SystemExit(
                f"Refusing to reuse subset job with different runtime knobs: {job_name}"
            )
        expected_code = expected_execution["code_sha256"]
        saved_code = contract.get("code_sha256") or {}
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
            saved_code.get(path) != expected_code.get(path)
            for path in contract_code_paths
        ):
            raise SystemExit(
                f"Refusing to reuse subset job with different benchmark code: {job_name}"
            )
    if job_is_complete(job_dir, expected_trials):
        return job_dir
    if job_dir.exists() and job_is_running(job_dir):
        print(f"[tts-subset-loop] waiting for active job {job_name}", flush=True)
        last_progress: tuple[int, int | None, str | None] | None = None
        while job_is_running(job_dir):
            if job_is_complete(job_dir, expected_trials):
                return job_dir
            progress = job_progress(job_dir)
            if progress != last_progress:
                current, total, finished_at = progress
                total_text = total if total is not None else expected_trials
                print(
                    f"[tts-subset-loop] {job_name}: {current}/{total_text} finished_at={finished_at}",
                    flush=True,
                )
                last_progress = progress
            time.sleep(args.poll_sec)
        if job_is_complete(job_dir, expected_trials):
            return job_dir
        current, total, _ = job_progress(job_dir)
        print(
            f"[tts-subset-loop] active process ended incomplete; resuming "
            f"{job_name} from {current}/{total or expected_trials}",
            flush=True,
        )
    elif job_dir.exists():
        current, total, _ = job_progress(job_dir)
        print(
            f"[tts-subset-loop] resuming inactive job {job_name} from "
            f"{current}/{total or expected_trials}",
            flush=True,
        )

    env = os.environ.copy()
    env.update(load_env_file(args.env_file))
    env.update(
        {
            "LLM_PROVIDER": args.provider,
            "OPENAI_COMPAT_API": env.get("OPENAI_COMPAT_API", "openai-completions"),
            "OPENAI_COMPAT_REASONING_EFFORT": "none",
            "OPENAI_COMPAT_ENABLE_THINKING": "false",
            "NOVITA_REASONING_EFFORT": "none",
            "NOVITA_ENABLE_THINKING": "false",
            "MACARON_REASONING_EFFORT": "none",
            "MACARON_ENABLE_THINKING": "false",
            "SGLANG_REASONING_EFFORT": "none",
            "SGLANG_ENABLE_THINKING": "false",
            "CLAUDE_CODE_ATTRIBUTION_HEADER": "0",
            "FORCE_DISABLE_THINKING": "1",
            "PI_THINKING": "off",
            "E2B_CONCURRENCY": str(args.concurrency),
            "PI_SKILL_PACK_ROOT": str(gate_root),
            "PI_USE_SKILL_HARNESS_MEMORY": "false",
            "CLAUDE_USE_SKILL_HARNESS_MEMORY": "false",
            "PI_SKILL_RETRIEVAL_SCOPE": "transfer",
        }
    )
    command = [
        args.python,
        "scripts/run_benchmark.py",
        "--dataset",
        args.dataset,
        "--benchmark-name",
        args.benchmark_name,
        "--provider",
        args.provider,
        "--harness",
        args.harness,
        "--job-name",
        job_name,
        "--resume-existing",
        "--task-names-file",
        str(task_file),
        "--concurrency",
        str(args.concurrency),
        "--agent-timeout-sec",
        str(args.agent_timeout_sec),
        "--agent-setup-timeout-sec",
        str(args.agent_setup_timeout_sec),
        "--e2b-sandbox-timeout-sec",
        str(args.e2b_sandbox_timeout_sec),
        "--use-skills",
    ]
    if args.benchmark_name == "deepswe":
        command.extend(
            [
                "--override-cpus",
                "2",
                "--override-memory-mb",
                "8192",
                "--override-storage-mb",
                "20480",
                "--max-retries",
                "3",
                "--force-agent-internet",
            ]
        )
    if args.provider_base_url:
        command.extend(["--provider-base-url", args.provider_base_url])
    if args.provider_anthropic_base_url:
        command.extend(["--provider-anthropic-base-url", args.provider_anthropic_base_url])
    if args.provider_model:
        command.extend(["--provider-model", args.provider_model])
    if args.provider_api:
        command.extend(["--provider-api", args.provider_api])
    if args.claude_max_turns is not None:
        command.extend(["--claude-max-turns", str(args.claude_max_turns)])
    if args.claude_max_budget_usd is not None:
        command.extend(["--claude-max-budget-usd", str(args.claude_max_budget_usd)])
    execution = subset_execution_payload(args, gate_root)
    if args.harness == "claude-code":
        command.extend(
            [
                "--claude-sdk-version",
                execution["dependency_versions"]["claude-agent-sdk"],
            ]
        )
    else:
        command.extend(
            ["--pi-version", execution["dependency_versions"]["pi-coding-agent"]]
        )
    log_path.parent.mkdir(parents=True, exist_ok=True)
    for recovery_round in range(1, args.recovery_rounds + 1):
        with log_path.open("a") as log:
            log.write(
                f"[{utc_now()}] start gate_{gate_index:03d} subset eval "
                f"from gate_{previous_gate_index:03d} "
                f"recovery_round={recovery_round}/{args.recovery_rounds}\n"
            )
            log.flush()
            proc = subprocess.run(
                command,
                cwd=ROOT,
                env=env,
                stdout=log,
                stderr=subprocess.STDOUT,
                check=False,
            )
            log.write(f"[{utc_now()}] command exit code={proc.returncode}\n")
        if job_is_complete(job_dir, expected_trials):
            return job_dir
    current, total, finished_at = job_progress(job_dir)
    raise SystemExit(
        "Subset job did not complete after recovery rounds: "
        f"{job_dir / 'result.json'} current={current} total={total} "
        f"expected={expected_trials} finished_at={finished_at}"
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run SWE-bench TTS subset-composition evolution from the current best gate up to a max gate."
    )
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--start-gate", type=int, default=2)
    parser.add_argument("--max-gate", type=int, default=8)
    parser.add_argument("--dataset", default="swe-bench/swe-bench-verified@2")
    parser.add_argument("--benchmark-name", default="swe-bench")
    parser.add_argument("--harness", choices=["pi", "claude-code"], default="pi")
    parser.add_argument("--provider", default="openai")
    parser.add_argument("--provider-base-url", default=None)
    parser.add_argument("--provider-anthropic-base-url", default=None)
    parser.add_argument("--provider-model", default=None)
    parser.add_argument("--provider-api", default=None)
    parser.add_argument("--claude-max-turns", type=int, default=None)
    parser.add_argument("--claude-max-budget-usd", type=float, default=None)
    parser.add_argument("--claude-sdk-version", default=None)
    parser.add_argument("--pi-version", default=None)
    parser.add_argument("--tts-root", type=Path, default=ROOT / "run_logs" / "swebench_verified_tts_evo")
    parser.add_argument("--skill-output-root", type=Path, default=ROOT / "skills" / "test_time")
    parser.add_argument("--policy-state", type=Path, default=DEFAULT_POLICY_STATE)
    parser.add_argument("--env-file", type=Path, default=ROOT / ".env")
    parser.add_argument("--python", default=DEFAULT_PYTHON)
    parser.add_argument("--reward-threshold", type=float, default=1.0)
    parser.add_argument("--repo-update-batch-size", type=int, default=5)
    parser.add_argument("--repo-min-support", type=int, default=2)
    parser.add_argument("--repo-min-positive-support", type=int, default=0)
    parser.add_argument("--failure-mode-min-repo-support", type=int, default=2)
    parser.add_argument("--max-repo-skills-per-gate", type=int, default=12)
    parser.add_argument("--max-failure-skills-per-gate", type=int, default=12)
    parser.add_argument("--concurrency", type=int, default=15)
    parser.add_argument("--agent-timeout-sec", type=float, default=3600)
    parser.add_argument("--agent-setup-timeout-sec", type=float, default=1200)
    parser.add_argument("--e2b-sandbox-timeout-sec", type=int, default=7200)
    parser.add_argument("--poll-sec", type=int, default=60)
    parser.add_argument("--recovery-rounds", type=int, default=4)
    return parser.parse_args()


def run_subset_loop(args: argparse.Namespace) -> None:
    if args.start_gate != 2:
        raise SystemExit(
            "Subset evolution recovery must replay from gate 2 so prior gate state "
            "and cumulative successes are reconstructed exactly."
        )
    if args.benchmark_name == "deepswe" and args.max_gate > 4:
        raise SystemExit("DeepSWE evolution is capped at gate 4 for this protocol.")
    args.tts_root = args.tts_root.expanduser().resolve()
    args.skill_output_root = args.skill_output_root.expanduser().resolve()
    args.policy_state = args.policy_state.expanduser().resolve()
    args.env_file = args.env_file.expanduser().resolve()
    args.python = str(Path(args.python).expanduser()) if "/" in args.python else args.python
    run_dir = args.tts_root / args.run_id
    manifest_path = run_dir / "manifest.json"
    manifest = read_json(manifest_path)
    evaluator_policy = load_evaluator_policy(args.policy_state)

    gate1 = gate_row(manifest, 1)
    gate1_report = gate1.get("verifier_report") or {}
    gate1_aggregate = Path(gate1_report["aggregate_path"]).expanduser().resolve()
    base_report = read_json(gate1_aggregate)
    validate_source_aggregate(
        base_report,
        gate1_aggregate,
        expected_benchmark_name=args.benchmark_name,
    )
    all_tasks = all_report_tasks(base_report)
    success_tasks = successful_tasks(base_report)
    unresolved_tasks = sorted(all_tasks - success_tasks)
    previous_report_path = gate1_aggregate
    previous_gate_index = 1

    state_path = run_dir / "subset_eval" / "loop_state.json"
    state_path.parent.mkdir(parents=True, exist_ok=True)
    for gate_index in range(args.start_gate, args.max_gate + 1):
        if not unresolved_tasks:
            print(f"[tts-subset-loop] no unresolved tasks before gate_{gate_index:03d}; stop", flush=True)
            break
        previous_gate = gate_row(manifest, previous_gate_index)
        previous_gate_root = Path(previous_gate["skill_root"]).expanduser().resolve()
        gate_manifest = materialize_next_gate(
            args=args,
            run_dir=run_dir,
            manifest=manifest,
            gate_index=gate_index,
            previous_gate_index=previous_gate_index,
            previous_gate_root=previous_gate_root,
            source_report_path=previous_report_path,
            evaluator_policy=evaluator_policy,
        )
        gate_root = Path(gate_manifest["output_root"]).expanduser().resolve()
        subset_tasks = set(unresolved_tasks)
        dataset_task_filters = dataset_filter_task_names(args.dataset, subset_tasks)
        task_file = run_dir / "subsets" / f"gate{previous_gate_index:03d}_unresolved_tasks.txt"
        write_text(task_file, "\n".join(sorted(dataset_task_filters)) + "\n")
        job_name = subset_job_name(
            args.run_id,
            gate_index,
            previous_gate_index,
            subset_tasks,
            subset_execution_identity(args, gate_root),
        )
        log_path = run_dir / "subset_eval" / f"gate{gate_index:03d}_on_gate{previous_gate_index:03d}_unresolved.log"
        job_dir = run_subset_eval(
            args=args,
            gate_index=gate_index,
            previous_gate_index=previous_gate_index,
            gate_root=gate_root,
            task_file=task_file,
            expected_trials=len(subset_tasks),
            job_name=job_name,
            log_path=log_path,
        )
        previous_success_count = len(success_tasks)
        summary = summarize_job(job_dir)
        recovered = successful_tasks({"evaluation": summary})
        success_tasks.update(recovered)
        unresolved_tasks = sorted(subset_tasks - recovered)
        subset_report_path = run_dir / "subset_eval" / f"gate{gate_index:03d}_subset_report.json"
        report = write_subset_report(
            path=subset_report_path,
            run_id=args.run_id,
            gate_index=gate_index,
            previous_gate_index=previous_gate_index,
            job_name=job_name,
            job_dir=job_dir,
            task_file=task_file,
            expected_tasks=subset_tasks,
            previous_success_count=previous_success_count,
            cumulative_success_count=len(success_tasks),
            benchmark_name=args.benchmark_name,
        )
        evaluation = report.get("evaluation") or {}
        verifier_report = {
            "status": "available_subset",
            "eval_run_id": job_name,
            "aggregate_path": str(subset_report_path),
            "complete": report.get("complete"),
            "n_trials": evaluation.get("n_trials"),
            "n_errors": evaluation.get("n_errors"),
            "resolved": len(recovered),
            "cumulative_resolved": len(success_tasks),
            "remaining_unresolved": len(unresolved_tasks),
            "mean_reward": evaluation.get("mean_reward"),
        }
        gate = gate_row(manifest, gate_index)
        gate["verifier_report"] = verifier_report
        run_gate_manifest_path = (
            run_dir / "gates" / f"gate_{gate_index:03d}" / "manifest.json"
        )
        run_gate_manifest = read_json(run_gate_manifest_path)
        run_gate_manifest["verifier_report"] = verifier_report
        write_json(run_gate_manifest_path, run_gate_manifest)
        write_json(manifest_path, manifest)
        (run_dir / "report.md").write_text(render_report_md(manifest))
        loop_state = {
            "updated_at": utc_now(),
            "run_id": args.run_id,
            "max_gate": args.max_gate,
            "latest_gate": gate_index,
            "combined_resolved": len(success_tasks),
            "remaining_unresolved": len(unresolved_tasks),
            "latest_subset_report": str(subset_report_path),
            "latest_job_name": job_name,
            "latest_subset_recovered": len(recovered),
        }
        write_json(state_path, loop_state)
        print(
            "[tts-subset-loop] gate_{gate:03d}: recovered={recovered} combined={combined}/{total} remaining={remaining}".format(
                gate=gate_index,
                recovered=len(recovered),
                combined=len(success_tasks),
                total=len(all_tasks),
                remaining=len(unresolved_tasks),
            ),
            flush=True,
        )
        previous_report_path = subset_report_path
        previous_gate_index = gate_index
        manifest = read_json(manifest_path)


def main() -> None:
    args = parse_args()
    lock_dir = (
        args.tts_root.expanduser().resolve()
        / ".evolution-locks"
        / safe_slug(args.run_id, limit=150)
    )
    with exclusive_job_run(lock_dir):
        run_subset_loop(args)


if __name__ == "__main__":
    main()
