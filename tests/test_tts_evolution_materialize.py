from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from agents.skill_writer import write_failure_mode_candidate, write_repo_candidate
from evolution.tts_evolution import generate_test_time_decisions, materialize_gate_library


class GateSkillCountTest(unittest.TestCase):
    def test_updated_skill_does_not_inflate_actual_total(self) -> None:
        with tempfile.TemporaryDirectory() as raw_dir:
            root = Path(raw_dir)
            base = root / "base"
            existing = (
                base
                / "_test_time"
                / "repo"
                / "owner__repo"
                / "same-skill"
                / "SKILL.md"
            )
            existing.parent.mkdir(parents=True)
            existing.write_text("old\n")
            output = root / "output"
            decision = {
                "decision": "promote",
                "level": "repo",
                "repo": "owner__repo",
                "candidate": {
                    "name": "same-skill",
                    "level": "repo",
                    "repo": "owner__repo",
                    "description": "updated",
                    "actions": ["Inspect the owner path."],
                },
            }

            manifest = materialize_gate_library(
                base_skill_root=base,
                output_root=output,
                run_name="test",
                promotion_decisions=[decision],
                gate_index=2,
            )

            counts = manifest["skill_counts"]
            self.assertEqual(counts["base"], 1)
            self.assertEqual(counts["test_time_promoted"], 1)
            self.assertEqual(counts["added_this_gate"], 0)
            self.assertEqual(counts["updated_this_gate"], 1)
            self.assertEqual(counts["total"], 1)


class WriterPolicyApplicationTest(unittest.TestCase):
    def setUp(self) -> None:
        self.policy = {
            "rules": [
                "Extract candidate content only from trace-visible owner paths, public commands, public outputs, and explicit stop conditions.",
                "Treat cold-start solved traces as weak-positive local evidence: require same-repo repeated support before drafting an active repo candidate.",
                "For unresolved or diagnostic traces, draft at most 2 recover/validate actions and omit semantic edit instructions.",
            ],
            "update_count": 4,
        }

    def test_repo_writer_policy_changes_candidate_content(self) -> None:
        cluster = {
            "repo": "owner__repo",
            "support_tasks": 5,
            "positive_support": 0,
            "repeated_paths": ["src/owner.py"],
            "repeated_tests": ["pytest tests/test_owner.py"],
            "failure_signature_counts": {"localization-drift": 3},
        }

        baseline = write_repo_candidate(cluster)
        candidate = write_repo_candidate(cluster, writer_policy=self.policy)

        self.assertNotEqual(candidate["trigger"], baseline["trigger"])
        self.assertIn("repo name alone is insufficient", candidate["evidence_gate"].lower())
        self.assertLess(len(candidate["actions"]), len(baseline["actions"]))
        self.assertTrue(candidate["writer_policy_application"]["applied"])
        self.assertEqual(candidate["writer_policy_application"]["update_count"], 4)

    def test_failure_writer_policy_limits_actions(self) -> None:
        cluster = {
            "failure_signature": "localization-drift",
            "repo_support_count": 3,
            "event_support_count": 7,
            "support_repos": ["a__one", "b__two", "c__three"],
        }

        candidate = write_failure_mode_candidate(cluster, writer_policy=self.policy)

        self.assertEqual(len(candidate["actions"]), 2)
        self.assertIn("trace-visible public diagnostic", candidate["evidence_gate"])
        self.assertIn("max_actions=2", candidate["writer_policy_application"]["effects"])

    def test_test_time_generation_consumes_writer_policy(self) -> None:
        evidence_rows = [
            {
                "repo": "owner__repo",
                "task_name": f"owner__repo-{index}",
                "reward": 0.0,
                "case_label": "weak_negative",
                "failure_signature": "localization-drift",
                "touched_paths": ["src/owner.py"],
                "edited_paths": ["src/owner.py"],
                "test_commands": ["pytest tests/test_owner.py"],
            }
            for index in range(2)
        ]

        generated = generate_test_time_decisions(
            evidence_rows=evidence_rows,
            run_name="test",
            writer_policy=self.policy,
            repo_update_batch_size=5,
            repo_min_support=2,
            repo_min_positive_support=0,
        )

        application = generated["repo_candidates"][0]["writer_policy_application"]
        self.assertTrue(application["applied"])
        self.assertEqual(application["update_count"], 4)


if __name__ == "__main__":
    unittest.main()
