from __future__ import annotations

import copy
import hashlib
import json
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from evolution.score import summarize_job
from providers import populate_anthropic_provider_env, resolve_provider
from scripts import run_deepswe_full_evolution as full_evolution
from scripts.materialize_deepswe_tts_evolution_gates import (
    TASKWISE_OR_SAMPLING_POLICY,
    sha256_tree,
)
from scripts.run_deepswe_setting_bon import (
    Setting,
    build_merged_score_report,
    combine_task_rows,
)


class AnthropicProviderEnvironmentTest(unittest.TestCase):
    def test_explicit_generic_environment_overrides_env_file_provider_values(
        self,
    ) -> None:
        provider = resolve_provider("macaron")
        with tempfile.TemporaryDirectory() as raw_dir:
            env_file = Path(raw_dir) / ".env"
            env_file.write_text(
                "MACARON_ANTHROPIC_BASE_URL=https://file.invalid\n"
                "MACARON_API_KEY=file-token\n"
            )
            with mock.patch.dict(
                os.environ,
                {
                    "ANTHROPIC_BASE_URL": "https://export.invalid",
                    "ANTHROPIC_AUTH_TOKEN": "export-token",
                },
                clear=True,
            ):
                env = full_evolution.provider_runtime_env(env_file, provider)

        self.assertEqual(
            env["MACARON_ANTHROPIC_BASE_URL"], "https://export.invalid"
        )
        self.assertEqual(env["MACARON_API_KEY"], "export-token")

    def test_maps_standard_anthropic_environment_for_selected_provider(self) -> None:
        provider = resolve_provider("macaron")
        env = {
            "ANTHROPIC_BASE_URL": "https://example.invalid",
            "ANTHROPIC_AUTH_TOKEN": "test-auth-token",
        }

        changed = populate_anthropic_provider_env(provider, env)

        self.assertTrue(changed)
        self.assertEqual(env["MACARON_ANTHROPIC_BASE_URL"], env["ANTHROPIC_BASE_URL"])
        self.assertEqual(env["MACARON_API_KEY"], env["ANTHROPIC_AUTH_TOKEN"])

    def test_provider_specific_environment_takes_precedence(self) -> None:
        provider = resolve_provider("macaron")
        env = {
            "ANTHROPIC_BASE_URL": "https://generic.invalid",
            "ANTHROPIC_AUTH_TOKEN": "generic-token",
            "MACARON_ANTHROPIC_BASE_URL": "https://specific.invalid",
            "MACARON_API_KEY": "specific-token",
        }

        changed = populate_anthropic_provider_env(provider, env)

        self.assertFalse(changed)
        self.assertEqual(
            env["MACARON_ANTHROPIC_BASE_URL"], "https://specific.invalid"
        )
        self.assertEqual(env["MACARON_API_KEY"], "specific-token")


