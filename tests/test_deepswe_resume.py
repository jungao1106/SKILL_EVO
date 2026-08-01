from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from scripts.job_run_lock import exclusive_job_run, job_is_running
from scripts.materialize_deepswe_tts_evolution_gates import (
    materialization_is_reusable,
    sha256_tree,
)
from scripts.attach_deepswe_gate_report import attach_gate_report
from scripts.aggregate_benchmark_job import reconcile_root_job_stats
from scripts.run_benchmark import (
    DEEPSWE_TRANSIENT_RETRY_EXCEPTIONS,
    _deepswe_result_infra_reason,
    _archive_deepswe_infra_trials,
    _apply_saved_config_to_args,
    _classify_deepswe_provider_auth_failure,
    _existing_job_progress,
    _migrate_deepswe_root_config,
    _negative_deepswe_reward,
    _patch_harbor_deepswe_resume_equality,
    _trial_config_without_deepswe_infra,
    _upgrade_deepswe_resume_config,
)
from scripts.run_deepswe import build_deepswe_argv
from scripts.run_deepswe_full_evolution import absolute_path_preserving_symlinks
from scripts import run_swebench_tts_subset_evo_loop as subset_loop


class DeepSweWrapperDefaultsTest(unittest.TestCase):
    def test_implicit_job_names_separate_skills_and_baseline_runs(self) -> None:
        with mock.patch.dict(os.environ, {}, clear=True):
            baseline = build_deepswe_argv(["--no-skills"])
            skills = build_deepswe_argv(["--use-skills"])

        baseline_name = baseline[baseline.index("--job-name") + 1]
        skills_name = skills[skills.index("--job-name") + 1]
        self.assertIn("noskills", baseline_name)
        self.assertIn("skills", skills_name)
        self.assertNotEqual(baseline_name, skills_name)

    def test_implicit_job_name_uses_cli_agent_and_provider(self) -> None:
        with mock.patch.dict(os.environ, {}, clear=True):
            argv = build_deepswe_argv(
                ["--harness=claude-code", "--provider", "sglang", "--use-skills"]
            )
        job_name = argv[argv.index("--job-name") + 1]
        self.assertTrue(job_name.startswith("claude_code_sglang_deepswe_skills_"))

    def test_injects_deepswe_only_resource_retry_and_resume_defaults(self) -> None:
        with mock.patch.dict(os.environ, {}, clear=True):
            argv = build_deepswe_argv(["--job-name", "example"])

        self.assertIn("--resume-existing", argv)
        self.assertIn("--force-agent-internet", argv)
        for option, expected in (
            ("--override-cpus", "2"),
            ("--override-memory-mb", "8192"),
            ("--override-storage-mb", "20480"),
            ("--max-retries", "3"),
            ("--e2b-sandbox-timeout-sec", "14400"),
            ("--verifier-buffer-sec", "4800"),
        ):
            self.assertEqual(argv[argv.index(option) + 1], expected)

    def test_explicit_cli_and_environment_overrides_are_preserved(self) -> None:
        with mock.patch.dict(
            os.environ,
            {
                "E2B_OVERRIDE_MEMORY_MB": "12288",
                "HARBOR_MAX_RETRIES": "5",
            },
            clear=True,
        ):
            argv = build_deepswe_argv(
                [
                    "--job-name",
                    "example",
                    "--override-cpus=4",
                    "--override-storage-mb",
                    "40960",
                ]
            )

        self.assertIn("--override-cpus=4", argv)
        self.assertNotIn("--override-cpus", argv)
        self.assertNotIn("--override-memory-mb", argv)
        self.assertNotIn("--max-retries", argv)
        self.assertEqual(argv[argv.index("--override-storage-mb") + 1], "40960")


class FullEvolutionRunnerTest(unittest.TestCase):
    def test_child_python_path_keeps_virtualenv_symlink(self) -> None:
        with tempfile.TemporaryDirectory() as raw_dir:
            root = Path(raw_dir)
            interpreter = root / "python-real"
            interpreter.write_text("placeholder")
            venv_python = root / "venv-python"
            venv_python.symlink_to(interpreter)

            normalized = absolute_path_preserving_symlinks(venv_python)

            self.assertEqual(normalized, venv_python)
            self.assertTrue(normalized.is_symlink())


