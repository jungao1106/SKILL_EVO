import hashlib
import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

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

from scripts.apply_deepswe_verifier_overlay import apply_overlay
from scripts.run_benchmark import (
    _deepswe_result_infra_reason,
    _sha256_file,
    _sha256_tree,
)


class DeepSweVerifierOverlayApplicationTest(unittest.TestCase):
    def create_fixture(self, root: Path) -> tuple[Path, Path]:
        task_dir = root / "tasks" / "sample-task"
        (task_dir / "environment").mkdir(parents=True)
        (task_dir / "tests").mkdir()
        (task_dir / "instruction.md").write_text("Fix the sample task.\n")
        (task_dir / "pre_artifacts.sh").write_text("#!/bin/sh\n")
        (task_dir / "environment" / "Dockerfile").write_text(
            "FROM example.invalid/base\nWORKDIR /app\n"
        )
        (task_dir / "tests" / "test.sh").write_text("#!/bin/sh\n")
        (task_dir / "tests" / "test.patch").write_text("hidden tests\n")
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
        (trial_dir / "artifacts" / "model.patch").write_bytes(
            b"diff --git a/file.go b/file.go\n"
            b"--- a/file.go\n"
            b"+++ b/file.go\n"
            b"@@ -1 +1 @@\n"
            b"-old\n"
            b"+new\n"
        )
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
            ),
        )
        config_path = trial_dir / "config.json"
        config_path.write_text(config.model_dump_json(indent=2))
        result = TrialResult(
            task_name="datacurve/sample-task",
            trial_name=trial_dir.name,
            trial_uri=trial_dir.resolve().as_uri(),
            task_id=LocalTaskId(path=task_dir),
            task_checksum=Task(task_dir).checksum,
            config=config,
            agent_info=AgentInfo(name="claude-code", version="test"),
            agent_result=AgentContext(
                n_input_tokens=101,
                n_output_tokens=202,
                cost_usd=3.5,
                metadata={"trajectory": "preserve-me", "completed": True},
            ),
            verifier_result=VerifierResult(rewards={"reward": -1}),
            exception_info=ExceptionInfo(
                exception_type="DeepSweVerifierInfraError",
                exception_message="missing verifier dependency",
                exception_traceback="trace",
                occurred_at=datetime.now(timezone.utc),
            ),
        )
        result_path = trial_dir / "result.json"
        result_path.write_text(result.model_dump_json(indent=2))

        replay_dir = root / "replays" / "same-patch"
        replay_dir.mkdir(parents=True)
        replay_patch_path = replay_dir / "artifacts" / "model.patch"
        replay_patch_path.parent.mkdir()
        replay_patch_path.write_bytes(
            (trial_dir / "artifacts" / "model.patch").read_bytes()
        )
        guard = {
            "trial_dir": str(trial_dir.resolve()),
            "trial_name": trial_dir.name,
            "task_name": "datacurve/sample-task",
            "source_config_sha256": _sha256_file(config_path),
            "source_result_sha256": _sha256_file(result_path),
            "source_infra_reason": _deepswe_result_infra_reason(
                json.loads(result_path.read_text())
            ),
            "model_patch_sha256": _sha256_file(trial_dir / "artifacts" / "model.patch"),
            "task_tree_sha256": _sha256_tree(task_dir),
            "tests_tree_sha256": _sha256_tree(task_dir / "tests"),
        }
        verifier_result = {"rewards": {"reward": 1}}
        replay_result_path = replay_dir / "replay_result.json"
        replay_result = {
            "schema_version": 1,
            "kind": "deepswe_verifier_same_patch_replay_result",
            "status": "complete",
            "exception": None,
            "source": {
                "trial_dir": guard["trial_dir"],
                "trial_name": guard["trial_name"],
                "task_name": guard["task_name"],
                "config_sha256": guard["source_config_sha256"],
                "result_sha256": guard["source_result_sha256"],
                "infra_reason": guard["source_infra_reason"],
            },
            "model_patch": {
                "sha256": guard["model_patch_sha256"],
                "replay_path": str(replay_patch_path.resolve()),
            },
            "task": {
                "tree_sha256": guard["task_tree_sha256"],
                "tests_tree_sha256": guard["tests_tree_sha256"],
            },
            "verifier_result": verifier_result,
        }
        replay_result_path.write_text(json.dumps(replay_result, indent=2))
        overlay_path = replay_dir / "overlay.json"
        overlay = {
            "schema_version": 1,
            "kind": "deepswe_trial_verifier_overlay",
            "eligible": True,
            "source_guard": guard,
            "replacement": {
                "verifier_result": verifier_result,
                "exception_info": None,
            },
            "replay_result_path": str(replay_result_path.resolve()),
            "automatic_application": False,
        }
        overlay_path.write_text(json.dumps(overlay, indent=2))
        return trial_dir, overlay_path

    @staticmethod
    def resign_source_result(trial_dir: Path, overlay_path: Path) -> None:
        result_path = trial_dir / "result.json"
        new_hash = _sha256_file(result_path)
        overlay = json.loads(overlay_path.read_text())
        overlay["source_guard"]["source_result_sha256"] = new_hash
        replay_path = Path(overlay["replay_result_path"])
        replay = json.loads(replay_path.read_text())
        replay["source"]["result_sha256"] = new_hash
        replay_path.write_text(json.dumps(replay))
        overlay_path.write_text(json.dumps(overlay))

    def test_applies_only_verifier_fields_and_preserves_original_history(self) -> None:
        with tempfile.TemporaryDirectory() as raw_dir:
            root = Path(raw_dir)
            trial_dir, overlay_path = self.create_fixture(root)
            result_path = trial_dir / "result.json"
            original_bytes = result_path.read_bytes()
            original = json.loads(original_bytes)

            application = apply_overlay(overlay_path)

            updated = json.loads(result_path.read_text())
            expected = dict(original)
            expected["verifier_result"] = {"rewards": {"reward": 1}}
            expected["exception_info"] = None
            self.assertEqual(updated, expected)
            self.assertEqual(updated["agent_info"], original["agent_info"])
            self.assertEqual(updated["agent_result"], original["agent_result"])
            history_path = Path(application["before"]["history_path"])
            self.assertEqual(history_path.read_bytes(), original_bytes)
            self.assertEqual(
                application["before"]["result_sha256"],
                hashlib.sha256(original_bytes).hexdigest(),
            )
            self.assertEqual(
                application["after"]["result_sha256"],
                _sha256_file(result_path),
            )
            self.assertEqual(
                json.loads((overlay_path.parent / "application.json").read_text()),
                application,
            )

    def test_application_is_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as raw_dir:
            root = Path(raw_dir)
            trial_dir, overlay_path = self.create_fixture(root)
            first = apply_overlay(overlay_path)
            result_path = trial_dir / "result.json"
            result_after_first = result_path.read_bytes()
            application_after_first = (
                overlay_path.parent / "application.json"
            ).read_bytes()

            second = apply_overlay(overlay_path)

            self.assertEqual(second, first)
            self.assertEqual(result_path.read_bytes(), result_after_first)
            self.assertEqual(
                (overlay_path.parent / "application.json").read_bytes(),
                application_after_first,
            )

    def test_rejects_old_eligible_overlay_for_offline_toolchain_failure(self) -> None:
        with tempfile.TemporaryDirectory() as raw_dir:
            root = Path(raw_dir)
            trial_dir, overlay_path = self.create_fixture(root)
            verifier_dir = overlay_path.parent / "verifier"
            verifier_dir.mkdir()
            (verifier_dir / "ctrf.json").write_text(
                json.dumps(
                    {
                        "results": {
                            "tests": [
                                {
                                    "name": "scored-test",
                                    "status": "failed",
                                    "message": (
                                        "missing from report (test did not run or "
                                        "produced no result)"
                                    ),
                                }
                            ]
                        }
                    }
                )
            )
            (verifier_dir / "test-stdout.txt").write_text(
                "go: download go1.26.1: "
                "golang.org/toolchain@v0.0.1-go1.26.1.linux-amd64: Get "
                '"https://proxy.golang.org/toolchain.zip": dial tcp: lookup '
                "proxy.golang.org: i/o timeout\n"
            )
            result_path = trial_dir / "result.json"
            before = result_path.read_bytes()

            with self.assertRaisesRegex(
                ValueError,
                "infrastructure-invalid",
            ):
                apply_overlay(overlay_path)

            self.assertEqual(result_path.read_bytes(), before)
            self.assertFalse((overlay_path.parent / "application.json").exists())

    def test_idempotence_fails_closed_after_result_or_application_drift(self) -> None:
        for case in ("result", "application"):
            with self.subTest(case=case), tempfile.TemporaryDirectory() as raw_dir:
                root = Path(raw_dir)
                trial_dir, overlay_path = self.create_fixture(root)
                apply_overlay(overlay_path)
                result_path = trial_dir / "result.json"
                application_path = overlay_path.parent / "application.json"
                if case == "result":
                    result = json.loads(result_path.read_text())
                    result["agent_result"]["metadata"]["trajectory"] = "drifted"
                    result_path.write_text(json.dumps(result))
                else:
                    application = json.loads(application_path.read_text())
                    application["after"]["result_sha256"] = "forged"
                    application_path.write_text(json.dumps(application))
                before = result_path.read_bytes()

                with self.assertRaises((ValueError, RuntimeError)):
                    apply_overlay(overlay_path)

                self.assertEqual(result_path.read_bytes(), before)

    def test_recovers_if_result_update_preceded_application_record(self) -> None:
        with tempfile.TemporaryDirectory() as raw_dir:
            root = Path(raw_dir)
            trial_dir, overlay_path = self.create_fixture(root)
            overlay = json.loads(overlay_path.read_text())
            result_path = trial_dir / "result.json"
            original = json.loads(result_path.read_text())
            history_path = (
                overlay_path.parent
                / "history"
                / f"source_result.{overlay['source_guard']['source_result_sha256']}.json"
            )
            history_path.parent.mkdir()
            history_path.write_bytes(result_path.read_bytes())
            original["verifier_result"] = overlay["replacement"]["verifier_result"]
            original["exception_info"] = None
            result_path.write_text(json.dumps(original))

            application = apply_overlay(overlay_path)

            self.assertTrue(application["recovered_after_interrupted_recording"])
            self.assertEqual(
                application["after"]["result_sha256"], _sha256_file(result_path)
            )

    def test_all_source_hash_guards_fail_closed(self) -> None:
        mutations = {
            "config": lambda trial: (trial / "config.json").write_text(
                (trial / "config.json").read_text() + "\n"
            ),
            "result": lambda trial: (trial / "result.json").write_text(
                (trial / "result.json")
                .read_text()
                .replace('"version": "test"', '"version": "changed"')
            ),
            "patch": lambda trial: (trial / "artifacts" / "model.patch").write_bytes(
                (trial / "artifacts" / "model.patch").read_bytes() + b"changed"
            ),
            "task": lambda trial: (
                Path(json.loads((trial / "config.json").read_text())["task"]["path"])
                / "instruction.md"
            ).write_text("changed task\n"),
            "tests": lambda trial: (
                Path(json.loads((trial / "config.json").read_text())["task"]["path"])
                / "tests"
                / "test.sh"
            ).write_text("changed tests\n"),
        }
        for label, mutate in mutations.items():
            with self.subTest(label=label), tempfile.TemporaryDirectory() as raw_dir:
                root = Path(raw_dir)
                trial_dir, overlay_path = self.create_fixture(root)
                mutate(trial_dir)
                before = (trial_dir / "result.json").read_bytes()

                with self.assertRaises((ValueError, RuntimeError)):
                    apply_overlay(overlay_path)

                self.assertEqual((trial_dir / "result.json").read_bytes(), before)
                self.assertFalse((overlay_path.parent / "application.json").exists())

    def test_rejects_ineligible_identity_and_non_verifier_sources(self) -> None:
        cases = ("ineligible", "identity", "provider")
        for case in cases:
            with self.subTest(case=case), tempfile.TemporaryDirectory() as raw_dir:
                root = Path(raw_dir)
                trial_dir, overlay_path = self.create_fixture(root)
                overlay = json.loads(overlay_path.read_text())
                if case == "ineligible":
                    overlay["eligible"] = False
                elif case == "identity":
                    overlay["source_guard"]["trial_name"] = "different-trial"
                else:
                    result_path = trial_dir / "result.json"
                    result = json.loads(result_path.read_text())
                    result["exception_info"]["exception_type"] = (
                        "DeepSweProviderAuthenticationError"
                    )
                    result_path.write_text(json.dumps(result))
                    overlay_path.write_text(json.dumps(overlay))
                    self.resign_source_result(trial_dir, overlay_path)
                    overlay = json.loads(overlay_path.read_text())
                overlay_path.write_text(json.dumps(overlay))
                before = (trial_dir / "result.json").read_bytes()

                with self.assertRaises((ValueError, RuntimeError)):
                    apply_overlay(overlay_path)

                self.assertEqual((trial_dir / "result.json").read_bytes(), before)

    def test_rejects_forged_or_incomplete_source_result(self) -> None:
        cases = ("embedded-config", "task-checksum", "agent-incomplete")
        for case in cases:
            with self.subTest(case=case), tempfile.TemporaryDirectory() as raw_dir:
                root = Path(raw_dir)
                trial_dir, overlay_path = self.create_fixture(root)
                result_path = trial_dir / "result.json"
                result = json.loads(result_path.read_text())
                if case == "embedded-config":
                    result["config"]["agent"]["model_name"] = "forged/model"
                elif case == "task-checksum":
                    result["task_checksum"] = "forged-task-checksum"
                else:
                    result["agent_result"]["metadata"]["completed"] = False
                result_path.write_text(json.dumps(result))
                self.resign_source_result(trial_dir, overlay_path)
                before = result_path.read_bytes()

                with self.assertRaises((ValueError, RuntimeError)):
                    apply_overlay(overlay_path)

                self.assertEqual(result_path.read_bytes(), before)
                self.assertFalse((overlay_path.parent / "application.json").exists())

    def test_rejects_invalid_replay_sidecar_provenance(self) -> None:
        cases = (
            "automatic",
            "schema",
            "kind",
            "exception",
            "copied-patch-hash",
            "copied-patch-path",
        )
        for case in cases:
            with self.subTest(case=case), tempfile.TemporaryDirectory() as raw_dir:
                root = Path(raw_dir)
                trial_dir, overlay_path = self.create_fixture(root)
                overlay = json.loads(overlay_path.read_text())
                replay_path = Path(overlay["replay_result_path"])
                replay = json.loads(replay_path.read_text())
                if case == "automatic":
                    overlay["automatic_application"] = True
                elif case == "schema":
                    replay["schema_version"] = 2
                elif case == "kind":
                    replay["kind"] = "forged-replay"
                elif case == "exception":
                    replay["exception"] = {
                        "type": "RuntimeError",
                        "message": "failed",
                    }
                elif case == "copied-patch-hash":
                    Path(replay["model_patch"]["replay_path"]).write_bytes(b"forged")
                else:
                    replay["model_patch"]["replay_path"] = str(
                        (trial_dir / "artifacts" / "model.patch").resolve()
                    )
                replay_path.write_text(json.dumps(replay))
                overlay_path.write_text(json.dumps(overlay))
                before = (trial_dir / "result.json").read_bytes()

                with self.assertRaises((ValueError, RuntimeError)):
                    apply_overlay(overlay_path)

                self.assertEqual((trial_dir / "result.json").read_bytes(), before)
                self.assertFalse((overlay_path.parent / "application.json").exists())


if __name__ == "__main__":
    unittest.main()
