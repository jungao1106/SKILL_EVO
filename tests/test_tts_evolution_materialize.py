from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from agents.skill_writer import write_failure_mode_candidate, write_repo_candidate
from evolution.tts_evolution import (
    collect_failed_trace_evidence,
    generate_test_time_decisions,
    materialize_gate_library,
    normalize_tts_evaluation_scope,
    select_tts_evaluation_task_names,
)
from scripts.materialize_swebench_tts_evolution_gates import (
    evaluation_scope_from_args,
    render_eval_launcher,
)


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


class TtsEvaluationScopeTest(unittest.TestCase):
    def test_scope_selects_reward_zero_or_all_tasks(self) -> None:
        report = {
            "tasks": [
                {"task_name": "zero", "reward": 0},
                {"task_name": "partial", "reward": 0.5},
                {"task_name": "solved", "reward": 1},
                {"task_name": "missing", "reward": None},
            ]
        }

        self.assertEqual(normalize_tts_evaluation_scope(), "reward-zero")
        self.assertEqual(
            select_tts_evaluation_task_names(report, "reward-zero"),
            ["zero"],
        )
        self.assertEqual(
            select_tts_evaluation_task_names(report, "all"),
            ["zero", "partial", "solved", "missing"],
        )

    def test_evolution_evidence_remains_exact_zero(self) -> None:
        with tempfile.TemporaryDirectory() as raw_dir:
            root = Path(raw_dir)
            report_path = root / "report.json"
            report_path.write_text(
                json.dumps(
                    {
                        "tasks": [
                            {
                                "task_name": f"owner__repo-{index}",
                                "reward": reward,
                                "result_path": str(root / f"trial-{index}" / "result.json"),
                            }
                            for index, reward in enumerate((0, 0.5, 1, None))
                        ]
                    }
                )
            )

            default_rows = collect_failed_trace_evidence(
                aggregate_report_path=report_path,
            )

        self.assertEqual([row["reward"] for row in default_rows], [0])
        self.assertEqual(
            default_rows[0]["selection_reward_condition"]["expression"],
            "reward == 0",
        )

    def test_existing_run_inherits_and_locks_evaluation_scope(self) -> None:
        manifest = {"parameters": {"evaluation_scope": "all"}}

        self.assertEqual(
            evaluation_scope_from_args(
                SimpleNamespace(evaluation_scope=None),
                manifest,
            ),
            "all",
        )
        with self.assertRaisesRegex(ValueError, "cannot change"):
            evaluation_scope_from_args(
                SimpleNamespace(evaluation_scope="reward-zero"),
                manifest,
            )

    def test_reward_zero_scope_launcher_uses_selected_task_file(self) -> None:
        args = SimpleNamespace(
            python="python",
            task_file_glob="all_tasks_*.txt",
            num_shards=5,
            concurrency_per_shard=10,
            harness="pi",
            provider="openai",
            env_file=Path(".env"),
            agent_timeout_sec=3600,
            agent_setup_timeout_sec=1200,
            e2b_sandbox_timeout_sec=7200,
            provider_base_url=None,
            provider_anthropic_base_url=None,
            provider_model=None,
            provider_api=None,
            claude_max_turns=None,
            claude_max_budget_usd=None,
            materialize_eval_scripts_only=False,
        )

        launcher = render_eval_launcher(
            run_id="test",
            gate_index=1,
            gate_skill_root=Path("skills/gate_001"),
            args=args,
            task_file_glob="selected_reward_zero.txt",
            num_shards=1,
        )

        self.assertIn("--task-file-glob selected_reward_zero.txt", launcher)
        self.assertIn("--num-shards 1", launcher)
        self.assertNotIn("all_tasks_*.txt", launcher)


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