class JobResumeStateTest(unittest.TestCase):
    def test_lock_excludes_duplicate_runner_and_releases_after_exit(self) -> None:
        with tempfile.TemporaryDirectory() as raw_dir:
            job_dir = Path(raw_dir) / "job"
            self.assertFalse(job_is_running(job_dir))
            with exclusive_job_run(job_dir):
                self.assertTrue(job_is_running(job_dir))
                with self.assertRaisesRegex(RuntimeError, "already running"):
                    with exclusive_job_run(job_dir):
                        pass
            self.assertFalse(job_is_running(job_dir))

    def test_existing_progress_uses_trial_files_when_root_is_partial(self) -> None:
        with tempfile.TemporaryDirectory() as raw_dir:
            job_dir = Path(raw_dir)
            for name in ("one", "two"):
                trial_dir = job_dir / name
                trial_dir.mkdir()
                (trial_dir / "result.json").write_text("{}")
            (job_dir / "result.json").write_text(
                json.dumps(
                    {
                        "n_total_trials": 3,
                        "finished_at": None,
                        "stats": {"n_trials": 1},
                    }
                )
            )

            self.assertEqual(_existing_job_progress(job_dir), (2, 3, None))

    def test_aggregate_reconciles_stale_root_stats_from_trial_results(self) -> None:
        from harbor.models.agent.context import AgentContext
        from harbor.models.task.id import LocalTaskId
        from harbor.models.trial.config import AgentConfig, TaskConfig, TrialConfig
        from harbor.models.trial.result import AgentInfo, TrialResult
        from harbor.models.verifier.result import VerifierResult

        with tempfile.TemporaryDirectory() as raw_dir:
            root = Path(raw_dir)
            job_dir = root / "job"
            task_dir = root / "task"
            job_dir.mkdir()
            task_dir.mkdir()

            for name, reward in (("failed", 0), ("passed", 1)):
                trial_dir = job_dir / name
                trial_dir.mkdir()
                config = TrialConfig(
                    task=TaskConfig(path=task_dir, source="tasks"),
                    trial_name=name,
                    trials_dir=job_dir,
                    agent=AgentConfig(name="oracle"),
                )
                result = TrialResult(
                    task_name=f"task-{name}",
                    trial_name=name,
                    trial_uri=trial_dir.resolve().as_uri(),
                    task_id=LocalTaskId(path=task_dir),
                    task_checksum="checksum",
                    config=config,
                    agent_info=AgentInfo(name="oracle", version="test"),
                    agent_result=AgentContext(),
                    verifier_result=VerifierResult(rewards={"reward": reward}),
                )
                (trial_dir / "result.json").write_text(result.model_dump_json())

            root_result_path = job_dir / "result.json"
            stale_root = {
                "stats": {
                    "n_trials": 2,
                    "n_errors": 1,
                    "evals": {
                        "oracle__tasks": {
                            "reward_stats": {"reward": {"-1": ["failed"]}},
                            "exception_stats": {"InfraError": ["failed"]},
                        }
                    },
                }
            }
            root_result_path.write_text(json.dumps(stale_root))

            reconciled = reconcile_root_job_stats(
                job_dir,
                root_result_path,
                stale_root,
            )

            stats = reconciled["stats"]
            self.assertEqual(stats["n_trials"], 2)
            self.assertEqual(stats["n_errors"], 0)
            evaluation = next(iter(stats["evals"].values()))
            self.assertEqual(evaluation["reward_stats"]["reward"]["0"], ["failed"])
            self.assertEqual(evaluation["reward_stats"]["reward"]["1"], ["passed"])
            self.assertEqual(evaluation["metrics"], [{"mean": 0.5}])
            self.assertEqual(json.loads(root_result_path.read_text()), reconciled)

    def test_infra_and_interrupted_trials_are_archived_for_reverification(self) -> None:
        from harbor.models.agent.context import AgentContext
        from harbor.models.task.id import LocalTaskId
        from harbor.models.trial.config import AgentConfig, TaskConfig, TrialConfig
        from harbor.models.trial.result import AgentInfo, ExceptionInfo, TrialResult
        from harbor.models.verifier.result import VerifierResult

        with tempfile.TemporaryDirectory() as raw_dir:
            root = Path(raw_dir)
            job_dir = root / "jobs" / "job"
            task_dir = root / "task"
            task_dir.mkdir()
            (task_dir / "pre_artifacts.sh").write_text("#!/bin/sh\n")
            job_dir.mkdir(parents=True)

            def write_trial(
                name: str,
                reward: int | None,
                *,
                exception_type: str | None = None,
                auth: bool = False,
            ) -> None:
                trial_dir = job_dir / name
                trial_dir.mkdir()
                config = TrialConfig(
                    task=TaskConfig(path=task_dir, source="tasks"),
                    trial_name=name,
                    trials_dir=job_dir,
                    agent=AgentConfig(name="oracle"),
                )
                (trial_dir / "config.json").write_text(
                    config.model_dump_json(indent=2)
                )
                result = TrialResult(
                    task_name="task",
                    trial_name=name,
                    trial_uri=trial_dir.resolve().as_uri(),
                    task_id=LocalTaskId(path=task_dir),
                    task_checksum="checksum",
                    config=config,
                    agent_info=AgentInfo(name="oracle", version="test"),
                    agent_result=AgentContext(),
                    verifier_result=(
                        VerifierResult(rewards={"reward": reward})
                        if reward is not None
                        else None
                    ),
                )
                if auth or exception_type:
                    result.exception_info = ExceptionInfo(
                        exception_type=(
                            "NonZeroAgentExitCodeError" if auth else exception_type
                        ),
                        exception_message=(
                            "Claude provider authentication failed"
                            if auth
                            else f"{exception_type} test fixture"
                        ),
                        exception_traceback="",
                        occurred_at=__import__("datetime").datetime.now(),
                    )
                (trial_dir / "result.json").write_text(
                    result.model_dump_json(indent=2)
                )

            write_trial("infra", -1)
            write_trial("failed", 0)
            write_trial("passed", 1)
            write_trial("auth", 0, auth=True)
            write_trial(
                "agent-timeout-no-verifier",
                None,
                exception_type="AgentTimeoutError",
            )
            write_trial(
                "verifier-timeout-no-verifier",
                None,
                exception_type="VerifierTimeoutError",
            )
            write_trial(
                "agent-timeout-with-reward",
                0,
                exception_type="AgentTimeoutError",
            )
            partial = job_dir / "partial"
            partial.mkdir()
            (partial / "config.json").write_text("not json")

            archived = _archive_deepswe_infra_trials(job_dir, run_id="resume")

            self.assertEqual(
                {row["trial_name"] for row in archived},
                {
                    "partial",
                    "agent-timeout-no-verifier",
                    "verifier-timeout-no-verifier",
                },
            )
            reasons = {row["trial_name"]: row["reason"] for row in archived}
            self.assertEqual(
                reasons["agent-timeout-no-verifier"],
                "invalid-verifier-result:missing",
            )
            self.assertEqual(
                reasons["verifier-timeout-no-verifier"],
                "invalid-verifier-result:missing",
            )
            self.assertTrue((job_dir / "infra").exists())
            self.assertTrue((job_dir / "auth").exists())
            self.assertFalse((job_dir / "partial").exists())
            self.assertFalse((job_dir / "agent-timeout-no-verifier").exists())
            self.assertFalse((job_dir / "verifier-timeout-no-verifier").exists())
            self.assertTrue((job_dir / "agent-timeout-with-reward").exists())
            self.assertTrue((job_dir / "failed").exists())
            self.assertTrue((job_dir / "passed").exists())
            archive = root / "jobs" / ".skills-evo-infra-archive" / "job" / "resume"
            self.assertTrue((archive / "trials" / "partial" / "config.json").exists())
            self.assertTrue((archive / "trials.manifest.json").exists())

    def test_negative_reward_is_invalid_but_not_resampled(self) -> None:
        self.assertNotIn("DeepSweVerifierInfraError", DEEPSWE_TRANSIENT_RETRY_EXCEPTIONS)
        self.assertEqual(
            _negative_deepswe_reward(SimpleNamespace(rewards={"reward": -1})),
            -1.0,
        )
        self.assertIsNone(
            _negative_deepswe_reward(SimpleNamespace(rewards={"reward": 0}))
        )

    def test_provider_auth_failure_is_reclassified_for_retry(self) -> None:
        info = SimpleNamespace(
            exception_type="NonZeroAgentExitCodeError",
            exception_message="Claude provider authentication failed; check key",
        )
        result = SimpleNamespace(exception_info=info)
        self.assertTrue(_classify_deepswe_provider_auth_failure(result))
        self.assertEqual(info.exception_type, "DeepSweProviderAuthenticationError")

        for message, expected in (
            (
                "Claude provider transient failure: upstream 502",
                "DeepSweProviderTransientError",
            ),
            (
                "Claude agent stream ended without ResultMessage",
                "DeepSweAgentIncompleteError",
            ),
        ):
            info.exception_type = "NonZeroAgentExitCodeError"
            info.exception_message = message
            self.assertTrue(_classify_deepswe_provider_auth_failure(result))
            self.assertEqual(info.exception_type, expected)

    def test_agent_timeout_with_valid_verifier_reward_is_a_model_outcome(self) -> None:
        result = {
            "verifier_result": {"rewards": {"reward": 0}},
            "exception_info": {"exception_type": "AgentTimeoutError"},
        }
        self.assertIsNone(_deepswe_result_infra_reason(result))
        result["exception_info"]["exception_type"] = "DeepSweProviderRequestError"
        self.assertEqual(
            _deepswe_result_infra_reason(result),
            "exception:DeepSweProviderRequestError",
        )


