from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

import scripts.run_deepswe_reward0_rounds as reward0_runner
from scripts.run_deepswe_reward0_rounds import (
    apply_source_runtime_knobs,
    normalized_config_allowing_code,
    sampling_job_name,
    strict_binary_rows,
    strict_reward_zero_names,
    validate_transition,
    validate_raw_report,
    write_json_once,
    write_text_once,
)


def report(*rows: tuple[str, object]) -> dict[str, object]:
    return {
        "tasks": [
            {
                "task_name": name,
                "reward": reward,
                "trial_name": f"trial-{name}",
                "result_path": f"/{name}/result.json",
                "exception_type": None,
            }
            for name, reward in rows
        ]
    }


def test_strict_reward_zero_accepts_only_numeric_binary() -> None:
    value = report(("zero", 0), ("float-zero", 0.0), ("one", 1))
    assert strict_reward_zero_names(value, label="fixture") == {
        "zero",
        "float-zero",
    }


@pytest.mark.parametrize("reward", [True, False, "0", "1", None, -1, 0.5, 2])
def test_strict_binary_rows_rejects_invalid_rewards(reward: object) -> None:
    with pytest.raises(ValueError, match="numeric binary"):
        strict_binary_rows(report(("bad", reward)), label="fixture")


def test_transition_must_equal_predecessor_reward_zero() -> None:
    parent = report(("passed", 1), ("retry-a", 0), ("retry-b", 0))
    child = report(("retry-a", 1), ("retry-b", 0))
    assert validate_transition(
        parent, child, parent_label="parent", child_label="child"
    ) == {"retry-a", "retry-b"}

    with pytest.raises(ValueError, match="not exactly"):
        validate_transition(
            parent,
            report(("retry-a", 1)),
            parent_label="parent",
            child_label="child",
        )
    with pytest.raises(ValueError, match="not exactly"):
        validate_transition(
            parent,
            report(("retry-a", 1), ("retry-b", 0), ("passed", 1)),
            parent_label="parent",
            child_label="child",
        )


def test_raw_report_rejects_errors_and_exceptions(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(reward0_runner, "validate_source_report", lambda *_args: None)
    with pytest.raises(ValueError, match="evaluation errors"):
        validate_raw_report(
            {**report(("bad", 0)), "evaluation": {"n_errors": 1}},
            tmp_path / "report.json",
            label="fixture",
        )
    exceptional = report(("bad", 0))
    exceptional["evaluation"] = {"n_errors": 0}
    exceptional["tasks"][0]["exception_type"] = "AgentError"
    with pytest.raises(ValueError, match="task exceptions"):
        validate_raw_report(
            exceptional,
            tmp_path / "report.json",
            label="fixture",
        )


def test_sampling_job_name_preserves_unique_suffix_after_truncation() -> None:
    parent = "p" * 150
    first = sampling_job_name(parent, "gate_003", 2, "a" * 64)
    second = sampling_job_name(first, "gate_003", 3, "b" * 64)
    assert len(first) <= 150
    assert len(second) <= 150
    assert first.endswith("_gate_003_s2_r0_aaaaaaaaaaaa")
    assert second.endswith("_gate_003_s3_r0_bbbbbbbbbbbb")
    assert first != second


def test_normalized_config_only_masks_explicit_code_path() -> None:
    def config(job: str, tasks: list[str], code_hash: str) -> dict[str, object]:
        return {
            "job_name": job,
            "retry": {"include_exceptions": ["B", "A"]},
            "datasets": [{"task_names": tasks}],
            "agents": [
                {
                    "kwargs": {
                        "resume_contract": {
                            "dataset": {"task_names": tasks},
                            "code_sha256": {
                                "scripts/run_benchmark.py": code_hash,
                                "agents/pi_agent.py": "same",
                            },
                        }
                    }
                }
            ],
        }

    left = normalized_config_allowing_code(
        config("one", ["a"], "old"), frozenset({"scripts/run_benchmark.py"})
    )
    right = normalized_config_allowing_code(
        config("two", ["b"], "new"), frozenset({"scripts/run_benchmark.py"})
    )
    assert left == right
    changed = config("two", ["b"], "new")
    changed["agents"][0]["kwargs"]["resume_contract"]["code_sha256"][
        "agents/pi_agent.py"
    ] = "different"
    assert left != normalized_config_allowing_code(
        changed, frozenset({"scripts/run_benchmark.py"})
    )


def test_write_once_refuses_changed_artifacts(tmp_path: Path) -> None:
    text_path = tmp_path / "tasks.txt"
    write_text_once(text_path, "a\n")
    write_text_once(text_path, "a\n")
    with pytest.raises(ValueError, match="immutable"):
        write_text_once(text_path, "b\n")

    json_path = tmp_path / "manifest.json"
    write_json_once(json_path, {"a": 1})
    assert json.loads(json_path.read_text()) == {"a": 1}
    write_json_once(json_path, {"a": 1})
    with pytest.raises(ValueError, match="immutable"):
        write_json_once(json_path, {"a": 2})


def test_apply_source_runtime_knobs_sets_empty_and_unsets(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    job = tmp_path / "job"
    job.mkdir()
    contract = {
        "runtime_knobs": {"PIN_SET": "", "PIN_UNSET": None},
    }
    (job / "config.json").write_text(
        json.dumps(
            {"agents": [{"kwargs": {"resume_contract": contract}}]}
        ),
        encoding="utf-8",
    )
    report_path = tmp_path / "frozen.json"
    report_path.write_text(
        json.dumps({"evaluation": {"job_dir": str(job)}}), encoding="utf-8"
    )
    launcher = tmp_path / "launcher"
    launcher.mkdir()
    (launcher / "state.json").write_text(
        json.dumps({"frozen_report": str(report_path)}), encoding="utf-8"
    )
    monkeypatch.setenv("PIN_SET", "wrong")
    monkeypatch.setenv("PIN_UNSET", "wrong")

    pinned = apply_source_runtime_knobs(type("Args", (), {"launcher_dir": launcher})())

    assert pinned == {"PIN_SET": "", "PIN_UNSET": None}
    assert os.environ["PIN_SET"] == ""
    assert "PIN_UNSET" not in os.environ
