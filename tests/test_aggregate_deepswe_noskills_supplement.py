import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from harbor.models.task.task import Task

from scripts.aggregate_deepswe_noskills_supplement import (
    AggregationError,
    aggregate,
    write_report,
)


class BaselineFixture:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.dataset = root / "dataset"
        self.legacy_repo = root / "legacy-repo"
        self.legacy_per_task = root / "legacy" / "per_task.jsonl"
        self.legacy_summary = root / "legacy" / "summary.json"
        self.supplement_job = root / "supplement-job"
        self.out_dir = root / "out"
        self.tasks = {
            slug: self._make_task(slug)
            for slug in ("legacy-task", "igel-task", "gap-task")
        }
        self._make_legacy_inputs()
        self._make_supplement_job()

    def _make_task(self, slug: str) -> Task:
        task_dir = self.dataset / slug
        task_dir.mkdir(parents=True)
        (task_dir / "instruction.md").write_text(f"Fix {slug}.\n")
        (task_dir / "task.toml").write_text(
            f'schema_version = "1.1"\n[task]\nname = "datacurve/{slug}"\n'
        )
        return Task(task_dir)

    @staticmethod
    def _result(
        task: Task,
        *,
        trial_name: str,
        reward: int | float,
        exception_type: str | None = None,
    ) -> dict:
        return {
            "task_name": task.name,
            "trial_name": trial_name,
            "task_checksum": task.checksum,
            "agent_info": {
                "model_info": {
                    "provider": "novita",
                    "name": "zai-org/glm-5.2",
                }
            },
            "config": {
                "agent": {
                    "model_name": "novita/zai-org/glm-5.2",
                    "skills": [],
                    "kwargs": {"provider_name": "novita", "use_skills": False},
                },
                "extra_instruction_paths": [],
            },
            "verifier_result": {"rewards": {"reward": reward}},
            "exception_info": (
                {"exception_type": exception_type, "exception_message": "model outcome"}
                if exception_type
                else None
            ),
        }

    def _write_legacy_result(
        self,
        slug: str,
        *,
        reward: int | float,
        exception_type: str | None = None,
    ) -> tuple[str, Path]:
        trial_name = f"{slug}__legacy"
        relative = Path("traces") / "deepswe" / slug / trial_name
        result_path = self.legacy_repo / relative / "result.json"
        result_path.parent.mkdir(parents=True)
        result_path.write_text(
            json.dumps(
                self._result(
                    self.tasks[slug],
                    trial_name=trial_name,
                    reward=reward,
                    exception_type=exception_type,
                )
            )
        )
        return trial_name, relative

    def _make_legacy_inputs(self) -> None:
        self.legacy_per_task.parent.mkdir(parents=True)
        legacy_trial, legacy_relative = self._write_legacy_result(
            "legacy-task",
            reward=0,
            exception_type="AgentTimeoutError",
        )
        # This is the historical igel shape: the raw verifier result is -1. The
        # supplement must override it; no timing heuristic may map it to zero.
        igel_trial, igel_relative = self._write_legacy_result(
            "igel-task",
            reward=-1,
        )
        rows = [
            {
                "task": "legacy-task",
                "reward": 1,
                "trials": [
                    {
                        "trial": legacy_trial,
                        "job": "old-job",
                        "trace_path": str(legacy_relative),
                        "reward": 1,
                    }
                ],
            },
            {
                "task": "igel-task",
                "reward": 0,
                "trials": [
                    {
                        "trial": igel_trial,
                        "job": "old-job",
                        "trace_path": str(igel_relative),
                        "reward": 0,
                    }
                ],
            },
            {"task": "gap-task", "reward": None, "trials": []},
        ]
        self.legacy_per_task.write_text("".join(json.dumps(row) + "\n" for row in rows))
        self.legacy_summary.write_text(
            json.dumps(
                {
                    "dataset": "deepswe",
                    "full_set_size": 3,
                    "n_passed": 999,
                    "pass_rate_full": 999.0,
                    "aggregation": "best-of-N with a duration heuristic",
                    "trace_dir": "traces/deepswe",
                }
            )
        )

    def _make_supplement_job(self) -> None:
        self.supplement_job.mkdir()
        (self.supplement_job / "config.json").write_text(
            json.dumps(
                {
                    "n_attempts": 1,
                    "agents": [
                        {
                            "model_name": "novita/zai-org/glm-5.2",
                            "skills": [],
                            "kwargs": {
                                "provider_name": "novita",
                                "benchmark_name": "deepswe",
                                "use_skills": False,
                                "resume_contract": {"skills": {"enabled": False}},
                            },
                        }
                    ],
                    "datasets": [{"task_names": ["igel-task", "gap-task"]}],
                }
            )
        )
        (self.supplement_job / "result.json").write_text(
            json.dumps(
                {
                    "n_total_trials": 2,
                    "finished_at": "2026-07-16T00:00:00Z",
                }
            )
        )
        self.write_supplement_result("igel-task", reward=1)
        self.write_supplement_result("gap-task", reward=0)

    def write_supplement_result(
        self,
        slug: str,
        *,
        reward: int | float,
        dirname: str | None = None,
        exception_type: str | None = None,
    ) -> Path:
        trial_name = dirname or f"{slug}__supplement"
        result_path = self.supplement_job / trial_name / "result.json"
        result_path.parent.mkdir(parents=True, exist_ok=True)
        result_path.write_text(
            json.dumps(
                self._result(
                    self.tasks[slug],
                    trial_name=trial_name,
                    reward=reward,
                    exception_type=exception_type,
                )
            )
        )
        return result_path

    def run(self) -> dict:
        return aggregate(
            legacy_per_task_path=self.legacy_per_task,
            legacy_summary_path=self.legacy_summary,
            legacy_trace_repo_root=self.legacy_repo,
            supplement_job_dir=self.supplement_job,
            dataset_dir=self.dataset,
            expected_tasks=3,
            expected_supplement_tasks=2,
        )


