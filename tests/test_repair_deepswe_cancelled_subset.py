from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from scripts.repair_deepswe_cancelled_subset import (
    RepairError,
    finalize_repair,
    orchestrate_repair,
    prepare_repair,
    sha256_file,
    sha256_tree,
)


def write_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2) + "\n")


def result_payload(
    *,
    job_dir: Path,
    dataset: Path,
    task: str,
    trial: str,
    reward: int | None,
    cancelled: bool = False,
) -> dict:
    logical_name = f"datacurve/{task}"
    exception = None
    metadata = {"completed": True, "termination": "result_message"}
    if cancelled:
        exception = {
            "exception_type": "CancelledError",
            "exception_message": "",
            "exception_traceback": "asyncio.exceptions.CancelledError",
        }
        metadata = {"completed": False, "termination": "interrupted_or_timeout"}
    return {
        "task_name": logical_name,
        "trial_name": trial,
        "task_id": {"path": str(dataset / task)},
        "config": {
            "trial_name": trial,
            "trials_dir": str(job_dir),
            "task": {"path": str(dataset / task)},
        },
        "verifier_result": (
            {"rewards": {"reward": reward}} if reward is not None else None
        ),
        "exception_info": exception,
        "agent_result": {"metadata": metadata},
        "started_at": "2026-07-17T00:00:00Z",
        "finished_at": "2026-07-17T00:01:00Z",
    }


def write_trial(
    *,
    job_dir: Path,
    dataset: Path,
    task: str,
    trial: str,
    reward: int | None,
    cancelled: bool = False,
) -> Path:
    trial_dir = job_dir / trial
    trial_dir.mkdir(parents=True)
    root_config = json.loads((job_dir / "config.json").read_text())
    resume_contract = root_config["agents"][0]["kwargs"]["resume_contract"]
    write_json(
        trial_dir / "config.json",
        {
            "task": {"path": str(dataset / task)},
            "agent": {"kwargs": {"resume_contract": resume_contract}},
            "environment": {
                "override_cpus": 2,
                "override_memory_mb": 8192,
                "override_storage_mb": 20480,
                "kwargs": {
                    "sandbox_timeout_sec": 14400,
                    "force_allow_internet": True,
                },
            },
        },
    )
    write_json(
        trial_dir / "result.json",
        result_payload(
            job_dir=job_dir,
            dataset=dataset,
            task=task,
            trial=trial,
            reward=reward,
            cancelled=cancelled,
        ),
    )
    return trial_dir


