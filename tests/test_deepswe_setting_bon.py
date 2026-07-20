from __future__ import annotations

import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from scripts.materialize_deepswe_tts_evolution_gates import (
    TASKWISE_OR_SAMPLING_POLICY,
    sha256_file,
    sha256_tree,
    validate_source_aggregate,
)
from scripts.run_benchmark import DEEPSWE_ARTIFACT_HOOK_VERSION, _sha256_tree
from scripts.run_deepswe_setting_bon import (
    Setting,
    build_merged_score_report,
    combine_task_rows,
    merged_task_rows,
    normalized_job_config,
    parse_args,
    retry_task_names,
    validate_requested_execution,
)
from scripts.run_swebench_tts_subset_evo_loop import (
    subset_execution_payload,
    successful_tasks,
)


def row(name: str, reward: object, trial: str) -> dict[str, object]:
    return {
        "task_name": f"datacurve/{name}",
        "trial_name": trial,
        "reward": reward,
        "exception_type": None,
        "result_path": f"/{trial}/result.json",
    }


def test_retry_task_names_uses_exact_reward_not_equal_to_one() -> None:
    report = {
        "tasks": [
            row("passed", 1, "p1"),
            row("failed", 0, "f1"),
        ]
    }

    assert retry_task_names(report) == {"datacurve/failed"}


@pytest.mark.parametrize("invalid_reward", [None, 0.5, -1, True, "bad"])
def test_retry_task_names_rejects_nonbinary_rewards(invalid_reward: object) -> None:
    with pytest.raises(ValueError, match="binary-reward"):
        retry_task_names({"tasks": [row("invalid", invalid_reward, "i1")]})


def test_combine_task_rows_keeps_first_pass_and_takes_best_retry() -> None:
    first = [
        row("already", 1, "a1"),
        row("recovered", 0, "r1"),
        row("still-failed", 0, "s1"),
    ]
    second = [
        row("recovered", 1, "r2"),
        row("still-failed", 0, "s2"),
    ]

    combined = {item["task_name"]: item for item in combine_task_rows(first, second)}

    assert combined["datacurve/already"]["sample_2"] is None
    assert combined["datacurve/already"]["best_reward"] == 1
    assert combined["datacurve/recovered"]["best_reward"] == 1
    assert combined["datacurve/recovered"]["sample_2"]["trial_name"] == "r2"
    assert combined["datacurve/still-failed"]["best_reward"] == 0


def test_merged_task_rows_selects_strict_improvement_and_prefers_first_on_tie() -> None:
    combined = combine_task_rows(
        [
            row("already", 1, "a1"),
            row("recovered", 0, "r1"),
            row("still-failed", 0, "s1"),
        ],
        [row("recovered", 1, "r2"), row("still-failed", 0, "s2")],
    )

    merged = {item["task_name"]: item for item in merged_task_rows(combined)}

    assert merged["datacurve/already"]["selected_sample"] == 1
    assert merged["datacurve/already"]["trial_name"] == "a1"
    assert merged["datacurve/recovered"]["selected_sample"] == 2
    assert merged["datacurve/recovered"]["trial_name"] == "r2"
    assert merged["datacurve/recovered"]["reward"] == 1
    assert merged["datacurve/still-failed"]["selected_sample"] == 1
    assert merged["datacurve/still-failed"]["trial_name"] == "s1"
    assert (
        merged["datacurve/still-failed"]["attempts"]["sample_2"]["trial_name"] == "s2"
    )


def test_combine_task_rows_rejects_missing_or_extra_retry_tasks() -> None:
    first = [row("passed", 1, "p1"), row("failed", 0, "f1")]

    with pytest.raises(ValueError, match="task set differs"):
        combine_task_rows(first, [])

    with pytest.raises(ValueError, match="task set differs"):
        combine_task_rows(first, [row("failed", 0, "f2"), row("passed", 1, "p2")])


def test_combine_task_rows_fails_closed_on_duplicate_or_invalid_attempt() -> None:
    duplicate = row("same", 0, "s1")
    with pytest.raises(ValueError, match="duplicate task"):
        combine_task_rows([duplicate, duplicate], [row("same", 0, "s2")])

    invalid = row("bad", None, "bad2")
    with pytest.raises(ValueError, match="non-binary reward"):
        combine_task_rows([row("bad", 0, "bad1")], [invalid])


