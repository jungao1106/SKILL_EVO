import hashlib
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from scripts.run_benchmark import (
    DEEPSWE_ARTIFACT_DOWNLOAD_ATTEMPTS,
    DEEPSWE_TRANSIENT_RETRY_EXCEPTIONS,
    DeepSweAgentSetupInfraError,
    DeepSweArtifactDownloadError,
    DeepSweVerifierInfraError,
    _DEEPSWE_MODEL_PATCH_POSTPROCESS_SCRIPT,
    _DEEPSWE_VERIFIER_TEST_ERRATA,
    _apply_deepswe_verifier_test_errata,
    _deepswe_fresh_environment_verifier_retry,
    _download_deepswe_artifacts_with_retry,
    _deepswe_official_test_patch_paths,
    _ensure_deepswe_agent_logs_before_isolation,
    _move_deepswe_verification_to_fresh_environment,
    _preserve_go_build_events_in_raw_log,
    _required_deepswe_artifact_paths,
)


class DeepSweModelPatchPostprocessTest(unittest.TestCase):
    def git(self, app_dir: Path, *args: str, input_text: str | None = None) -> str:
        result = subprocess.run(
            ["git", *args],
            cwd=app_dir,
            input=input_text,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=True,
        )
        return result.stdout.strip()

    def write(self, root: Path, rel: str, content: str) -> None:
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")

    def test_restores_pristine_head_and_removes_all_dropped_tests(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            app_dir = root / "app"
            logs_dir = root / "logs"
            app_dir.mkdir()
            (logs_dir / "agent").mkdir(parents=True)
            (logs_dir / "artifacts").mkdir(parents=True)

            self.git(app_dir, "init", "-q")
            self.git(app_dir, "config", "user.name", "DeepSWE Test")
            self.git(app_dir, "config", "user.email", "deepswe@example.invalid")
            self.write(app_dir, "src/base.py", "BASE = True\n")
            self.write(app_dir, "bandit/core/test_set.py", "production base\n")
            self.write(app_dir, "tests/existing_test.py", "base test\n")
            self.write(app_dir, ".gitignore", "tests/ignored_test.py\ncache/\n")
            self.write(app_dir, "cache/preexisting.bin", "preinstalled cache\n")
            self.git(app_dir, "add", "-A")
            self.git(app_dir, "commit", "-q", "-m", "base")
            base = self.git(app_dir, "rev-parse", "HEAD")

            # Match the runner's initial image snapshot: it can contain files
            # that are not in the benchmark base commit.
            self.write(app_dir, "image-state.txt", "template state\n")
            self.git(app_dir, "add", "-A")
            tree = self.git(app_dir, "write-tree")
            baseline = self.git(
                app_dir,
                "commit-tree",
                tree,
                "-p",
                base,
                input_text="initial snapshot\n",
            )
            (logs_dir / "agent" / "deepswe_baseline_commit").write_text(
                baseline + "\n", encoding="utf-8"
            )
            (logs_dir / "agent" / "deepswe_baseline_ignored.json").write_text(
                json.dumps(["cache/preexisting.bin"]), encoding="utf-8"
            )
            self.git(app_dir, "reset", "-q")

            # Exercise both collision modes seen in real trials: a committed
            # agent test is restorable from final HEAD, while an uncommitted
            # test survives `git reset --hard` unless explicitly removed.
            self.write(app_dir, "src/feature.py", "FEATURE = True\n")
            self.write(
                app_dir,
                ".gitignore",
                "tests/ignored_test.py\ncache/\ndynamic/\n",
            )
            self.write(
                app_dir,
                "bandit/core/test_set.py",
                "production implementation\n",
            )
            self.write(app_dir, "tests/committed_test.py", "agent committed test\n")
            self.write(app_dir, "tests/existing_test.py", "agent replacement\n")
            self.git(app_dir, "add", "-A")
            self.git(app_dir, "commit", "-q", "-m", "agent changes")
            self.write(app_dir, "src/untracked_feature.py", "UNTRACKED = True\n")
            self.write(app_dir, "tests/untracked_test.py", "agent untracked test\n")
            self.write(app_dir, "tests/ignored_test.py", "agent ignored test\n")
            self.write(app_dir, "cache/agent-created.bin", "new ignored cache\n")
            self.write(app_dir, "dynamic/hidden.py", "HIDDEN = True\n")

            env = os.environ.copy()
            env.update(
                {
                    "DEEPSWE_APP_DIR": str(app_dir),
                    "DEEPSWE_LOGS_DIR": str(logs_dir),
                    "DEEPSWE_BASE_COMMIT": base,
                    "DEEPSWE_OFFICIAL_TEST_PATHS_JSON": json.dumps(
                        [
                            "tests/existing_test.py",
                            "tests/committed_test.py",
                            "tests/untracked_test.py",
                            "tests/ignored_test.py",
                        ]
                    ),
                }
            )
            result = subprocess.run(
                [sys.executable, "-c", _DEEPSWE_MODEL_PATCH_POSTPROCESS_SCRIPT],
                env=env,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=True,
            )

            self.assertIn("excluded test files from model.patch", result.stdout)
            model_patch = logs_dir / "artifacts" / "model.patch"
            patch_text = model_patch.read_text(encoding="utf-8")
            self.assertIn("src/feature.py", patch_text)
            self.assertIn("src/untracked_feature.py", patch_text)
            self.assertIn("bandit/core/test_set.py", patch_text)
            self.assertNotIn("committed_test.py", patch_text)
            self.assertNotIn("untracked_test.py", patch_text)
            self.assertNotIn("existing_test.py", patch_text)
            self.assertNotIn(
                "diff --git a/tests/ignored_test.py",
                patch_text,
            )

            self.assertEqual(baseline, self.git(app_dir, "rev-parse", "HEAD"))
            self.assertEqual(
                "template state\n",
                (app_dir / "image-state.txt").read_text(encoding="utf-8"),
            )
            self.assertEqual(
                "base test\n",
                (app_dir / "tests/existing_test.py").read_text(encoding="utf-8"),
            )
            self.assertFalse((app_dir / "tests/committed_test.py").exists())
            self.assertFalse((app_dir / "tests/untracked_test.py").exists())
            self.assertFalse((app_dir / "tests/ignored_test.py").exists())
            self.assertEqual(
                "preinstalled cache\n",
                (app_dir / "cache/preexisting.bin").read_text(encoding="utf-8"),
            )
            self.assertFalse((app_dir / "cache/agent-created.bin").exists())
            self.assertFalse((app_dir / "dynamic/hidden.py").exists())

            # This is the verifier operation that previously failed with
            # "already exists in working directory".
            official_patch = root / "test.patch"
            official_patch.write_text(
                """\
diff --git a/tests/existing_test.py b/tests/existing_test.py
--- a/tests/existing_test.py
+++ b/tests/existing_test.py
@@ -1 +1 @@
-base test
+official replacement
diff --git a/tests/committed_test.py b/tests/committed_test.py
new file mode 100644
--- /dev/null
+++ b/tests/committed_test.py
@@ -0,0 +1 @@
+official committed test
diff --git a/tests/untracked_test.py b/tests/untracked_test.py
new file mode 100644
--- /dev/null
+++ b/tests/untracked_test.py
@@ -0,0 +1 @@
+official untracked test
diff --git a/tests/ignored_test.py b/tests/ignored_test.py
new file mode 100644
--- /dev/null
+++ b/tests/ignored_test.py
@@ -0,0 +1 @@
+official ignored test
""",
                encoding="utf-8",
            )
            self.git(app_dir, "apply", str(model_patch))
            self.git(app_dir, "apply", str(official_patch))

            self.assertEqual(
                "FEATURE = True\n",
                (app_dir / "src/feature.py").read_text(encoding="utf-8"),
            )
            self.assertEqual(
                "UNTRACKED = True\n",
                (app_dir / "src/untracked_feature.py").read_text(encoding="utf-8"),
            )
            self.assertEqual(
                "production implementation\n",
                (app_dir / "bandit/core/test_set.py").read_text(encoding="utf-8"),
            )
            self.assertEqual(
                "official committed test\n",
                (app_dir / "tests/committed_test.py").read_text(encoding="utf-8"),
            )
            self.assertEqual(
                "official untracked test\n",
                (app_dir / "tests/untracked_test.py").read_text(encoding="utf-8"),
            )
            self.assertEqual(
                "official ignored test\n",
                (app_dir / "tests/ignored_test.py").read_text(encoding="utf-8"),
            )

    def test_extracts_exact_official_patch_paths(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            task_dir = Path(tmp)
            patch_path = task_dir / "tests" / "test.patch"
            patch_path.parent.mkdir()
            patch_path.write_text(
                """\
diff --git a/tests/new test.py b/tests/new test.py
new file mode 100644
--- /dev/null
+++ b/tests/new test.py
@@ -0,0 +1 @@
+official test
""",
                encoding="utf-8",
            )

            self.assertEqual(
                _deepswe_official_test_patch_paths(task_dir),
                ["tests/new test.py"],
            )

    def test_go_build_events_are_logged_before_reporter_filter(self) -> None:
        source = r"""
# The `grep -v '"Action":"build-'` filter remains required by the reporter.
go test -json ./... | grep -v '"Action":"build-' | tee -a "$RUN_LOG" | reporter
go test -json ./pkg \
  | grep -v '"Action":"build-' \
  | tee -a "$RUN_LOG" | reporter
""".lstrip()

        transformed, count = _preserve_go_build_events_in_raw_log(source)

        self.assertEqual(count, 2)
        self.assertEqual(
            transformed.count(
                "| tee -a \"$RUN_LOG\" | grep -v "
                "'\"Action\":\"build-' | reporter"
            ),
            2,
        )
        self.assertIn("# The `grep -v", transformed)

    def test_hidden_test_errata_is_checksum_guarded(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            task_dir = Path(tmp) / "example-task"
            tests_dir = Path(tmp) / "prepared-tests"
            tests_dir.mkdir()
            source = "type helper struct{}\nfunc helper() {}\n"
            patch_path = tests_dir / "test.patch"
            patch_path.write_text(source)
            erratum = {
                "path": "test.patch",
                "sha256": hashlib.sha256(source.encode()).hexdigest(),
                "replacements": (("helper", "taskUniqueHelper", 2),),
            }

            with mock.patch.dict(
                _DEEPSWE_VERIFIER_TEST_ERRATA,
                {task_dir.name: erratum},
                clear=True,
            ):
                applied = _apply_deepswe_verifier_test_errata(
                    task_dir,
                    tests_dir,
                )

            self.assertEqual(applied, ["helper->taskUniqueHelper:2"])
            self.assertNotIn("helper", patch_path.read_text())

            patch_path.write_text(source + "// drift\n")
            with (
                mock.patch.dict(
                    _DEEPSWE_VERIFIER_TEST_ERRATA,
                    {task_dir.name: erratum},
                    clear=True,
                ),
                self.assertRaisesRegex(
                    DeepSweVerifierInfraError,
                    "erratum source changed",
                ),
            ):
                _apply_deepswe_verifier_test_errata(task_dir, tests_dir)


class DeepSweArtifactDownloadRetryTest(unittest.IsolatedAsyncioTestCase):
    async def test_retries_required_downloads_in_the_same_trial(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            paths = SimpleNamespace(
                artifacts_dir=root / "artifacts",
                agent_dir=root / "agent",
            )
            trial = SimpleNamespace(
                _trial_paths=paths,
                config=SimpleNamespace(
                    agent=SimpleNamespace(
                        import_path="agents.claude_sdk_agent:ClaudeSdkAgent"
                    )
                ),
                _are_agent_logs_downloaded=True,
                _logger=mock.Mock(),
            )
            calls = {"artifacts": 0, "agent_logs": 0}

            async def download_agent_logs(*, source_dir: str, target_dir: Path) -> None:
                calls["agent_logs"] += 1
                self.assertEqual(source_dir, "/logs/agent")
                self.assertEqual(target_dir, paths.agent_dir)
                target_dir.mkdir(parents=True, exist_ok=True)
                for name in (
                    "claude-agent-metadata.json",
                    "claude-agent-sdk-result.json",
                    "claude-agent-sdk.jsonl",
                ):
                    (target_dir / name).write_text("{}")

            async def download_artifacts(current_trial: object) -> None:
                self.assertIs(current_trial, trial)
                calls["artifacts"] += 1
                if calls["artifacts"] == DEEPSWE_ARTIFACT_DOWNLOAD_ATTEMPTS:
                    paths.artifacts_dir.mkdir(parents=True, exist_ok=True)
                    (paths.artifacts_dir / "model.patch").write_bytes(b"")

            trial._maybe_download_logs = download_agent_logs
            missing = await _download_deepswe_artifacts_with_retry(
                trial,
                download_artifacts,
                result_only=False,
                retry_delay_sec=0,
            )

            self.assertEqual(missing, [])
            self.assertEqual(
                calls,
                {
                    "artifacts": DEEPSWE_ARTIFACT_DOWNLOAD_ATTEMPTS,
                    "agent_logs": DEEPSWE_ARTIFACT_DOWNLOAD_ATTEMPTS - 1,
                },
            )
            self.assertFalse(trial._are_agent_logs_downloaded)
            self.assertTrue(
                all(
                    path.is_file()
                    for path in _required_deepswe_artifact_paths(
                        trial,
                        result_only=False,
                    )
                )
            )

    async def test_exhaustion_stays_fail_closed_and_is_not_model_retryable(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            trial = SimpleNamespace(
                _trial_paths=SimpleNamespace(
                    artifacts_dir=root / "artifacts",
                    agent_dir=root / "agent",
                ),
                config=SimpleNamespace(
                    agent=SimpleNamespace(import_path="agents.pi_agent:PiAgent")
                ),
                _are_agent_logs_downloaded=True,
                _logger=mock.Mock(),
            )

            async def no_agent_logs(**_kwargs: object) -> None:
                return None

            async def no_artifacts(_trial: object) -> None:
                return None

            trial._maybe_download_logs = no_agent_logs
            missing = await _download_deepswe_artifacts_with_retry(
                trial,
                no_artifacts,
                result_only=False,
                retry_delay_sec=0,
            )

            self.assertEqual(len(missing), 3)
            self.assertNotIn(
                DeepSweArtifactDownloadError.__name__,
                DEEPSWE_TRANSIENT_RETRY_EXCEPTIONS,
            )

    def test_baseline_setup_failure_is_a_retryable_pre_model_error(self) -> None:
        self.assertIn(
            DeepSweAgentSetupInfraError.__name__,
            DEEPSWE_TRANSIENT_RETRY_EXCEPTIONS,
        )


class DeepSweVerifierRetryPolicyTest(unittest.IsolatedAsyncioTestCase):
    async def test_deepswe_timeout_retries_same_patch_in_fresh_environment(self) -> None:
        from harbor.trial.trial import VerifierTimeoutError

        calls: list[str] = []

        async def verify_once(_trial: object) -> None:
            calls.append("once")
            if len(calls) == 1:
                raise VerifierTimeoutError("verifier timed out")
            _trial.result.verifier_result = SimpleNamespace(  # type: ignore[attr-defined]
                rewards={"reward": 0}
            )

        async def verify_with_retry(_trial: object) -> None:
            calls.append("retry-wrapper")

        verify_with_retry.__wrapped__ = verify_once  # type: ignore[attr-defined]

        with tempfile.TemporaryDirectory() as tmp:
            task_dir = Path(tmp)
            (task_dir / "pre_artifacts.sh").write_text("#!/bin/sh\n")
            trial = SimpleNamespace(
                _task=SimpleNamespace(
                    paths=SimpleNamespace(task_dir=task_dir),
                ),
                _logger=mock.Mock(),
                _skills_evo_deepswe_fresh_verifier_environment=True,
                result=SimpleNamespace(verifier_result=None),
            )
            with (
                mock.patch(
                    "scripts.run_benchmark."
                    "_download_deepswe_verifier_logs_best_effort",
                    new_callable=mock.AsyncMock,
                ) as download_logs,
                mock.patch(
                    "scripts.run_benchmark."
                    "_replace_deepswe_verifier_environment",
                    new_callable=mock.AsyncMock,
                ) as replace_environment,
            ):
                patched = _deepswe_fresh_environment_verifier_retry(
                    verify_with_retry
                )
                await patched(trial)

            download_logs.assert_awaited_once_with(
                trial,
                label="timeout_attempt_1",
            )
            replace_environment.assert_awaited_once_with(
                trial,
                retry_index=1,
                stop_current=True,
            )
        self.assertEqual(calls, ["once", "once"])

    async def test_negative_reward_retries_same_patch_in_fresh_environment(self) -> None:
        calls = 0

        async def verify_once(trial: object) -> None:
            nonlocal calls
            calls += 1
            trial.result.verifier_result = SimpleNamespace(  # type: ignore[attr-defined]
                rewards={"reward": -1 if calls == 1 else 0}
            )

        async def verify_with_retry(_trial: object) -> None:
            raise AssertionError("Harbor retry wrapper should not run")

        verify_with_retry.__wrapped__ = verify_once  # type: ignore[attr-defined]
        with tempfile.TemporaryDirectory() as tmp:
            task_dir = Path(tmp)
            (task_dir / "pre_artifacts.sh").write_text("#!/bin/sh\n")
            trial = SimpleNamespace(
                _task=SimpleNamespace(
                    paths=SimpleNamespace(task_dir=task_dir),
                ),
                _logger=mock.Mock(),
                _skills_evo_deepswe_fresh_verifier_environment=True,
                result=SimpleNamespace(verifier_result=None),
            )
            with (
                mock.patch(
                    "scripts.run_benchmark."
                    "_download_deepswe_verifier_logs_best_effort",
                    new_callable=mock.AsyncMock,
                ) as download_logs,
                mock.patch(
                    "scripts.run_benchmark."
                    "_replace_deepswe_verifier_environment",
                    new_callable=mock.AsyncMock,
                ) as replace_environment,
            ):
                patched = _deepswe_fresh_environment_verifier_retry(
                    verify_with_retry
                )
                await patched(trial)

            download_logs.assert_awaited_once_with(
                trial,
                label="negative_reward_attempt_1",
            )
            replace_environment.assert_awaited_once_with(
                trial,
                retry_index=1,
                stop_current=True,
            )
        self.assertEqual(calls, 2)

    async def test_non_deepswe_preserves_harbor_retry_wrapper(self) -> None:
        calls: list[str] = []

        async def verify_once(_trial: object) -> None:
            calls.append("once")

        async def verify_with_retry(_trial: object) -> None:
            calls.append("retry-wrapper")

        verify_with_retry.__wrapped__ = verify_once  # type: ignore[attr-defined]
        trial = SimpleNamespace(
            _task=SimpleNamespace(
                paths=SimpleNamespace(task_dir=Path("/does/not/exist")),
            )
        )

        patched = _deepswe_fresh_environment_verifier_retry(verify_with_retry)
        await patched(trial)

        self.assertEqual(calls, ["retry-wrapper"])

    async def test_deepswe_verifier_uses_fresh_offline_environment(self) -> None:
        from harbor.models.task.config import EnvironmentConfig as TaskEnvironmentConfig
        from harbor.models.trial.config import (
            EnvironmentConfig as TrialEnvironmentConfig,
        )

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            task_dir = root / "task"
            environment_dir = task_dir / "environment"
            environment_dir.mkdir(parents=True)
            (task_dir / "pre_artifacts.sh").write_text("#!/bin/sh\n")
            (task_dir / "task.toml").write_text(
                """\
[metadata]
base_commit_hash = "abc123"
[verifier]
environment_mode = "separate"
[verifier.environment]
cpus = 2
memory_mb = 8192
storage_mb = 20480
allow_internet = false
"""
            )
            paths = SimpleNamespace(
                trial_dir=root / "trial",
                artifacts_dir=root / "trial" / "artifacts",
                agent_dir=root / "trial" / "agent",
                verifier_dir=root / "trial" / "verifier",
            )

            async def download_patch(*, source_path: str, target_path: Path) -> None:
                self.assertEqual(source_path, "/logs/artifacts/model.patch")
                target_path.write_text("diff --git a/a b/a\n")

            agent_environment = SimpleNamespace(
                download_file=mock.AsyncMock(side_effect=download_patch),
                stop=mock.AsyncMock(),
            )
            verifier_environment = SimpleNamespace(
                default_user=None,
                start=mock.AsyncMock(),
                stop=mock.AsyncMock(),
                exec=mock.AsyncMock(
                    return_value=SimpleNamespace(return_code=0)
                ),
                upload_file=mock.AsyncMock(),
            )
            trial_environment = TrialEnvironmentConfig(
                import_path="environments.e2b_swebench:E2BSwebenchEnvironment",
                env={"NOVITA_API_KEY": "secret"},
                kwargs={
                    "force_allow_internet": True,
                    "template_namespace": "test-team",
                    "pi_template_suffix": "agent-augmented",
                },
            )
            task_environment = TaskEnvironmentConfig(
                cpus=1,
                memory_mb=2048,
                storage_mb=10240,
                allow_internet=True,
                mcp_servers=[],
                skills_dir="/agent/skills",
            )
            trial = SimpleNamespace(
                _environment=agent_environment,
                _trial_paths=paths,
                _task=SimpleNamespace(
                    name="datacurve/example",
                    paths=SimpleNamespace(
                        task_dir=task_dir,
                        environment_dir=environment_dir,
                    ),
                    config=SimpleNamespace(
                        environment=task_environment,
                        verifier=SimpleNamespace(user="verifier"),
                    ),
                ),
                config=SimpleNamespace(
                    trial_name="example__trial",
                    environment=trial_environment,
                    agent=SimpleNamespace(import_path="agents.unknown:Agent"),
                    environment_build_timeout_multiplier=None,
                    timeout_multiplier=1.0,
                ),
                _logger=mock.Mock(),
            )

            with mock.patch(
                "harbor.environments.factory.EnvironmentFactory."
                "create_environment_from_config",
                return_value=verifier_environment,
            ) as create_environment:
                await _move_deepswe_verification_to_fresh_environment(trial)

            agent_environment.stop.assert_awaited_once_with(delete=True)
            self.assertIs(trial._environment, verifier_environment)
            verifier_environment.start.assert_awaited_once_with(force_build=False)
            verifier_environment.upload_file.assert_awaited_once_with(
                source_path=paths.artifacts_dir / "model.patch",
                target_path="/logs/artifacts/model.patch",
            )
            self.assertEqual(verifier_environment.default_user, "verifier")

            call = create_environment.call_args.kwargs
            self.assertEqual(call["session_id"], "example__trial-verifier")
            self.assertEqual(call["config"].env, {})
            self.assertFalse(call["config"].kwargs["force_allow_internet"])
            self.assertEqual(call["config"].kwargs["pi_template_suffix"], "")
            self.assertEqual(call["task_env_config"].cpus, 2)
            self.assertEqual(call["task_env_config"].memory_mb, 8192)
            self.assertEqual(call["task_env_config"].storage_mb, 20480)
            self.assertFalse(call["task_env_config"].allow_internet)
            self.assertEqual(call["task_env_config"].mcp_servers, [])
            self.assertIsNone(call["task_env_config"].skills_dir)

    async def test_agent_logs_are_retried_before_agent_sandbox_is_removed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            paths = SimpleNamespace(
                artifacts_dir=root / "artifacts",
                agent_dir=root / "agent",
            )
            calls = 0

            async def download_logs(**_kwargs: object) -> None:
                nonlocal calls
                calls += 1
                if calls < 2:
                    return
                paths.agent_dir.mkdir(parents=True, exist_ok=True)
                for name in (
                    "claude-agent-metadata.json",
                    "claude-agent-sdk-result.json",
                    "claude-agent-sdk.jsonl",
                ):
                    (paths.agent_dir / name).write_text("{}")

            trial = SimpleNamespace(
                _trial_paths=paths,
                config=SimpleNamespace(
                    agent=SimpleNamespace(
                        import_path="agents.claude_sdk_agent:ClaudeSdkAgent"
                    )
                ),
                _are_agent_logs_downloaded=True,
                _maybe_download_logs=mock.AsyncMock(side_effect=download_logs),
            )
            with mock.patch("scripts.run_benchmark.asyncio.sleep", mock.AsyncMock()):
                await _ensure_deepswe_agent_logs_before_isolation(
                    trial,
                    result_only=False,
                )

            self.assertEqual(calls, 2)
            self.assertTrue(
                all(
                    path.is_file()
                    for path in _required_deepswe_artifact_paths(
                        trial,
                        result_only=False,
                    )[1:]
                )
            )


if __name__ == "__main__":
    unittest.main()