class DeepSweConfigMigrationTest(unittest.TestCase):
    def test_pi_saved_version_is_restored_without_attribute_error(self) -> None:
        from harbor.models.job.config import JobConfig, RetryConfig
        from harbor.models.trial.config import AgentConfig, EnvironmentConfig

        config = JobConfig(
            job_name="pi-resume",
            jobs_dir=Path("jobs"),
            retry=RetryConfig(max_retries=3),
            environment=EnvironmentConfig(
                override_cpus=2,
                override_memory_mb=8192,
                override_storage_mb=20480,
                kwargs={
                    "sandbox_timeout_sec": 10800,
                    "force_allow_internet": True,
                },
            ),
            agents=[
                AgentConfig(
                    import_path="agents.pi_agent:PiAgent",
                    kwargs={
                        "provider_name": "novita",
                        "benchmark_name": "deepswe",
                        "use_skills": True,
                        "version": "0.80.6",
                    },
                )
            ],
        )
        args = SimpleNamespace(
            _requested_provider=None,
            _requested_dataset=None,
            _requested_use_skills=None,
            _requested_task_names=None,
            _requested_agent=None,
            e2b_sandbox_timeout_sec=7200,
            force_agent_internet=False,
            benchmark_name="deepswe",
            use_skills=False,
            result_only=False,
            agent_timeout_sec=None,
            agent_setup_timeout_sec=1200,
            claude_sdk_version="0.2.116",
            pi_version="old",
        )

        _apply_saved_config_to_args(args, config)

        self.assertEqual(args.agent, "pi")
        self.assertEqual(args.pi_version, "0.80.6")

    def test_upgrades_root_and_keeps_old_trial_configs_matchable(self) -> None:
        from harbor.models.job.config import DatasetConfig, JobConfig, RetryConfig
        from harbor.models.trial.config import AgentConfig, EnvironmentConfig, TaskConfig, TrialConfig

        with tempfile.TemporaryDirectory() as raw_dir:
            root = Path(raw_dir)
            dataset_dir = root / "tasks"
            task_dir = dataset_dir / "task-a"
            task_dir.mkdir(parents=True)
            (dataset_dir / "dataset.toml").write_text("[dataset]\nname='test'\n")
            (dataset_dir / "manifest.json").write_text("{}")
            (task_dir / "pre_artifacts.sh").write_text("#!/bin/sh\n")
            job_dir = root / "jobs" / "job"
            job_dir.mkdir(parents=True)
            previous = JobConfig(
                job_name="job",
                jobs_dir=root / "jobs",
                retry=RetryConfig(max_retries=0),
                environment=EnvironmentConfig(
                    override_cpus=1,
                    override_memory_mb=8192,
                    override_storage_mb=10240,
                ),
                agents=[AgentConfig(name="oracle")],
                datasets=[DatasetConfig(path=dataset_dir)],
            )
            (job_dir / "config.json").write_text(previous.model_dump_json(indent=4))
            upgraded = _upgrade_deepswe_resume_config(previous)

            backup = _migrate_deepswe_root_config(
                job_dir, previous, upgraded, run_id="migration"
            )

            self.assertIsNotNone(backup)
            self.assertEqual(upgraded.environment.override_cpus, 2)
            self.assertEqual(upgraded.environment.override_storage_mb, 20480)
            self.assertEqual(upgraded.retry.max_retries, 3)
            self.assertEqual(
                upgraded.environment.kwargs["sandbox_timeout_sec"], 14400
            )
            self.assertNotIn(
                "VerifierTimeoutError", upgraded.retry.include_exceptions or set()
            )
            self.assertIn(
                "DeepSweProviderTransientError",
                upgraded.retry.include_exceptions or set(),
            )
            saved = JobConfig.model_validate_json((job_dir / "config.json").read_text())
            self.assertEqual(saved, upgraded)
            self.assertTrue(Path(backup).exists())

            old_trial = TrialConfig(
                task=TaskConfig(path=task_dir),
                trials_dir=job_dir,
                agent=AgentConfig(name="oracle"),
                environment=previous.environment,
            )
            new_trial = old_trial.model_copy(deep=True)
            new_trial.environment = upgraded.environment
            self.assertEqual(
                _trial_config_without_deepswe_infra(old_trial),
                _trial_config_without_deepswe_infra(new_trial),
            )
            _patch_harbor_deepswe_resume_equality()
            self.assertEqual(old_trial, new_trial)
            different_agent = new_trial.model_copy(deep=True)
            different_agent.agent = AgentConfig(name="terminus-2")
            self.assertNotEqual(old_trial, different_agent)

    def test_valid_reward_with_legacy_resources_is_archived(self) -> None:
        from harbor.models.agent.context import AgentContext
        from harbor.models.job.config import JobConfig
        from harbor.models.task.id import LocalTaskId
        from harbor.models.trial.config import AgentConfig, EnvironmentConfig, TaskConfig, TrialConfig
        from harbor.models.trial.result import AgentInfo, TrialResult
        from harbor.models.verifier.result import VerifierResult

        with tempfile.TemporaryDirectory() as raw_dir:
            root = Path(raw_dir)
            job_dir = root / "jobs" / "job"
            task_dir = root / "task"
            task_dir.mkdir()
            (task_dir / "pre_artifacts.sh").write_text("#!/bin/sh\n")
            job_dir.mkdir(parents=True)
            contract = {"version": 1, "artifact_hook_version": "test"}
            agent = AgentConfig(
                import_path="agents.claude_sdk_agent:ClaudeSdkAgent",
                kwargs={"resume_contract": contract},
            )
            current_environment = EnvironmentConfig(
                override_cpus=2,
                override_memory_mb=8192,
                override_storage_mb=20480,
                kwargs={
                    "sandbox_timeout_sec": 14400,
                    "force_allow_internet": True,
                },
            )
            root_config = JobConfig(
                job_name="job",
                jobs_dir=root / "jobs",
                agents=[agent],
                environment=current_environment,
            )
            (job_dir / "config.json").write_text(root_config.model_dump_json())
            legacy_environment = current_environment.model_copy(deep=True)
            legacy_environment.override_cpus = 1
            legacy_environment.override_storage_mb = 10240
            legacy_environment.kwargs["sandbox_timeout_sec"] = 9000
            trial_config = TrialConfig(
                task=TaskConfig(path=task_dir),
                trial_name="legacy",
                trials_dir=job_dir,
                agent=agent,
                environment=legacy_environment,
            )
            trial_dir = job_dir / "legacy"
            trial_dir.mkdir()
            (trial_dir / "config.json").write_text(trial_config.model_dump_json())
            result = TrialResult(
                task_name="task",
                trial_name="legacy",
                trial_uri=trial_dir.resolve().as_uri(),
                task_id=LocalTaskId(path=task_dir),
                task_checksum="checksum",
                config=trial_config,
                agent_info=AgentInfo(name="claude", version="test"),
                agent_result=AgentContext(),
                verifier_result=VerifierResult(rewards={"reward": 0}),
            )
            (trial_dir / "result.json").write_text(result.model_dump_json())

            archived = _archive_deepswe_infra_trials(job_dir, run_id="upgrade")

            self.assertEqual(len(archived), 1)
            self.assertEqual(
                archived[0]["reason"],
                "incompatible-trial-contract-or-resources",
            )
            self.assertFalse(trial_dir.exists())


