---
name: "iterative-dvc-repo-validate-recover"
description: "promoted repo skill materialized from verifier-gated training evidence."
active: true
status: "promoted"
level: "repo"
decision: "promote"
candidate_decision: "accept"
support_bucket: 3
support_count: 4
support_unit: "verifier_positive_task_events"
run: "swegym_novita_glm52_c15_resume_merged_20260630_071956"
source_decision_index: 98
repo: "iterative__dvc"
confidence: 0.95
proxy_reward: 1.0
official_skill_path: "/vePFS-Mindverse/user/intern/jungao/SKILLS_EVO/skills/accepted/v0100/_repos/iterative__dvc/candidate/iterative-dvc-repo-validate-recover/SKILL.md"
---

# iterative-dvc-repo-validate-recover

## Status

- Status: `promoted`
- Decision: `promote`
- Candidate decision: `accept`
- Support: `4` `verifier_positive_task_events`; bucket `support_3`
- Reason: candidate passed deterministic verifier-proxy gate
- Official promoted path: `/vePFS-Mindverse/user/intern/jungao/SKILLS_EVO/skills/accepted/v0100/_repos/iterative__dvc/candidate/iterative-dvc-repo-validate-recover/SKILL.md`

## Trigger

current task is in repo iterative__dvc; focused validation resembles repeated test commands; failure signature matches repeated repo failures

## Evidence Gate

Use only when current public evidence independently matches the repo cluster; do not copy a source-task patch.

## Actions

1. Prefer the repeated focused validation command when it matches the current issue.
2. If the repeated failure signature appears, recover before broadening the edit.

## Validation Hint

cd /testbed && python -m pytest -q tests/test_pipeline.py::TestPipelineShowSingle::test_tree tests/test_pipeline.py::TestPipelineShowSingle::test tests/test_pipeline.py::TestPipelineShowDeep::test_ascii tests/test_pipeline.py::TestPipelineShowDeep::test_ascii_

## Stop Condition

Stop using this repo candidate if the current traceback, symbol, path, or focused test does not match the cluster evidence.

## Support Summary

5 task events; 4 verifier-positive events; paths=none; tests=cd /testbed && python -m pytest -q tests/test_pipeline.py::TestPipelineShowSingle::test_tree tests/test_pipeline.py::TestPipelineShowSingle::test tests/test_pipeline.py::TestPipelineShowDeep::test_ascii tests/test_pipeline.py::TestPipelineShowDeep::test_ascii_; failures=localization-drift

## Evaluator Summary

candidate passed deterministic verifier-proxy gate
