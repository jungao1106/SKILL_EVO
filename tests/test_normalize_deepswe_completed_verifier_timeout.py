from __future__ import annotations

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

from scripts.normalize_deepswe_completed_verifier_timeout import normalize_trial


class CompletedVerifierTimeoutNormalizationTest(unittest.TestCase):
    def create_trial(self, root: Path) -> Path:
        task_dir = root / "tasks" / "sample"
        (task_dir / "environment").mkdir(parents=True)
        (task_dir / "tests").mkdir()
        (task_dir / "instruction.md").write_text("Fix it.\n")
        (task_dir / "pre_artifacts.sh").write_text("#!/bin/sh\n")
        (task_dir / "tests" / "test.sh").write_text("#!/bin/sh\n")
        (task_dir / "tests" / "test.patch").write_text("tests\n")
        (task_dir / "task.toml").write_text(
            """schema_version = "1.1"
[task]
name = "datacurve/sample"
[verifier]
timeout_sec = 30.0
[environment]
docker_image = "example.invalid/sample"
"""
        )
        job_dir = root / "jobs" / "job"
        trial_dir = job_dir / "sample__abc"
        (trial_dir / "artifacts").mkdir(parents=True)
        (trial_dir / "artifacts" / "model.patch").write_text("diff --git a/a b/a\n")
        config = TrialConfig(
            task=TaskConfig(path=task_dir, source="tasks"),
            trial_name=trial_dir.name,
            trials_dir=job_dir,
            agent=AgentConfig(import_path="agents.claude_sdk_agent:ClaudeSdkAgent"),
            environment=EnvironmentConfig(
                import_path="environments.e2b_swebench:E2BSwebenchEnvironment"
            ),
        )
        (trial_dir / "config.json").write_text(config.model_dump_json(indent=2))
        result = TrialResult(
            task_name="datacurve/sample",
            trial_name=trial_dir.name,
            trial_uri=trial_dir.resolve().as_uri(),
            task_id=LocalTaskId(path=task_dir),
            task_checksum=Task(task_dir).checksum,
            config=config,
            agent_info=AgentInfo(name="claude-code", version="test"),
            agent_result=AgentContext(metadata={"completed": True}),
            verifier_result=None,
            exception_info=ExceptionInfo(
                exception_type="VerifierTimeoutError",
                exception_message="timed out",
                exception_traceback="",
                occurred_at=datetime.now(timezone.utc),
            ),
        )
        (trial_dir / "result.json").write_text(result.model_dump_json(indent=2))
        (job_dir / "result.json").write_text(
            json.dumps({"n_total_trials": 1, "finished_at": "done"})
        )
        for attempt in (1, 2):
            attempt_dir = trial_dir / "verifier_attempts" / f"timeout_attempt_{attempt}"
            attempt_dir.mkdir(parents=True)
            (attempt_dir / "base.xml").write_text(
                '<testsuites><testsuite tests="2" failures="0" errors="0">'
                '<testcase classname="base" name="a"/>'
                '<testcase classname="base" name="b"/>'
                "</testsuite></testsuites>"
            )
            (attempt_dir / "new.xml").write_text(
                '<testsuites><testsuite tests="2" failures="1" errors="0">'
                '<testcase classname="new" name="a"><failure>bad</failure></testcase>'
                '<testcase classname="new" name="b"/>'
                "</testsuite></testsuites>"
            )
        return trial_dir

    def test_normalizes_repeated_completed_failure_and_invalidates_root(self) -> None:
        with tempfile.TemporaryDirectory() as raw_dir:
            trial_dir = self.create_trial(Path(raw_dir))
            source = (trial_dir / "result.json").read_bytes()

            outcome = normalize_trial(trial_dir)

            result = json.loads((trial_dir / "result.json").read_text())
            self.assertEqual(result["verifier_result"], {"rewards": {"reward": 0}})
            self.assertIsNone(result["exception_info"])
            self.assertEqual(
                (trial_dir / "result.pre-terminal-normalization.json").read_bytes(),
                source,
            )
            self.assertEqual(outcome["fresh_verifier_attempts"][0]["new"]["failure"], 1)
            root = json.loads((trial_dir.parent / "result.json").read_text())
            self.assertIsNone(root["finished_at"])
            self.assertEqual(normalize_trial(trial_dir), outcome)

    def test_accepts_different_failures_when_both_attempts_prove_zero(self) -> None:
        with tempfile.TemporaryDirectory() as raw_dir:
            trial_dir = self.create_trial(Path(raw_dir))
            second = (
                trial_dir / "verifier_attempts" / "timeout_attempt_2" / "new.xml"
            )
            second.write_text(second.read_text().replace('name="a"', 'name="c"', 1))

            outcome = normalize_trial(trial_dir)

            attempts = outcome["fresh_verifier_attempts"]
            self.assertNotEqual(
                attempts[0]["new"]["outcomes_sha256"],
                attempts[1]["new"]["outcomes_sha256"],
            )
            self.assertEqual(outcome["assigned_reward"], 0)

    def test_rejects_attempt_that_does_not_prove_zero(self) -> None:
        with tempfile.TemporaryDirectory() as raw_dir:
            trial_dir = self.create_trial(Path(raw_dir))
            second = (
                trial_dir / "verifier_attempts" / "timeout_attempt_2" / "new.xml"
            )
            second.write_text(
                second.read_text()
                .replace('failures="1"', 'failures="0"')
                .replace("<failure>bad</failure>", "")
            )

            with self.assertRaisesRegex(ValueError, "does not prove"):
                normalize_trial(trial_dir)


if __name__ == "__main__":
    unittest.main()