class ExistingFrozenReportTest(unittest.TestCase):
    def _validate(
        self,
        report_path: Path,
        task_names: list[str],
        *,
        run_id: str = "frozen-run",
    ) -> Path:
        with (
            mock.patch.object(full_evolution, "infra_invalid_trials", return_value=[]),
            mock.patch.object(
                full_evolution,
                "job_identity_issues",
                return_value=([], len(task_names)),
            ),
        ):
            return full_evolution.validate_existing_frozen_report(
                report_path,
                expected_trials=len(task_names),
                expected_run_id=run_id,
                expected_task_names=task_names,
            )

    def _fixture(
        self, root: Path, *, run_id: str = "frozen-run", n_trials: int = 3
    ) -> tuple[Path, list[str], dict[str, object], Path]:
        job_dir = root / run_id
        job_dir.mkdir()
        config_path = job_dir / "config.json"
        config_path.write_text('{"agents": []}\n')
        finished_at = "2026-07-16T00:00:00+00:00"
        (job_dir / "result.json").write_text(
            json.dumps({"finished_at": finished_at, "n_total_trials": n_trials})
        )
        task_names = []
        for index in range(n_trials):
            task_name = f"datacurve/task-{index:03d}"
            trial_name = f"task-{index:03d}__trial"
            task_names.append(task_name)
            trial_dir = job_dir / trial_name
            trial_dir.mkdir()
            (trial_dir / "result.json").write_text(
                json.dumps(
                    {
                        "task_name": task_name,
                        "trial_name": trial_name,
                        "exception_info": None,
                        "verifier_result": {"rewards": {"reward": index % 2}},
                    }
                )
            )

        evaluation = summarize_job(job_dir)
        report: dict[str, object] = {
            "schema_version": 1,
            "run_id": run_id,
            "complete": True,
            "benchmark_name": "deepswe",
            "infra_invalid_trials": [],
            "completeness": {
                "expected_trials": n_trials,
                "trial_result_files": n_trials,
                "infra_invalid_trials": 0,
                "finished_at": finished_at,
            },
            "evaluation": evaluation,
            "tasks": evaluation["tasks"],
            "provenance": {
                "benchmark_name": "deepswe",
                "job_config_sha256": hashlib.sha256(
                    config_path.read_bytes()
                ).hexdigest(),
                "task_set_sha256": full_evolution.task_set_sha256(task_names),
            },
        }
        report_path = root / "aggregate" / "score_report.json"
        report_path.parent.mkdir()
        report_path.write_text(json.dumps(report))
        return report_path, task_names, report, job_dir

    def test_accepts_report_that_matches_all_source_results(self) -> None:
        with tempfile.TemporaryDirectory() as raw_dir:
            report_path, task_names, _, _ = self._fixture(Path(raw_dir))

            validated = self._validate(report_path, task_names)

            self.assertEqual(validated, report_path.resolve())

    def test_accepts_taskwise_or_after_validating_both_source_jobs(self) -> None:
        with tempfile.TemporaryDirectory() as raw_dir:
            root = Path(raw_dir)
            (root / "first").mkdir()
            (root / "second").mkdir()
            first_path, first_tasks, first_report, first_job = self._fixture(
                root / "first",
                run_id="sample-1",
                n_trials=3,
            )
            second_path, _, second_report, second_job = self._fixture(
                root / "second",
                run_id="sample-2",
                n_trials=2,
            )
            second_results = sorted(second_job.glob("*/result.json"))
            for result_path, task_name, reward in (
                (second_results[0], first_tasks[0], 1),
                (second_results[1], first_tasks[2], 0),
            ):
                result = json.loads(result_path.read_text())
                result["task_name"] = task_name
                result["verifier_result"]["rewards"]["reward"] = reward
                result_path.write_text(json.dumps(result))
            second_evaluation = summarize_job(second_job)
            second_report["evaluation"] = second_evaluation
            second_report["tasks"] = second_evaluation["tasks"]
            second_report["provenance"]["task_set_sha256"] = (
                full_evolution.task_set_sha256(
                    [row["task_name"] for row in second_evaluation["tasks"]]
                )
            )

            skill_root = root / "skills"
            skill_root.mkdir()
            contract = {
                "version": 1,
                "skills": {
                    "roots": [str(skill_root)],
                    "tree_sha256": [sha256_tree(skill_root)],
                },
                "dataset": {"path": "dataset", "tree_sha256": "dataset-hash"},
            }
            first_report["provenance"]["resume_contract"] = contract
            second_report["provenance"]["resume_contract"] = contract
            for job_dir, source_report in (
                (first_job, first_report),
                (second_job, second_report),
            ):
                source_tasks = [
                    row["task_name"] for row in source_report["evaluation"]["tasks"]
                ]
                config_path = job_dir / "config.json"
                config_path.write_text(
                    json.dumps(
                        {
                            "job_name": job_dir.name,
                            "retry": {
                                "include_exceptions": [],
                                "exclude_exceptions": [],
                            },
                            "datasets": [{"task_names": source_tasks}],
                            "agents": [{"kwargs": {"resume_contract": contract}}],
                        }
                    )
                )
                source_report["provenance"]["job_config_sha256"] = hashlib.sha256(
                    config_path.read_bytes()
                ).hexdigest()
            first_path.write_text(json.dumps(first_report))
            second_path.write_text(json.dumps(second_report))
            combined = combine_task_rows(first_report["tasks"], second_report["tasks"])
            merged = build_merged_score_report(
                setting=Setting("frozen", 0, first_path, skill_root),
                source_report=first_report,
                skill_tree_sha256=sha256_tree(skill_root),
                sample_2_report=second_report,
                sample_2_report_path=second_path,
                best_of_2_report={
                    "kind": "deepswe_same_setting_best_of_2",
                    "complete": True,
                    "setting": "frozen",
                    "sampling_policy": dict(TASKWISE_OR_SAMPLING_POLICY),
                    "tasks": combined,
                },
            )
            merged_path = root / "merged_score_report.json"
            merged_path.write_text(json.dumps(merged))

            def identity(job_dir: Path) -> tuple[list[str], int]:
                return [], len(list(job_dir.glob("*/result.json")))

            with (
                mock.patch.object(
                    full_evolution,
                    "infra_invalid_trials",
                    return_value=[],
                ),
                mock.patch.object(
                    full_evolution,
                    "job_identity_issues",
                    side_effect=identity,
                ),
            ):
                validated = full_evolution.validate_existing_frozen_report(
                    merged_path,
                    expected_trials=3,
                    expected_run_id=str(merged["run_id"]),
                    expected_task_names=first_tasks,
                    expected_skill_root=skill_root,
                )

                other_skill_root = root / "other-skills"
                other_skill_root.mkdir()
                with self.assertRaisesRegex(ValueError, "requested base skill root"):
                    full_evolution.validate_existing_frozen_report(
                        merged_path,
                        expected_trials=3,
                        expected_run_id=str(merged["run_id"]),
                        expected_task_names=first_tasks,
                        expected_skill_root=other_skill_root,
                    )

            self.assertEqual(validated, merged_path.resolve())
            self.assertEqual(merged["evaluation"]["resolved"], 2)

    def test_rejects_incomplete_mismatched_or_infra_invalid_report(self) -> None:
        with tempfile.TemporaryDirectory() as raw_dir:
            report_path, task_names, report, _ = self._fixture(Path(raw_dir))
            cases = {
                "wrong run": lambda value: value.__setitem__("run_id", "other"),
                "not complete": lambda value: value.__setitem__("complete", False),
                "wrong count": lambda value: value["completeness"].__setitem__(
                    "trial_result_files", 2
                ),
                "infra invalid": lambda value: value.__setitem__(
                    "infra_invalid_trials", [{"trial_name": "bad"}]
                ),
                "task rows differ": lambda value: value.__setitem__("tasks", []),
            }
            for label, mutate in cases.items():
                with self.subTest(label=label):
                    candidate = copy.deepcopy(report)
                    mutate(candidate)
                    report_path.write_text(json.dumps(candidate))
                    with self.assertRaisesRegex(ValueError, "failed validation"):
                        self._validate(report_path, task_names)

    def test_rejects_report_when_authoritative_trial_result_changed(self) -> None:
        with tempfile.TemporaryDirectory() as raw_dir:
            report_path, task_names, _, job_dir = self._fixture(Path(raw_dir))
            result_path = job_dir / "task-000__trial" / "result.json"
            result = json.loads(result_path.read_text())
            result["verifier_result"]["rewards"]["reward"] = 1
            result_path.write_text(json.dumps(result))

            with self.assertRaisesRegex(ValueError, "no longer match"):
                self._validate(report_path, task_names)

    def test_rejects_report_when_source_job_config_changed(self) -> None:
        with tempfile.TemporaryDirectory() as raw_dir:
            report_path, task_names, _, job_dir = self._fixture(Path(raw_dir))
            (job_dir / "config.json").write_text('{"agents": ["changed"]}\n')

            with self.assertRaisesRegex(ValueError, "config hash mismatch"):
                self._validate(report_path, task_names)

    def test_rejects_current_infra_or_identity_issues_from_source_job(self) -> None:
        with tempfile.TemporaryDirectory() as raw_dir:
            report_path, task_names, _, _ = self._fixture(Path(raw_dir))
            with (
                mock.patch.object(
                    full_evolution,
                    "infra_invalid_trials",
                    return_value=[{"trial_name": "bad", "reason": "negative-reward"}],
                ),
                mock.patch.object(
                    full_evolution,
                    "job_identity_issues",
                    return_value=([], 3),
                ),
                self.assertRaisesRegex(ValueError, "infra-invalid"),
            ):
                full_evolution.validate_existing_frozen_report(
                    report_path,
                    expected_trials=3,
                    expected_run_id="frozen-run",
                    expected_task_names=task_names,
                )