def aggregate_report(
    *,
    rows: list[dict[str, object]],
    run_id: str,
    job_dir: str,
    resume_contract: dict[str, object],
) -> dict[str, object]:
    resolved = sum(item["reward"] == 1 for item in rows)
    return {
        "schema_version": 1,
        "run_id": run_id,
        "complete": True,
        "benchmark_name": "deepswe",
        "provenance": {
            "benchmark_name": "deepswe",
            "job_config_sha256": hashlib.sha256(
                (Path(job_dir) / "config.json").read_bytes()
            ).hexdigest(),
            "task_set_sha256": f"{run_id}-tasks",
            "resume_contract": resume_contract,
        },
        "infra_invalid_trials": [],
        "evaluation": {
            "job_dir": job_dir,
            "job_name": run_id,
            "n_trials": len(rows),
            "n_errors": 0,
            "resolved": resolved,
            "mean_reward": resolved / len(rows),
            "tasks": rows,
        },
        "tasks": rows,
        "completeness": {
            "expected_trials": len(rows),
            "trial_result_files": len(rows),
            "infra_invalid_trials": 0,
        },
    }


def write_job_config(
    job_dir: Path,
    *,
    job_name: str,
    resume_contract: dict[str, object],
) -> None:
    (job_dir / "config.json").write_text(
        json.dumps(
            {
                "job_name": job_name,
                "retry": {"include_exceptions": [], "exclude_exceptions": []},
                "datasets": [{"task_names": ["fixture-task"]}],
                "agents": [{"kwargs": {"resume_contract": resume_contract}}],
            }
        ),
        encoding="utf-8",
    )