class AggregateDeepSweNoSkillsSupplementTest(unittest.TestCase):
    def fixture(self, root: Path) -> BaselineFixture:
        return BaselineFixture(root)

    def test_reads_raw_results_and_supplement_overrides_legacy_invalid_result(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            fixture = self.fixture(Path(tmp))
            report = fixture.run()
            rows = {row["task"]: row for row in report["tasks"]}

            # The index claims reward=1, but the authoritative result says 0.
            self.assertEqual(rows["legacy-task"]["reward"], 0)
            self.assertEqual(rows["legacy-task"]["source"], "legacy_trace")
            self.assertEqual(
                rows["legacy-task"]["exception_type"],
                "AgentTimeoutError",
            )
            # The old raw igel result is -1 and is intentionally never selected.
            self.assertEqual(rows["igel-task"]["reward"], 1)
            self.assertEqual(rows["igel-task"]["source"], "supplement_job")
            self.assertEqual(rows["gap-task"]["reward"], 0)
            self.assertEqual(report["overall"]["n_tasks"], 3)
            self.assertEqual(report["overall"]["n_passed"], 1)
            self.assertEqual(
                report["overall"]["source_counts"],
                {"legacy_trace": 1, "supplement_job": 2},
            )
            self.assertEqual(report["overall"]["n_execution_profiles"], 1)
            self.assertEqual(report["overall"]["execution_profiles"][0]["n_tasks"], 3)
            self.assertTrue(
                report["aggregation_policy"]["execution_profile_homogeneous"]
            )
            for row in rows.values():
                self.assertTrue(row["execution_profile_sha256"])
                self.assertIn("agent", row["execution_profile"])
            self.assertFalse(
                report["aggregation_policy"]["legacy_per_task_reward_used"]
            )
            self.assertFalse(report["aggregation_policy"]["best_of_n"])
            self.assertFalse(report["aggregation_policy"]["duration_heuristics"])

            for row in rows.values():
                result_path = Path(row["result_path"])
                self.assertEqual(
                    row["result_sha256"],
                    hashlib.sha256(result_path.read_bytes()).hexdigest(),
                )
                self.assertEqual(
                    row["task_checksum"],
                    fixture.tasks[row["task"]].checksum,
                )

            outputs = write_report(report, fixture.out_dir)
            self.assertEqual(
                len((fixture.out_dir / "per_task.jsonl").read_text().splitlines()),
                3,
            )
            self.assertNotIn(
                "tasks",
                json.loads((fixture.out_dir / "summary.json").read_text()),
            )
            self.assertEqual(
                json.loads((fixture.out_dir / "score_report.json").read_text())[
                    "overall"
                ]["n_passed"],
                1,
            )
            self.assertEqual(
                outputs["score_report"], str(fixture.out_dir / "score_report.json")
            )

    def test_rejects_multiple_legacy_trials_instead_of_best_of_n(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            fixture = self.fixture(Path(tmp))
            rows = [
                json.loads(line)
                for line in fixture.legacy_per_task.read_text().splitlines()
            ]
            rows[0]["trials"].append(dict(rows[0]["trials"][0]))
            fixture.legacy_per_task.write_text(
                "".join(json.dumps(row) + "\n" for row in rows)
            )

            with self.assertRaisesRegex(AggregationError, "best-of-N"):
                fixture.run()
            self.assertFalse(fixture.out_dir.exists())

    def test_rejects_checksum_mismatch(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            fixture = self.fixture(Path(tmp))
            result_path = next(
                (fixture.legacy_repo / "traces" / "deepswe" / "legacy-task").glob(
                    "*/result.json"
                )
            )
            result = json.loads(result_path.read_text())
            result["task_checksum"] = "not-the-dataset-checksum"
            result_path.write_text(json.dumps(result))

            with self.assertRaisesRegex(AggregationError, "checksum mismatch"):
                fixture.run()

    def test_rejects_infra_exception_even_with_zero_reward(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            fixture = self.fixture(Path(tmp))
            result_path = next(
                (fixture.legacy_repo / "traces" / "deepswe" / "legacy-task").glob(
                    "*/result.json"
                )
            )
            result = json.loads(result_path.read_text())
            result["exception_info"] = {
                "exception_type": "DeepSweVerifierInfraError",
                "exception_message": "missing dependency",
            }
            result_path.write_text(json.dumps(result))

            with self.assertRaisesRegex(AggregationError, "Infra-invalid"):
                fixture.run()

    def test_rejects_non_binary_raw_reward_without_duration_inference(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            fixture = self.fixture(Path(tmp))
            result_path = next(
                (fixture.legacy_repo / "traces" / "deepswe" / "legacy-task").glob(
                    "*/result.json"
                )
            )
            result = json.loads(result_path.read_text())
            result["verifier_result"]["rewards"]["reward"] = -1
            result["started_at"] = "2026-07-16T00:00:00Z"
            result["finished_at"] = "2026-07-16T00:00:01Z"
            result_path.write_text(json.dumps(result))

            with self.assertRaisesRegex(AggregationError, "negative-reward"):
                fixture.run()

    def test_rejects_duplicate_or_missing_supplement_results(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            fixture = self.fixture(Path(tmp))
            fixture.write_supplement_result(
                "gap-task",
                reward=0,
                dirname="gap-task__duplicate",
            )
            with self.assertRaisesRegex(AggregationError, "Duplicate supplement"):
                fixture.run()

        with tempfile.TemporaryDirectory() as tmp:
            fixture = self.fixture(Path(tmp))
            (fixture.supplement_job / "gap-task__supplement" / "result.json").unlink()
            with self.assertRaisesRegex(AggregationError, "do not exactly match"):
                fixture.run()

    def test_rejects_incomplete_dataset_before_writing_outputs(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            fixture = self.fixture(Path(tmp))
            with self.assertRaisesRegex(
                AggregationError, "Dataset task count mismatch"
            ):
                aggregate(
                    legacy_per_task_path=fixture.legacy_per_task,
                    legacy_summary_path=fixture.legacy_summary,
                    legacy_trace_repo_root=fixture.legacy_repo,
                    supplement_job_dir=fixture.supplement_job,
                    dataset_dir=fixture.dataset,
                    expected_tasks=4,
                    expected_supplement_tasks=2,
                )
            self.assertFalse(fixture.out_dir.exists())


if __name__ == "__main__":
    unittest.main()