class ExistingFrozenEvolutionFlowTest(unittest.TestCase):
    def test_code_change_allowance_requires_an_existing_frozen_report(self) -> None:
        with tempfile.TemporaryDirectory() as raw_dir:
            root = Path(raw_dir)
            args = SimpleNamespace(
                dataset=root / "dataset",
                base_skill_root=root / "skills",
                policy_state=root / "policy.json",
                env_file=root / ".env",
                python=root / "python",
                existing_frozen_report=None,
                allow_run_benchmark_code_change=True,
            )

            with self.assertRaisesRegex(
                SystemExit,
                "requires --existing-frozen-report",
            ):
                full_evolution.run_full(args, root / "state.json")

    def test_reusing_frozen_report_still_runs_gate_1_and_later_gates(self) -> None:
        with tempfile.TemporaryDirectory() as raw_dir:
            root = Path(raw_dir)
            dataset = root / "dataset"
            for index in range(113):
                task_dir = dataset / f"task-{index:03d}"
                task_dir.mkdir(parents=True)
                (task_dir / "task.toml").write_text(
                    f'[task]\nname = "datacurve/task-{index:03d}"\n'
                )
            base_skills = root / "base-skills"
            base_skills.mkdir()
            policy = root / "policy.json"
            policy.write_text("{}")
            env_file = root / ".env"
            env_file.write_text("E2B_API_KEY=test\n")
            python = root / "python"
            python.write_text("")
            existing_report = root / "existing" / "score_report.json"
            existing_report.parent.mkdir()
            existing_report.write_text("{}")
            args = SimpleNamespace(
                launcher_id="launcher",
                frozen_run_id="frozen-run",
                tts_run_id="tts-run",
                dataset=dataset,
                base_skill_root=base_skills,
                policy_state=policy,
                env_file=env_file,
                python=python,
                provider="novita",
                model="zai-org/glm-5.2",
                claude_sdk_version="0.2.116",
                pi_version="0.80.6",
                concurrency=15,
                agent_timeout_sec=7200,
                agent_setup_timeout_sec=1200,
                recovery_rounds=4,
                max_gate=4,
                existing_frozen_report=existing_report,
                allow_run_benchmark_code_change=False,
            )
            state_path = root / "run" / "state.json"
            gate1_report = root / "gate1" / "score_report.json"
            provider = mock.Mock()
            provider.required_env.return_value = []

            with (
                mock.patch.object(full_evolution, "validate_child_python"),
                mock.patch.object(
                    full_evolution,
                    "resolve_provider",
                    return_value=provider,
                ),
                mock.patch.object(
                    full_evolution.shutil,
                    "disk_usage",
                    return_value=SimpleNamespace(free=10 * 1024**3),
                ),
                mock.patch.object(
                    full_evolution,
                    "validate_existing_frozen_report",
                    return_value=existing_report.resolve(),
                ) as validate_report,
                mock.patch.object(
                    full_evolution,
                    "validate_reused_frozen_execution",
                    return_value={"code_mismatches": {}},
                ) as validate_execution,
                mock.patch.object(
                    full_evolution,
                    "run_eval_until_valid",
                    return_value=gate1_report,
                ) as run_eval,
                mock.patch.object(
                    full_evolution,
                    "run_logged",
                    return_value=0,
                ) as run_logged,
            ):
                full_evolution.run_full(args, state_path)

            validate_report.assert_called_once()
            validate_execution.assert_called_once()
            self.assertEqual(
                validate_execution.call_args.args[:2],
                (args, existing_report.resolve()),
            )
            runtime_environment = validate_execution.call_args.kwargs[
                "runtime_environment"
            ]
            self.assertEqual(runtime_environment["TIMEOUT_MULTIPLIER"], "1.0")
            self.assertEqual(
                runtime_environment["AGENT_SETUP_TIMEOUT_MULTIPLIER"],
                "2.0",
            )
            self.assertEqual(run_eval.call_count, 1)
            self.assertEqual(
                run_eval.call_args.kwargs["job_name"], "tts-run_gate001_eval"
            )
            commands = [call.kwargs["command"] for call in run_logged.call_args_list]
            self.assertTrue(
                any(
                    any(
                        part.endswith("materialize_deepswe_tts_evolution_gates.py")
                        for part in command
                    )
                    for command in commands
                )
            )
            subset = next(
                command
                for command in commands
                if any(
                    part.endswith("run_swebench_tts_subset_evo_loop.py")
                    for part in command
                )
            )
            self.assertEqual(subset[subset.index("--start-gate") + 1], "2")
            self.assertEqual(subset[subset.index("--max-gate") + 1], "4")
            state = json.loads(state_path.read_text())
            self.assertTrue(state["frozen_report_reused"])
            self.assertEqual(
                state["frozen_execution_contract"],
                {"code_mismatches": {}},
            )
            self.assertEqual(state["status"], "complete")


