from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from scripts.materialize_deepswe_tts_evolution_gates import sha256_tree
from scripts.run_benchmark import _sha256_tree
from scripts.run_deepswe_setting_bon import (
    combine_task_rows,
    normalized_job_config,
    retry_task_names,
)
from scripts.run_swebench_tts_subset_evo_loop import subset_execution_payload


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


def test_combine_task_rows_rejects_missing_or_extra_retry_tasks() -> None:
    first = [row("passed", 1, "p1"), row("failed", 0, "f1")]

    with pytest.raises(ValueError, match="task set differs"):
        combine_task_rows(first, [])

    with pytest.raises(ValueError, match="task set differs"):
        combine_task_rows(first, [row("failed", 0, "f2"), row("passed", 1, "p2")])


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
