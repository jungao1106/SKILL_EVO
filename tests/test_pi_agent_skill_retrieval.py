from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from agents.pi_agent import (
    _filter_task_specific_skills,
    _repo_slug_from_instruction,
    _task_filter_text,
)
from agents.claude_sdk_agent import _task_filter_text as _claude_task_filter_text


class TestTimeSkillRetrievalTest(unittest.TestCase):
    def test_transfer_scope_selects_nested_test_time_failure_skills(self) -> None:
        skills = [
            {
                "name": "tts-recovery",
                "relative_path": "_test_time/failure_modes/no-diff/tts/SKILL.md",
            },
            {
                "name": "base-success",
                "relative_path": "_success_patterns/base/SKILL.md",
            },
        ]
        with mock.patch.dict(
            os.environ, {"PI_SKILL_RETRIEVAL_SCOPE": "transfer"}, clear=False
        ):
            selected = _filter_task_specific_skills(skills, "plain DeepSWE issue")

        self.assertEqual(selected[0]["name"], "tts-recovery")
        self.assertIn("base-success", {skill["name"] for skill in selected})

    def test_deepswe_task_metadata_routes_repo_test_time_skill(self) -> None:
        with tempfile.TemporaryDirectory() as raw_dir:
            task_dir = Path(raw_dir) / "task"
            environment_dir = task_dir / "environment"
            environment_dir.mkdir(parents=True)
            (task_dir / "task.toml").write_text(
                """\
[metadata]
repository_url = "https://github.com/PyCQA/bandit.git"
"""
            )
            environment = SimpleNamespace(
                environment_dir=environment_dir,
                environment_name="datacurve/bandit-structured-nosec-directives",
                session_id="trial",
            )
            filter_text = _task_filter_text("DeepSWE issue", environment)
            self.assertEqual(
                _repo_slug_from_instruction(
                    _claude_task_filter_text("DeepSWE issue", environment)
                ),
                "PyCQA__bandit",
            )
            self.assertEqual(
                _repo_slug_from_instruction(filter_text),
                "PyCQA__bandit",
            )

            skills = [
                {
                    "name": "matching-repo",
                    "relative_path": (
                        "_test_time/repo/PyCQA__bandit/matching/SKILL.md"
                    ),
                },
                {
                    "name": "other-repo",
                    "relative_path": "_test_time/repo/encode__httpx/other/SKILL.md",
                },
                {
                    "name": "generic-recovery",
                    "relative_path": (
                        "_test_time/failure_modes/no-diff/recovery/SKILL.md"
                    ),
                },
            ]
            with mock.patch.dict(
                os.environ, {"PI_SKILL_RETRIEVAL_SCOPE": "transfer"}, clear=False
            ):
                selected = _filter_task_specific_skills(skills, filter_text)

            selected_names = [skill["name"] for skill in selected]
            self.assertEqual(selected_names[0], "matching-repo")
            self.assertIn("generic-recovery", selected_names)
            self.assertNotIn("other-repo", selected_names)


if __name__ == "__main__":
    unittest.main()
