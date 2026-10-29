from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from evolution.tts_evolution import materialize_gate_library


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


if __name__ == "__main__":
    unittest.main()