class SubsetResumeTest(unittest.TestCase):
    @staticmethod
    def _args(tmp_path: Path) -> SimpleNamespace:
        return SimpleNamespace(
            python="python",
            dataset="dataset",
            benchmark_name="deepswe",
            provider="novita",
            harness="claude-code",
            concurrency=2,
            agent_timeout_sec=100,
            agent_setup_timeout_sec=20,
            e2b_sandbox_timeout_sec=200,
            provider_base_url=None,
            provider_anthropic_base_url=None,
            provider_model=None,
            provider_api=None,
            claude_max_turns=None,
            claude_max_budget_usd=None,
            env_file=tmp_path / "missing.env",
            poll_sec=0,
            recovery_rounds=2,
        )

    def test_inactive_incomplete_job_is_resumed(self) -> None:
        with tempfile.TemporaryDirectory() as raw_dir:
            root = Path(raw_dir)
            job_name = "partial"
            (root / "jobs" / job_name).mkdir(parents=True)
            task_file = root / "tasks.txt"
            task_file.write_text("task-a\n")
            log_path = root / "resume.log"

            with (
                mock.patch.object(subset_loop, "ROOT", root),
                mock.patch.object(
                    subset_loop, "job_is_complete", side_effect=[False, True]
                ),
                mock.patch.object(subset_loop, "job_is_running", return_value=False),
                mock.patch.object(
                    subset_loop, "job_progress", return_value=(1, 2, None)
                ),
                mock.patch.object(
                    subset_loop.subprocess,
                    "run",
                    return_value=SimpleNamespace(returncode=0),
                ) as run_mock,
            ):
                result = subset_loop.run_subset_eval(
                    args=self._args(root),
                    gate_index=2,
                    previous_gate_index=1,
                    gate_root=root / "gate",
                    task_file=task_file,
                    expected_trials=2,
                    job_name=job_name,
                    log_path=log_path,
                )

            self.assertEqual(result, root / "jobs" / job_name)
            command = run_mock.call_args.args[0]
            self.assertIn("--resume-existing", command)
            self.assertIn("--force-agent-internet", command)
            self.assertIn(job_name, command)
            for option, expected in (
                ("--override-cpus", "2"),
                ("--override-memory-mb", "8192"),
                ("--override-storage-mb", "20480"),
                ("--max-retries", "3"),
            ):
                self.assertEqual(command[command.index(option) + 1], expected)

    def test_local_deepswe_logical_names_map_to_directory_filters(self) -> None:
        with tempfile.TemporaryDirectory() as raw_dir:
            dataset = Path(raw_dir)
            (dataset / "dataset.toml").write_text("[dataset]\nname='test'\n")
            task_dir = dataset / "abs-module-cache-flags"
            task_dir.mkdir()
            (task_dir / "task.toml").write_text(
                "[task]\nname='datacurve/abs-module-cache-flags'\n"
            )

            self.assertEqual(
                subset_loop.dataset_filter_task_names(
                    str(dataset),
                    {"datacurve/abs-module-cache-flags"},
                ),
                {"abs-module-cache-flags"},
            )

    def test_long_subset_job_name_preserves_identity_hashes(self) -> None:
        name = subset_loop.subset_job_name(
            "long-run-" + "x" * 200,
            2,
            1,
            {"task-a", "task-b"},
            "e" * 64,
        )
        task_hash = __import__("hashlib").sha256(
            b"task-a\ntask-b\n"
        ).hexdigest()[:10]
        self.assertLessEqual(len(name), 150)
        self.assertTrue(name.endswith(f"_{task_hash}_{'e' * 10}"))

    def test_complete_job_with_different_gate_tree_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as raw_dir:
            root = Path(raw_dir)
            job_name = "stale-complete"
            job_dir = root / "jobs" / job_name
            job_dir.mkdir(parents=True)
            task_file = root / "tasks.txt"
            task_file.write_text("task-a\n")
            gate_root = root / "gate"
            skill = gate_root / "skill" / "SKILL.md"
            skill.parent.mkdir(parents=True)
            skill.write_text("current gate\n")
            (job_dir / "config.json").write_text(
                json.dumps(
                    {
                        "datasets": [{"task_names": ["task-a"]}],
                        "agents": [
                            {
                                "kwargs": {
                                    "resume_contract": {
                                        "skills": {"tree_sha256": ["stale"]},
                                        "provider": {
                                            "name": "novita",
                                            "model": "zai-org/glm-5.2",
                                        },
                                    }
                                }
                            }
                        ],
                    }
                )
            )

            with mock.patch.object(subset_loop, "ROOT", root):
                with self.assertRaisesRegex(SystemExit, "different gate skill tree"):
                    subset_loop.run_subset_eval(
                        args=self._args(root),
                        gate_index=2,
                        previous_gate_index=1,
                        gate_root=gate_root,
                        task_file=task_file,
                        expected_trials=1,
                        job_name=job_name,
                        log_path=root / "resume.log",
                    )