def test_merged_score_report_is_consumable_and_preserves_lineage(tmp_path) -> None:
    source_job = tmp_path / "source-job"
    second_job = tmp_path / "second-job"
    skill_root = tmp_path / "skills"
    source_job.mkdir()
    second_job.mkdir()
    skill_root.mkdir()
    skill_hash = sha256_tree(skill_root)
    contract = {
        "version": 1,
        "skills": {"roots": [str(skill_root)], "tree_sha256": [skill_hash]},
    }
    write_job_config(source_job, job_name="source-job", resume_contract=contract)
    write_job_config(second_job, job_name="second-job", resume_contract=contract)
    first_rows = [
        row("already", 1, "a1"),
        row("recovered", 0, "r1"),
        row("still-failed", 0, "s1"),
    ]
    second_rows = [row("recovered", 1, "r2"), row("still-failed", 0, "s2")]
    source_report = aggregate_report(
        rows=first_rows,
        run_id="source-run",
        job_dir=str(source_job),
        resume_contract=contract,
    )
    sample_2_report = aggregate_report(
        rows=second_rows,
        run_id="sample-2-run",
        job_dir=str(second_job),
        resume_contract=contract,
    )
    source_path = tmp_path / "source.json"
    second_path = tmp_path / "sample-2.json"
    source_path.write_text(json.dumps(source_report), encoding="utf-8")
    second_path.write_text(json.dumps(sample_2_report), encoding="utf-8")
    setting = Setting("frozen", 0, source_path, skill_root)
    best_of_2 = {
        "kind": "deepswe_same_setting_best_of_2",
        "complete": True,
        "setting": "frozen",
        "sampling_policy": dict(TASKWISE_OR_SAMPLING_POLICY),
        "tasks": combine_task_rows(first_rows, second_rows),
    }

    merged = build_merged_score_report(
        setting=setting,
        source_report=source_report,
        skill_tree_sha256=skill_hash,
        sample_2_report=sample_2_report,
        sample_2_report_path=second_path,
        best_of_2_report=best_of_2,
    )

    validate_source_aggregate(
        merged,
        tmp_path / "merged_score_report.json",
        expected_benchmark_name="deepswe",
        expected_skill_root=skill_root,
    )
    assert successful_tasks(merged) == {
        "datacurve/already",
        "datacurve/recovered",
    }
    assert retry_task_names(merged) == {"datacurve/still-failed"}
    assert merged["evaluation"]["resolved"] == 2
    assert merged["completeness"]["expected_trials"] == 3
    assert merged["provenance"]["resume_contract"] == contract
    assert [source["run_id"] for source in merged["lineage"]["sources"]] == [
        "source-run",
        "sample-2-run",
    ]
    assert all(source["report_sha256"] for source in merged["lineage"]["sources"])

    other_skill_root = tmp_path / "other-skills"
    other_skill_root.mkdir()
    with pytest.raises(SystemExit, match="requested base skill root"):
        validate_source_aggregate(
            merged,
            tmp_path / "wrong-skill-root.json",
            expected_benchmark_name="deepswe",
            expected_skill_root=other_skill_root,
        )

    missing_top_level = json.loads(json.dumps(merged))
    del missing_top_level["tasks"]
    with pytest.raises(SystemExit, match="top-level tasks"):
        validate_source_aggregate(
            missing_top_level,
            tmp_path / "missing-top-level.json",
            expected_benchmark_name="deepswe",
        )

    empty_top_level = json.loads(json.dumps(merged))
    empty_top_level["tasks"] = []
    with pytest.raises(SystemExit, match="top-level tasks"):
        validate_source_aggregate(
            empty_top_level,
            tmp_path / "empty-top-level.json",
            expected_benchmark_name="deepswe",
        )

    reordered = json.loads(json.dumps(merged))
    reordered["tasks"].reverse()
    reordered["evaluation"]["tasks"].reverse()
    with pytest.raises(SystemExit, match="canonical task-name order"):
        validate_source_aggregate(
            reordered,
            tmp_path / "reordered.json",
            expected_benchmark_name="deepswe",
        )

    bad_config = json.loads((source_job / "config.json").read_text())
    bad_config["agents"] = []
    (source_job / "config.json").write_text(json.dumps(bad_config))
    changed_source = json.loads(json.dumps(source_report))
    changed_source["provenance"]["job_config_sha256"] = hashlib.sha256(
        (source_job / "config.json").read_bytes()
    ).hexdigest()
    source_path.write_text(json.dumps(changed_source))
    unanchored_contract = json.loads(json.dumps(merged))
    unanchored_contract["lineage"]["sources"][0]["report_sha256"] = sha256_file(
        source_path
    )
    unanchored_contract["provenance"]["lineage_sha256"] = hashlib.sha256(
        json.dumps(
            unanchored_contract["lineage"],
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    with pytest.raises(SystemExit, match="has no agents"):
        validate_source_aggregate(
            unanchored_contract,
            tmp_path / "unanchored-contract.json",
            expected_benchmark_name="deepswe",
        )


def test_merged_score_report_requires_source_run_ids(tmp_path) -> None:
    job_dir = tmp_path / "job"
    skill_root = tmp_path / "skills"
    job_dir.mkdir()
    skill_root.mkdir()
    contract = {"version": 1}
    skill_hash = sha256_tree(skill_root)
    contract["skills"] = {
        "roots": [str(skill_root)],
        "tree_sha256": [skill_hash],
    }
    write_job_config(job_dir, job_name="job", resume_contract=contract)
    source = aggregate_report(
        rows=[row("failed", 0, "f1")],
        run_id="source-run",
        job_dir=str(job_dir),
        resume_contract=contract,
    )
    second = aggregate_report(
        rows=[row("failed", 0, "f2")],
        run_id="second-run",
        job_dir=str(job_dir),
        resume_contract=contract,
    )
    del second["run_id"]
    source_path = tmp_path / "source.json"
    second_path = tmp_path / "second.json"
    source_path.write_text(json.dumps(source), encoding="utf-8")
    second_path.write_text(json.dumps(second), encoding="utf-8")

    with pytest.raises(ValueError, match="Sample 2 report has no run_id"):
        build_merged_score_report(
            setting=Setting("frozen", 0, source_path, skill_root),
            source_report=source,
            skill_tree_sha256=skill_hash,
            sample_2_report=second,
            sample_2_report_path=second_path,
            best_of_2_report={
                "kind": "deepswe_same_setting_best_of_2",
                "complete": True,
                "setting": "frozen",
                "tasks": combine_task_rows(source["tasks"], second["tasks"]),
            },
        )


def test_merged_score_report_rejects_tampered_best_of_2_rows(tmp_path) -> None:
    job_dir = tmp_path / "job"
    skill_root = tmp_path / "skills"
    job_dir.mkdir()
    skill_root.mkdir()
    contract = {"version": 1}
    skill_hash = sha256_tree(skill_root)
    contract["skills"] = {
        "roots": [str(skill_root)],
        "tree_sha256": [skill_hash],
    }
    write_job_config(job_dir, job_name="job", resume_contract=contract)
    source = aggregate_report(
        rows=[row("failed", 0, "f1")],
        run_id="source-run",
        job_dir=str(job_dir),
        resume_contract=contract,
    )
    second = aggregate_report(
        rows=[row("failed", 0, "f2")],
        run_id="second-run",
        job_dir=str(job_dir),
        resume_contract=contract,
    )
    source_path = tmp_path / "source.json"
    second_path = tmp_path / "second.json"
    source_path.write_text(json.dumps(source), encoding="utf-8")
    second_path.write_text(json.dumps(second), encoding="utf-8")
    combined = combine_task_rows(source["tasks"], second["tasks"])
    combined[0]["best_reward"] = 1

    with pytest.raises(ValueError, match="differ from their source reports"):
        build_merged_score_report(
            setting=Setting("frozen", 0, source_path, skill_root),
            source_report=source,
            skill_tree_sha256=skill_hash,
            sample_2_report=second,
            sample_2_report_path=second_path,
            best_of_2_report={
                "kind": "deepswe_same_setting_best_of_2",
                "complete": True,
                "setting": "frozen",
                "tasks": combined,
            },
        )


def test_normalized_job_config_ignores_only_job_and_task_identity() -> None:
    source = {
        "job_name": "first",
        "n_concurrent_trials": 8,
        "retry": {
            "include_exceptions": ["B", "A"],
            "exclude_exceptions": ["D", "C"],
        },
        "datasets": [{"path": "/dataset", "task_names": None}],
        "agents": [
            {
                "kwargs": {
                    "resume_contract": {
                        "dataset": {"path": "/dataset", "task_names": []}
                    }
                }
            }
        ],
    }
    retry = json.loads(json.dumps(source))
    retry["job_name"] = "second"
    retry["datasets"][0]["task_names"] = ["failed-task"]
    retry["agents"][0]["kwargs"]["resume_contract"]["dataset"]["task_names"] = [
        "datacurve/failed-task"
    ]
    retry["retry"]["include_exceptions"].reverse()
    retry["retry"]["exclude_exceptions"].reverse()

    assert normalized_job_config(source) == normalized_job_config(retry)

    retry["n_concurrent_trials"] = 16
    assert normalized_job_config(source) != normalized_job_config(retry)


def test_subset_execution_uses_resume_contract_dataset_hash(tmp_path) -> None:
    dataset = tmp_path / "dataset"
    dataset.mkdir()
    (dataset / "task.txt").write_text("task", encoding="utf-8")
    external = tmp_path / "external.txt"
    external.write_text("linked", encoding="utf-8")
    (dataset / "linked.txt").symlink_to(external)
    gate_root = tmp_path / "skills"
    gate_root.mkdir()
    (gate_root / "SKILL.md").write_text("skill", encoding="utf-8")
    args = SimpleNamespace(
        provider="macaron",
        env_file=tmp_path / "missing.env",
        harness="claude-code",
        provider_model="glm-5.2",
        provider_anthropic_base_url="https://pi-api-cn.macaron.xin",
        provider_base_url=None,
        provider_api=None,
        claude_sdk_version="0.2.116",
        pi_version="0.80.6",
        dataset=str(dataset),
        benchmark_name="deepswe",
        claude_max_turns=None,
        claude_max_budget_usd=None,
        agent_timeout_sec=7200,
        agent_setup_timeout_sec=1200,
        e2b_sandbox_timeout_sec=14400,
    )

    payload = subset_execution_payload(args, gate_root)

    assert payload["dataset_tree_sha256"] == _sha256_tree(dataset)
    assert payload["dataset_tree_sha256"] != sha256_tree(dataset)


def test_requested_execution_requires_an_exact_code_change_allowlist(
    tmp_path,
    monkeypatch,
) -> None:
    job_dir = tmp_path / "source-job"
    skill_root = tmp_path / "skills"
    job_dir.mkdir()
    skill_root.mkdir()
    protected_paths = {
        "agents/claude_sdk_agent.py",
        "agents/pi_agent.py",
        "agents/skill_harness_memory.py",
        "environments/e2b_swebench.py",
        "providers/__init__.py",
        "providers/specs.py",
        "scripts/run_benchmark.py",
    }
    current_code = {path: "a" * 64 for path in protected_paths}
    source_code = dict(current_code)
    source_code["scripts/run_benchmark.py"] = "b" * 64
    provider = {
        "name": "macaron",
        "model": "glm-5.2",
        "endpoint": "https://example.invalid",
        "agent": "claude-code",
        "provider_api": None,
    }
    runtime_knobs = {"FORCE_DISABLE_THINKING": "1"}
    source_contract = {
        "artifact_hook_version": DEEPSWE_ARTIFACT_HOOK_VERSION,
        "artifact_hook_enabled": True,
        "force_agent_internet": True,
        "provider": provider,
        "agent_parameters": {"max_turns": None, "max_budget_usd": None},
        "runtime_knobs": runtime_knobs,
        "dataset": {"path": "/dataset", "tree_sha256": "dataset-hash"},
        "dependency_versions": {"claude-agent-sdk": "0.2.116"},
        "code_sha256": source_code,
    }
    source_config = {
        "n_concurrent_trials": 8,
        "agents": [
            {
                "override_timeout_sec": 7200,
                "override_setup_timeout_sec": 1200,
            }
        ],
        "environment": {
            "kwargs": {"sandbox_timeout_sec": 14400},
            "override_cpus": 2,
            "override_memory_mb": 8192,
            "override_storage_mb": 20480,
        },
    }
    (job_dir / "config.json").write_text(json.dumps(source_config))
    source_report = {
        "evaluation": {"job_dir": str(job_dir)},
        "provenance": {"resume_contract": source_contract},
    }
    requested = {
        "provider": dict(provider),
        "agent_parameters": {"max_turns": None, "max_budget_usd": None},
        "runtime_knobs": dict(runtime_knobs),
        "dataset": "/dataset",
        "dataset_tree_sha256": "dataset-hash",
        "dependency_versions": {"claude-agent-sdk": "0.2.116"},
        "code_sha256": current_code,
    }
    monkeypatch.setattr(
        "scripts.run_deepswe_setting_bon.subset_execution_payload",
        lambda _args, _skill_root: requested,
    )
    args = SimpleNamespace(
        harness="claude-code",
        concurrency=8,
        agent_timeout_sec=7200,
        agent_setup_timeout_sec=1200,
        e2b_sandbox_timeout_sec=14400,
    )

    with pytest.raises(ValueError, match="explicit allowlist"):
        validate_requested_execution(args, source_report, skill_root)

    audit = validate_requested_execution(
        args,
        source_report,
        skill_root,
        allowed_code_changes=frozenset({"scripts/run_benchmark.py"}),
    )
    assert audit["allowed_code_changes"] == ["scripts/run_benchmark.py"]
    assert audit["code_mismatches"] == {
        "scripts/run_benchmark.py": {
            "source_sha256": "b" * 64,
            "current_sha256": "a" * 64,
        }
    }

    source_code["agents/pi_agent.py"] = "c" * 64
    with pytest.raises(ValueError, match="explicit allowlist"):
        validate_requested_execution(
            args,
            source_report,
            skill_root,
            allowed_code_changes=frozenset({"scripts/run_benchmark.py"}),
        )
    source_code["agents/pi_agent.py"] = "a" * 64

    source_code["scripts/run_benchmark.py"] = "a" * 64
    with pytest.raises(ValueError, match="explicit allowlist"):
        validate_requested_execution(
            args,
            source_report,
            skill_root,
            allowed_code_changes=frozenset({"scripts/run_benchmark.py"}),
        )


def test_requested_execution_rejects_behavioral_setting_drift(
    tmp_path,
    monkeypatch,
) -> None:
    job_dir = tmp_path / "source-job"
    skill_root = tmp_path / "skills"
    job_dir.mkdir()
    skill_root.mkdir()
    protected_paths = {
        "agents/claude_sdk_agent.py",
        "agents/pi_agent.py",
        "agents/skill_harness_memory.py",
        "environments/e2b_swebench.py",
        "providers/__init__.py",
        "providers/specs.py",
        "scripts/run_benchmark.py",
    }
    code = {path: "a" * 64 for path in protected_paths}
    provider = {
        "name": "macaron",
        "model": "glm-5.2",
        "endpoint": "https://example.invalid",
        "agent": "claude-code",
        "provider_api": None,
    }
    contract = {
        "artifact_hook_version": DEEPSWE_ARTIFACT_HOOK_VERSION,
        "artifact_hook_enabled": True,
        "force_agent_internet": True,
        "provider": provider,
        "agent_parameters": {"max_turns": None, "max_budget_usd": None},
        "runtime_knobs": {"FORCE_DISABLE_THINKING": "1"},
        "dataset": {"path": "/dataset", "tree_sha256": "dataset-hash"},
        "dependency_versions": {"claude-agent-sdk": "0.2.116"},
        "code_sha256": code,
    }
    config = {
        "n_concurrent_trials": 8,
        "agents": [
            {
                "override_timeout_sec": 7200,
                "override_setup_timeout_sec": 1200,
            }
        ],
        "environment": {
            "kwargs": {"sandbox_timeout_sec": 14400},
            "override_cpus": 2,
            "override_memory_mb": 8192,
            "override_storage_mb": 20480,
        },
    }
    (job_dir / "config.json").write_text(json.dumps(config))
    report = {
        "evaluation": {"job_dir": str(job_dir)},
        "provenance": {"resume_contract": contract},
    }
    requested = {
        "provider": dict(provider),
        "agent_parameters": dict(contract["agent_parameters"]),
        "runtime_knobs": dict(contract["runtime_knobs"]),
        "dataset": "/dataset",
        "dataset_tree_sha256": "dataset-hash",
        "dependency_versions": {"claude-agent-sdk": "0.2.116"},
        "code_sha256": code,
    }
    monkeypatch.setattr(
        "scripts.run_deepswe_setting_bon.subset_execution_payload",
        lambda _args, _skill_root: requested,
    )
    args = SimpleNamespace(
        harness="claude-code",
        concurrency=8,
        agent_timeout_sec=7200,
        agent_setup_timeout_sec=1200,
        e2b_sandbox_timeout_sec=14400,
    )

    requested["provider"] = {**provider, "model": "other"}
    with pytest.raises(ValueError, match="provider/endpoint/model"):
        validate_requested_execution(args, report, skill_root)
    requested["provider"] = dict(provider)

    requested["runtime_knobs"] = {"FORCE_DISABLE_THINKING": "0"}
    with pytest.raises(ValueError, match="runtime knobs"):
        validate_requested_execution(args, report, skill_root)
    requested["runtime_knobs"] = dict(contract["runtime_knobs"])

    requested["dataset_tree_sha256"] = "other"
    with pytest.raises(ValueError, match="dataset differs"):
        validate_requested_execution(args, report, skill_root)
    requested["dataset_tree_sha256"] = "dataset-hash"

    requested["dependency_versions"] = {"claude-agent-sdk": "other"}
    with pytest.raises(ValueError, match="dependency"):
        validate_requested_execution(args, report, skill_root)
    requested["dependency_versions"] = {"claude-agent-sdk": "0.2.116"}

    args.concurrency = 9
    with pytest.raises(ValueError, match="concurrency"):
        validate_requested_execution(args, report, skill_root)


def test_allow_paused_source_is_explicit_cli_opt_in(monkeypatch) -> None:
    monkeypatch.setattr(
        "sys.argv",
        [
            "run_deepswe_setting_bon.py",
            "--launcher-dir",
            "/launcher",
            "--tts-run-dir",
            "/tts",
            "--dataset",
            "/dataset",
            "--env-file",
            "/env",
            "--no-wait",
            "--allow-paused-source",
        ],
    )

    args = parse_args()

    assert args.no_wait is True
    assert args.allow_paused_source is True
