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
    _DEEPSWE_MODEL_PATCH_POSTPROCESS_SCRIPT,
    _download_deepswe_artifacts_with_retry,
    _deepswe_official_test_patch_paths,
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


if __name__ == "__main__":
    unittest.main()