def build_fixture(root: Path) -> dict[str, Path]:
    dataset = root / "dataset"
    jobs_dir = root / "jobs"
    job_dir = jobs_dir / "gate3-job"
    tts_run_dir = root / "tts" / "run"
    gate_root = root / "skills" / "run" / "gate_003"
    gate_root.mkdir(parents=True)
    (gate_root / "SKILL.md").write_text("# gate 3\n")
    gate_tree_hash = sha256_tree(gate_root)
    tasks = ["valid-zero", "valid-one", "error-a", "error-b"]
    for task in tasks:
        (dataset / task).mkdir(parents=True)

    gate2_report = tts_run_dir / "subset_eval" / "gate002_subset_report.json"
    write_json(
        gate2_report,
        {
            "complete": True,
            "gate_index": 2,
            "tasks": [
                {"task_name": f"datacurve/{task}", "reward": 0} for task in tasks
            ],
            "composition": {
                "previous_success_count": 2,
                "subset_recovered_count": 0,
                "cumulative_success_count": 2,
                "remaining_unresolved_count": 4,
            },
        },
    )
    manifest = {
        "run_id": "tts-run",
        "source_run_id": "frozen",
        "summary": {
            "task_evidence": 4,
            "repo_candidates": 1,
            "failure_candidates": 1,
            "promoted_skills": 1,
        },
        "gates": [
            {"gate_index": 0, "skill_counts": {}},
            {"gate_index": 1, "skill_counts": {}},
            {
                "gate_index": 2,
                "skill_counts": {},
                "verifier_report": {
                    "status": "available_subset",
                    "aggregate_path": str(gate2_report),
                    "cumulative_resolved": 2,
                },
            },
            {
                "gate_index": 3,
                "source_previous_gate": 2,
                "source_aggregate": str(gate2_report),
                "skill_root": str(gate_root),
                "skill_counts": {"base": 1, "test_time_promoted": 1},
                "verifier_report": {"status": "subset_pending"},
            },
        ],
    }
    write_json(tts_run_dir / "manifest.json", manifest)
    write_json(
        tts_run_dir / "gates" / "gate_003" / "manifest.json",
        {
            "output_tree_sha256": gate_tree_hash,
            "verifier_report": {"status": "not_run"},
        },
    )
    write_json(
        tts_run_dir / "subset_eval" / "loop_state.json",
        {
            "run_id": "tts-run",
            "max_gate": 4,
            "latest_gate": 2,
            "combined_resolved": 2,
            "remaining_unresolved": 4,
        },
    )
    task_file = tts_run_dir / "subsets" / "gate002_unresolved_tasks.txt"
    task_file.parent.mkdir(parents=True)
    task_file.write_text("\n".join(tasks) + "\n")

    job_dir.mkdir(parents=True)
    resume_contract = {
        "dataset": {"task_names": tasks},
        "skills": {
            "enabled": True,
            "roots": [str(gate_root)],
            "tree_sha256": [gate_tree_hash],
        },
    }
    write_json(
        job_dir / "config.json",
        {
            "job_name": job_dir.name,
            "jobs_dir": str(jobs_dir),
            "n_attempts": 1,
            "datasets": [{"path": str(dataset), "task_names": tasks}],
            "agents": [
                {
                    "kwargs": {
                        "benchmark_name": "deepswe",
                        "use_skills": True,
                        "resume_contract": resume_contract,
                    }
                }
            ],
        },
    )
    write_json(
        job_dir / "result.json",
        {
            "n_total_trials": 4,
            "finished_at": "2026-07-17T01:00:00Z",
            "stats": {"n_trials": 4},
        },
    )
    write_trial(
        job_dir=job_dir,
        dataset=dataset,
        task="valid-zero",
        trial="valid-zero__old",
        reward=0,
    )
    write_trial(
        job_dir=job_dir,
        dataset=dataset,
        task="valid-one",
        trial="valid-one__old",
        reward=1,
    )
    write_trial(
        job_dir=job_dir,
        dataset=dataset,
        task="error-a",
        trial="error-a__old",
        reward=None,
        cancelled=True,
    )
    write_trial(
        job_dir=job_dir,
        dataset=dataset,
        task="error-b",
        trial="error-b__old",
        reward=None,
        cancelled=True,
    )
    allowlist = root / "errors.txt"
    allowlist.write_text("datacurve/error-a\ndatacurve/error-b\n")
    resume_log = root / "logs" / "gate3-job.log"
    resume_log.parent.mkdir(parents=True)
    resume_log.write_text("historical START trial=old task=datacurve/valid-zero\n")
    return {
        "dataset": dataset,
        "job_dir": job_dir,
        "tts_run_dir": tts_run_dir,
        "gate_root": gate_root,
        "allowlist": allowlist,
        "resume_log": resume_log,
        "repair_dir": root / "repair",
    }


def prepare(fixture: dict[str, Path]) -> dict:
    return prepare_repair(
        job_dir=fixture["job_dir"],
        tts_run_dir=fixture["tts_run_dir"],
        allowlist_file=fixture["allowlist"],
        repair_dir=fixture["repair_dir"],
        resume_log=fixture["resume_log"],
        expected_error_count=2,
        expected_valid_count=2,
        execute=True,
    )


def simulate_error_only_resume(
    fixture: dict[str, Path], *, extra_start: bool = False
) -> None:
    job_dir = fixture["job_dir"]
    dataset = fixture["dataset"]
    write_trial(
        job_dir=job_dir,
        dataset=dataset,
        task="error-a",
        trial="error-a__new",
        reward=1,
    )
    write_trial(
        job_dir=job_dir,
        dataset=dataset,
        task="error-b",
        trial="error-b__new",
        reward=0,
    )
    root_result = json.loads((job_dir / "result.json").read_text())
    root_result["finished_at"] = "2026-07-17T02:00:00Z"
    write_json(job_dir / "result.json", root_result)
    with fixture["resume_log"].open("a") as handle:
        handle.write("START trial=error-a__new task=datacurve/error-a\n")
        handle.write("START trial=error-b__new task=datacurve/error-b\n")
        if extra_start:
            handle.write("START trial=valid-zero__new task=datacurve/valid-zero\n")


