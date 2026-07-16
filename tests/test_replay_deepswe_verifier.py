import asyncio
import hashlib
import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

from harbor.models.agent.context import AgentContext
from harbor.models.task.id import LocalTaskId
from harbor.models.task.task import Task
from harbor.models.trial.config import (
    AgentConfig,
    EnvironmentConfig,
    TaskConfig,
    TrialConfig,
)
from harbor.models.trial.result import AgentInfo, ExceptionInfo, TrialResult
from harbor.models.verifier.result import VerifierResult

from scripts.replay_deepswe_verifier import (
    build_overlay,
    execute_replay,
    prepare_replay,
    run_isolated_verifier,
)


class DeepSweVerifierReplayTest(unittest.TestCase):
    def create_source_trial(self, root: Path) -> Path:
        task_dir = root / "tasks" / "sample-task"
        (task_dir / "environment").mkdir(parents=True)
        (task_dir / "tests").mkdir()
        (task_dir / "instruction.md").write_text("Fix the sample task.\n")
        (task_dir / "pre_artifacts.sh").write_text("#!/bin/sh\n")
        (task_dir / "environment" / "Dockerfile").write_text(
            "FROM example.invalid/base\nWORKDIR /app\n"
        )
        (task_dir / "tests" / "test.sh").write_text("#!/bin/sh\n")
        (task_dir / "tests" / "test.patch").write_text("sample hidden tests\n")
        (task_dir / "task.toml").write_text(
            """\
schema_version = "1.1"
[task]
name = "datacurve/sample-task"
[verifier]
environment_mode = "separate"
timeout_sec = 30.0
[verifier.environment]
build_timeout_sec = 60.0
cpus = 2
memory_mb = 8192
storage_mb = 20480
allow_internet = false
[environment]
build_timeout_sec = 60.0
docker_image = "example.invalid/sample:latest"
cpus = 2
memory_mb = 8192
storage_mb = 20480
allow_internet = false
"""
        )

        job_dir = root / "jobs" / "source-job"
        trial_dir = job_dir / "sample-task__abc"
        (trial_dir / "artifacts").mkdir(parents=True)
        patch = b"diff --git a/file.go b/file.go\n"
        (trial_dir / "artifacts" / "model.patch").write_bytes(patch)
        config = TrialConfig(
            task=TaskConfig(path=task_dir, source="tasks"),
            trial_name=trial_dir.name,
            trials_dir=job_dir,
            agent=AgentConfig(
                import_path="agents.claude_sdk_agent:ClaudeSdkAgent",
                model_name="novita/zai-org/glm-5.2",
            ),
            environment=EnvironmentConfig(
                import_path="environments.e2b_swebench:E2BSwebenchEnvironment",
                delete=True,
                override_cpus=2,
                override_memory_mb=8192,
                override_storage_mb=20480,
                kwargs={
                    "template_namespace": "test",
                    "sandbox_timeout_sec": 14400,
                },
            ),
        )
        (trial_dir / "config.json").write_text(config.model_dump_json(indent=2))
        result = TrialResult(
            task_name="datacurve/sample-task",
            trial_name=trial_dir.name,
            trial_uri=trial_dir.resolve().as_uri(),
            task_id=LocalTaskId(path=task_dir),
            task_checksum=Task(task_dir).checksum,
            config=config,
            agent_info=AgentInfo(name="claude-code", version="test"),
            agent_result=AgentContext(
                metadata={"completed": True, "termination": "result_message"}
            ),
            verifier_result=VerifierResult(rewards={"reward": -1}),
            exception_info=ExceptionInfo(
                exception_type="DeepSweVerifierInfraError",
                exception_message="missing verifier dependency",
                exception_traceback="",
                occurred_at=datetime.now(timezone.utc),
            ),
        )
        (trial_dir / "result.json").write_text(result.model_dump_json(indent=2))
        return trial_dir

    @staticmethod
    def tree_fingerprint(root: Path) -> dict[str, str]:
        return {
            str(path.relative_to(root)): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in sorted(root.rglob("*"))
            if path.is_file()
        }

    def test_prepare_creates_auditable_sidecar_without_mutating_source(self) -> None:
        with tempfile.TemporaryDirectory() as raw_dir:
            root = Path(raw_dir)
            source = self.create_source_trial(root)
            before = self.tree_fingerprint(source)
            replay_dir, request = prepare_replay(
                source_trial_dir=source,
                output_root=root / "replays",
                replay_id="replay-one",
            )

            self.assertEqual(before, self.tree_fingerprint(source))
            self.assertFalse(replay_dir.is_relative_to(source))
            self.assertEqual(request["status"], "prepared")
            self.assertEqual(request["source"]["trial_name"], source.name)
            self.assertEqual(
                request["model_patch"]["sha256"],
                hashlib.sha256(
                    (source / "artifacts" / "model.patch").read_bytes()
                ).hexdigest(),
            )
            self.assertEqual(
                (replay_dir / "artifacts" / "model.patch").read_bytes(),
                (source / "artifacts" / "model.patch").read_bytes(),
            )
            self.assertTrue(request["task"]["tree_sha256"])
            self.assertTrue(request["task"]["tests_tree_sha256"])
            self.assertTrue(request["runner"]["commit"])
            self.assertFalse(request["safety"]["agent_executed"])
            self.assertEqual(
                json.loads((replay_dir / "request.json").read_text()),
                request,
            )

    def test_prepare_rejects_valid_source_and_output_inside_source(self) -> None:
        with tempfile.TemporaryDirectory() as raw_dir:
            root = Path(raw_dir)
            source = self.create_source_trial(root)
            result_path = source / "result.json"
            result = json.loads(result_path.read_text())
            result["verifier_result"]["rewards"]["reward"] = 0
            result["exception_info"] = None
            result_path.write_text(json.dumps(result))
            with self.assertRaisesRegex(ValueError, "infra-valid"):
                prepare_replay(
                    source_trial_dir=source,
                    output_root=root / "replays",
                    replay_id="valid",
                )

            result["verifier_result"]["rewards"]["reward"] = -1
            result_path.write_text(json.dumps(result))
            with self.assertRaisesRegex(ValueError, "sidecar"):
                prepare_replay(
                    source_trial_dir=source,
                    output_root=source,
                    replay_id="nested",
                )

    def test_prepare_rejects_non_verifier_infrastructure_exception(self) -> None:
        with tempfile.TemporaryDirectory() as raw_dir:
            root = Path(raw_dir)
            source = self.create_source_trial(root)
            result_path = source / "result.json"
            result = json.loads(result_path.read_text())
            result["exception_info"]["exception_type"] = (
                "DeepSweProviderAuthenticationError"
            )
            result_path.write_text(json.dumps(result))

            with self.assertRaisesRegex(ValueError, "non-verifier"):
                prepare_replay(
                    source_trial_dir=source,
                    output_root=root / "replays",
                    replay_id="provider-failure",
                )

    def test_prepare_rejects_incomplete_agent_and_empty_patch(self) -> None:
        with tempfile.TemporaryDirectory() as raw_dir:
            root = Path(raw_dir)
            source = self.create_source_trial(root)
            result_path = source / "result.json"
            result = json.loads(result_path.read_text())

            result["agent_result"]["metadata"]["completed"] = False
            result_path.write_text(json.dumps(result))
            with self.assertRaisesRegex(ValueError, "completed agent"):
                prepare_replay(
                    source_trial_dir=source,
                    output_root=root / "incomplete-replay",
                )

            result["agent_result"]["metadata"]["completed"] = True
            result_path.write_text(json.dumps(result))
            (source / "artifacts" / "model.patch").write_bytes(b"")
            with self.assertRaisesRegex(ValueError, "non-empty model.patch"):
                prepare_replay(
                    source_trial_dir=source,
                    output_root=root / "empty-patch-replay",
                )

    def test_execute_writes_eligible_overlay_without_running_agent(self) -> None:
        with tempfile.TemporaryDirectory() as raw_dir:
            root = Path(raw_dir)
            source = self.create_source_trial(root)
            replay_dir, request = prepare_replay(
                source_trial_dir=source,
                output_root=root / "replays",
                replay_id="successful",
            )
            before = self.tree_fingerprint(source)
            with mock.patch(
                "scripts.replay_deepswe_verifier.run_isolated_verifier",
                new=mock.AsyncMock(return_value={"rewards": {"reward": 1}}),
            ) as verifier:
                result = asyncio.run(execute_replay(replay_dir, request))

            verifier.assert_awaited_once()
            self.assertEqual(before, self.tree_fingerprint(source))
            self.assertEqual(result["status"], "complete")
            overlay = json.loads((replay_dir / "overlay.json").read_text())
            self.assertTrue(overlay["eligible"])
            self.assertFalse(overlay["automatic_application"])
            self.assertEqual(
                overlay["replacement"]["verifier_result"],
                {"rewards": {"reward": 1}},
            )
            self.assertEqual(
                overlay["source_guard"]["model_patch_sha256"],
                request["model_patch"]["sha256"],
            )

    def test_isolated_replay_reuses_fresh_verifier_without_agent_factory(self) -> None:
        with tempfile.TemporaryDirectory() as raw_dir:
            root = Path(raw_dir)
            source = self.create_source_trial(root)
            replay_dir, request = prepare_replay(
                source_trial_dir=source,
                output_root=root / "replays",
                replay_id="isolated",
            )
            environment = mock.AsyncMock()

            async def install_environment(context, **_kwargs):
                context._environment = environment

            verifier = mock.Mock()
            verifier.verify = mock.AsyncMock(
                return_value=VerifierResult(rewards={"reward": 0})
            )
            with (
                mock.patch(
                    "scripts.replay_deepswe_verifier."
                    "_replace_deepswe_verifier_environment",
                    new=mock.AsyncMock(side_effect=install_environment),
                ) as replace_environment,
                mock.patch("scripts.replay_deepswe_verifier._patch_e2b_disable_http2"),
                mock.patch("scripts.replay_deepswe_verifier._patch_harbor_runtime"),
                mock.patch(
                    "harbor.verifier.verifier.Verifier",
                    return_value=verifier,
                ),
                mock.patch(
                    "harbor.agents.factory.AgentFactory.create_agent_from_config",
                    side_effect=AssertionError("agent factory must not be called"),
                ) as agent_factory,
            ):
                result = asyncio.run(
                    run_isolated_verifier(
                        replay_dir=replay_dir,
                        request=request,
                    )
                )

            self.assertEqual(result, {"rewards": {"reward": 0}})
            replace_environment.assert_awaited_once()
            verifier.verify.assert_awaited_once()
            environment.stop.assert_awaited_once_with(delete=True)
            agent_factory.assert_not_called()

    def test_failed_replay_is_recorded_but_not_overlay_eligible(self) -> None:
        with tempfile.TemporaryDirectory() as raw_dir:
            root = Path(raw_dir)
            source = self.create_source_trial(root)
            replay_dir, request = prepare_replay(
                source_trial_dir=source,
                output_root=root / "replays",
                replay_id="failed",
            )
            with mock.patch(
                "scripts.replay_deepswe_verifier.run_isolated_verifier",
                new=mock.AsyncMock(side_effect=RuntimeError("verifier unavailable")),
            ):
                with self.assertRaisesRegex(RuntimeError, "verifier unavailable"):
                    asyncio.run(execute_replay(replay_dir, request))

            result = json.loads((replay_dir / "replay_result.json").read_text())
            overlay = json.loads((replay_dir / "overlay.json").read_text())
            self.assertEqual(result["status"], "failed")
            self.assertEqual(result["exception"]["type"], "RuntimeError")
            self.assertFalse(overlay["eligible"])

    def test_overlay_requires_clean_binary_reward(self) -> None:
        request = {
            "source": {
                "trial_dir": "/source/trial",
                "trial_name": "trial",
                "task_name": "datacurve/task",
                "config_sha256": "config",
                "result_sha256": "result",
                "infra_reason": "negative-reward:-1",
            },
            "model_patch": {"sha256": "patch"},
            "task": {"tree_sha256": "task", "tests_tree_sha256": "tests"},
            "runner": {"commit": "commit"},
        }
        overlay = build_overlay(
            request=request,
            replay_result_path=Path("/replay/result.json"),
            verifier_result={"rewards": {"reward": -1}},
            exception=None,
        )
        self.assertFalse(overlay["eligible"])


if __name__ == "__main__":
    unittest.main()