class MaterializationReuseTest(unittest.TestCase):
    def test_complete_gate_materialization_is_reusable(self) -> None:
        with tempfile.TemporaryDirectory() as raw_dir:
            root = Path(raw_dir)
            run_dir = root / "run"
            skill_root = root / "skills"
            required = (
                run_dir / "manifest.json",
                run_dir / "promotion_decisions.jsonl",
                run_dir / "gates" / "gate_000" / "manifest.json",
                run_dir / "gates" / "gate_001" / "manifest.json",
                skill_root / "gate_000" / "gate_library_manifest.json",
                skill_root / "gate_001" / "gate_library_manifest.json",
            )
            for path in required:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("{}")
            skill_file = skill_root / "gate_001" / "skill" / "SKILL.md"
            skill_file.parent.mkdir(parents=True)
            skill_file.write_text("stable skill\n")
            (run_dir / "manifest.json").write_text(
                json.dumps(
                    {
                        "output_fingerprints": {
                            "gate_000_tree_sha256": sha256_tree(
                                skill_root / "gate_000"
                            ),
                            "gate_001_tree_sha256": sha256_tree(
                                skill_root / "gate_001"
                            ),
                        }
                    }
                )
            )

            self.assertTrue(materialization_is_reusable(run_dir, skill_root))
            skill_file.write_text("mutated skill\n")
            self.assertFalse(materialization_is_reusable(run_dir, skill_root))
            skill_file.write_text("stable skill\n")
            required[-1].unlink()
            self.assertFalse(materialization_is_reusable(run_dir, skill_root))

    def test_attach_report_does_not_mutate_frozen_skill_tree(self) -> None:
        with tempfile.TemporaryDirectory() as raw_dir:
            root = Path(raw_dir)
            run_dir = root / "run"
            gate_root = root / "skills" / "gate_001"
            skill = gate_root / "skill" / "SKILL.md"
            skill.parent.mkdir(parents=True)
            skill.write_text("frozen\n")
            (gate_root / "gate_library_manifest.json").write_text("{}\n")
            before = sha256_tree(gate_root)
            manifest_path = run_dir / "manifest.json"
            manifest_path.parent.mkdir(parents=True)
            manifest_path.write_text(
                json.dumps(
                    {
                        "run_id": "tts-run",
                        "benchmark_name": "deepswe",
                        "source_run_id": "source",
                        "summary": {
                            "task_evidence": 1,
                            "repo_candidates": 0,
                            "failure_candidates": 0,
                            "promoted_skills": 0,
                        },
                        "gates": [
                            {
                                "gate_index": 1,
                                "skill_root": str(gate_root),
                                "skill_counts": {"total": 1},
                                "verifier_report": {"status": "not_run"},
                            }
                        ],
                    }
                )
            )
            run_gate_manifest = run_dir / "gates" / "gate_001" / "manifest.json"
            run_gate_manifest.parent.mkdir(parents=True)
            run_gate_manifest.write_text("{}\n")
            aggregate = root / "aggregate.json"
            aggregate.write_text(
                json.dumps(
                    {
                        "run_id": "gate1-eval",
                        "benchmark_name": "deepswe",
                        "complete": True,
                        "infra_invalid_trials": [],
                        "tasks": [{"task_name": "task-a", "reward": 0}],
                        "evaluation": {
                            "n_trials": 1,
                            "n_errors": 0,
                            "resolved": 0,
                            "mean_reward": 0.0,
                        },
                        "completeness": {
                            "expected_trials": 1,
                            "trial_result_files": 1,
                        },
                        "provenance": {
                            "resume_contract": {
                                "skills": {"tree_sha256": [before]}
                            }
                        },
                    }
                )
            )

            attached = attach_gate_report(
                manifest_path=manifest_path,
                gate_index=1,
                aggregate_path=aggregate,
                eval_run_id="gate1-eval",
                expected_benchmark_name="deepswe",
            )

            self.assertEqual(attached["status"], "available")
            self.assertEqual(sha256_tree(gate_root), before)


if __name__ == "__main__":
    unittest.main()