class Gate3CancelledRepairTest(unittest.TestCase):
    def test_prepare_archives_only_allowlisted_cancelled_trials(self) -> None:
        with tempfile.TemporaryDirectory() as raw_dir:
            fixture = build_fixture(Path(raw_dir))
            valid_path = fixture["job_dir"] / "valid-zero__old" / "result.json"
            valid_hash = sha256_file(valid_path)

            state = prepare(fixture)

            self.assertEqual(state["status"], "prepared")
            self.assertEqual(len(state["valid_results_before"]), 2)
            self.assertEqual(sha256_file(valid_path), valid_hash)
            self.assertTrue(fixture["job_dir"].joinpath("valid-one__old").is_dir())
            self.assertFalse(fixture["job_dir"].joinpath("error-a__old").exists())
            self.assertTrue(
                fixture["repair_dir"]
                .joinpath("archive/trials/error-a__old/result.json")
                .is_file()
            )
            root_result = json.loads((fixture["job_dir"] / "result.json").read_text())
            self.assertIsNone(root_result["finished_at"])

    def test_finalize_accepts_retry_set_serialization_reordering(self) -> None:
        with tempfile.TemporaryDirectory() as raw_dir:
            fixture = build_fixture(Path(raw_dir))
            config_path = fixture["job_dir"] / "config.json"
            config = json.loads(config_path.read_text())
            config["retry"] = {
                "max_retries": 3,
                "include_exceptions": ["ReadError", "ConnectError"],
                "exclude_exceptions": ["AgentTimeoutError", "VerifierTimeoutError"],
            }
            write_json(config_path, config)
            prepare(fixture)
            config = json.loads(config_path.read_text())
            config["retry"]["include_exceptions"].reverse()
            config["retry"]["exclude_exceptions"].reverse()
            write_json(config_path, config)
            simulate_error_only_resume(fixture)

            result = finalize_repair(repair_dir=fixture["repair_dir"], execute=False)

            self.assertEqual(result["status"], "validated")

    def test_prepare_rejects_non_exact_allowlist(self) -> None:
        with tempfile.TemporaryDirectory() as raw_dir:
            fixture = build_fixture(Path(raw_dir))
            fixture["allowlist"].write_text(
                "datacurve/error-a\ndatacurve/not-an-error\n"
            )

            with self.assertRaisesRegex(
                RepairError, "Allowlist does not exactly match"
            ):
                prepare(fixture)

            self.assertTrue(fixture["job_dir"].joinpath("error-a__old").is_dir())

    def test_prepare_rejects_cancelled_result_with_binary_verifier(self) -> None:
        with tempfile.TemporaryDirectory() as raw_dir:
            fixture = build_fixture(Path(raw_dir))
            result_path = fixture["job_dir"] / "error-a__old" / "result.json"
            result = json.loads(result_path.read_text())
            result["verifier_result"] = {"rewards": {"reward": 0}}
            write_json(result_path, result)

            with self.assertRaisesRegex(RepairError, "verifier-free interrupted"):
                prepare(fixture)

    def test_prepare_rejects_cancelled_result_without_interrupted_termination(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as raw_dir:
            fixture = build_fixture(Path(raw_dir))
            result_path = fixture["job_dir"] / "error-a__old" / "result.json"
            result = json.loads(result_path.read_text())
            result["agent_result"]["metadata"]["termination"] = "result_message"
            write_json(result_path, result)

            with self.assertRaisesRegex(RepairError, "verifier-free interrupted"):
                prepare(fixture)

    def test_prepare_rejects_valid_trial_that_resume_would_reschedule(self) -> None:
        with tempfile.TemporaryDirectory() as raw_dir:
            fixture = build_fixture(Path(raw_dir))
            config_path = fixture["job_dir"] / "valid-zero__old" / "config.json"
            config = json.loads(config_path.read_text())
            config["environment"]["override_memory_mb"] = 1024
            write_json(config_path, config)

            with self.assertRaisesRegex(RepairError, "would be rescheduled"):
                prepare(fixture)

            self.assertTrue(fixture["job_dir"].joinpath("error-a__old").is_dir())

    def test_prepare_rejects_gate2_unresolved_task_drift(self) -> None:
        with tempfile.TemporaryDirectory() as raw_dir:
            fixture = build_fixture(Path(raw_dir))
            report_path = (
                fixture["tts_run_dir"] / "subset_eval" / "gate002_subset_report.json"
            )
            report = json.loads(report_path.read_text())
            report["tasks"][0]["task_name"] = "datacurve/different-task"
            write_json(report_path, report)

            with self.assertRaisesRegex(RepairError, "Gate 2 unresolved tasks"):
                prepare(fixture)

    def test_finalize_validates_and_updates_only_gate3_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as raw_dir:
            fixture = build_fixture(Path(raw_dir))
            valid_path = fixture["job_dir"] / "valid-zero__old" / "result.json"
            valid_hash = sha256_file(valid_path)
            prepare(fixture)
            simulate_error_only_resume(fixture)

            result = finalize_repair(repair_dir=fixture["repair_dir"], execute=True)

            self.assertEqual(result["status"], "finalized")
            self.assertEqual(result["gate_resolved"], 2)
            self.assertEqual(result["cumulative_resolved"], 4)
            self.assertEqual(sha256_file(valid_path), valid_hash)
            report_path = (
                fixture["tts_run_dir"] / "subset_eval" / "gate003_subset_report.json"
            )
            report = json.loads(report_path.read_text())
            self.assertTrue(report["complete"])
            self.assertEqual(report["expected_trials"], 4)
            self.assertEqual(report["error_only_repair"]["rerun_task_count"], 2)
            manifest = json.loads(
                (fixture["tts_run_dir"] / "manifest.json").read_text()
            )
            self.assertEqual(
                [gate["gate_index"] for gate in manifest["gates"]], [0, 1, 2, 3]
            )
            gate3 = next(gate for gate in manifest["gates"] if gate["gate_index"] == 3)
            self.assertEqual(gate3["verifier_report"]["status"], "available_subset")
            loop_state = json.loads(
                (fixture["tts_run_dir"] / "subset_eval" / "loop_state.json").read_text()
            )
            self.assertEqual(loop_state["latest_gate"], 3)
            self.assertFalse(fixture["tts_run_dir"].joinpath("gates/gate_004").exists())
            self.assertFalse(fixture["gate_root"].parent.joinpath("gate_004").exists())

    def test_finalize_rejects_start_outside_error_set(self) -> None:
        with tempfile.TemporaryDirectory() as raw_dir:
            fixture = build_fixture(Path(raw_dir))
            prepare(fixture)
            simulate_error_only_resume(fixture, extra_start=True)

            with self.assertRaisesRegex(RepairError, "START task set"):
                finalize_repair(repair_dir=fixture["repair_dir"], execute=True)

            self.assertFalse(
                fixture["tts_run_dir"]
                .joinpath("subset_eval/gate003_subset_report.json")
                .exists()
            )

    def test_finalize_rejects_changed_preexisting_valid_result(self) -> None:
        with tempfile.TemporaryDirectory() as raw_dir:
            fixture = build_fixture(Path(raw_dir))
            prepare(fixture)
            simulate_error_only_resume(fixture)
            valid_path = fixture["job_dir"] / "valid-zero__old" / "result.json"
            result = json.loads(valid_path.read_text())
            result["finished_at"] = "changed"
            write_json(valid_path, result)

            with self.assertRaisesRegex(RepairError, "Previously valid result changed"):
                finalize_repair(repair_dir=fixture["repair_dir"], execute=True)

    def test_orchestrate_holds_workflow_and_finalizes(self) -> None:
        with tempfile.TemporaryDirectory() as raw_dir:
            fixture = build_fixture(Path(raw_dir))

            result = orchestrate_repair(
                job_dir=fixture["job_dir"],
                tts_run_dir=fixture["tts_run_dir"],
                allowlist_file=fixture["allowlist"],
                repair_dir=fixture["repair_dir"],
                resume_log=fixture["resume_log"],
                expected_error_count=2,
                expected_valid_count=2,
                env_file=Path(raw_dir) / ".env",
                python="python",
                poll_sec=0,
                recovery_rounds=1,
                active_job_wait_timeout_sec=1,
                job_wall_timeout_sec=1,
                resume_runner=lambda _state: simulate_error_only_resume(fixture),
            )

            self.assertEqual(result["status"], "finalized")
            self.assertEqual(
                result["resume_start_tasks"],
                [
                    "datacurve/error-a",
                    "datacurve/error-b",
                ],
            )

    def test_orchestrate_rejects_cli_paths_that_differ_from_prepared_state(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as raw_dir:
            fixture = build_fixture(Path(raw_dir))
            prepare(fixture)
            wrong_tts_run_dir = Path(raw_dir) / "wrong-tts-run"

            with self.assertRaisesRegex(
                RepairError,
                "TTS run directory differs from prepared repair state",
            ):
                orchestrate_repair(
                    job_dir=fixture["job_dir"],
                    tts_run_dir=wrong_tts_run_dir,
                    allowlist_file=fixture["allowlist"],
                    repair_dir=fixture["repair_dir"],
                    resume_log=fixture["resume_log"],
                    expected_error_count=2,
                    expected_valid_count=2,
                    env_file=Path(raw_dir) / ".env",
                    python="python",
                    poll_sec=0,
                    recovery_rounds=1,
                    active_job_wait_timeout_sec=1,
                    job_wall_timeout_sec=1,
                    resume_runner=lambda _state: self.fail(
                        "resume runner must not be called"
                    ),
                )


if __name__ == "__main__":
    unittest.main()