class RecoveryProgressTest(unittest.TestCase):
    def test_terminal_invalid_job_does_not_spin_all_recovery_rounds(self) -> None:
        with tempfile.TemporaryDirectory() as raw_dir:
            root = Path(raw_dir)
            job_name = "terminal-invalid"
            job_dir = root / "jobs" / job_name
            trial_dir = job_dir / "task__trial"
            trial_dir.mkdir(parents=True)
            (trial_dir / "result.json").write_text('{"stable": true}\n')
            invalid = [
                {
                    "trial_name": "task__trial",
                    "reason": "negative-reward:-1",
                }
            ]
            args = SimpleNamespace(
                python=Path("/python"),
                dataset=Path("/dataset"),
                provider="novita",
                model="zai-org/glm-5.2",
                concurrency=15,
                agent_timeout_sec=7200,
                agent_setup_timeout_sec=1200,
                claude_sdk_version="0.2.116",
                recovery_rounds=4,
            )
            skill_root = root / "skills"
            skill_root.mkdir()

            with (
                mock.patch.object(full_evolution, "ROOT", root),
                mock.patch.object(
                    full_evolution,
                    "infra_invalid_trials",
                    return_value=invalid,
                ),
                mock.patch.object(
                    full_evolution,
                    "run_logged",
                    return_value=1,
                ) as run_logged,
                self.assertRaisesRegex(RuntimeError, "made no progress"),
            ):
                full_evolution.run_eval_until_valid(
                    args=args,
                    env={},
                    job_name=job_name,
                    skill_root=skill_root,
                    output_dir=root / "aggregate",
                    log_dir=root / "logs",
                    expected_trials=1,
                )

            self.assertEqual(run_logged.call_count, 2)


if __name__ == "__main__":
    unittest.main()
