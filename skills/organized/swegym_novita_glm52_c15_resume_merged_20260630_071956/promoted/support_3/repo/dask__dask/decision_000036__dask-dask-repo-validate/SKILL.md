---
name: "dask-dask-repo-validate"
description: "promoted repo skill materialized from verifier-gated training evidence."
active: true
status: "promoted"
level: "repo"
decision: "promote"
candidate_decision: "accept"
support_bucket: 3
support_count: 3
support_unit: "verifier_positive_task_events"
run: "swegym_novita_glm52_c15_resume_merged_20260630_071956"
source_decision_index: 36
repo: "dask__dask"
confidence: 0.91
proxy_reward: 0.96
official_skill_path: "/vePFS-Mindverse/user/intern/jungao/SKILLS_EVO/skills/accepted/v0100/_repos/dask__dask/candidate/dask-dask-repo-validate/SKILL.md"
---

# dask-dask-repo-validate

## Status

- Status: `promoted`
- Decision: `promote`
- Candidate decision: `accept`
- Support: `3` `verifier_positive_task_events`; bucket `support_3`
- Reason: candidate passed deterministic verifier-proxy gate
- Official promoted path: `/vePFS-Mindverse/user/intern/jungao/SKILLS_EVO/skills/accepted/v0100/_repos/dask__dask/candidate/dask-dask-repo-validate/SKILL.md`

## Trigger

current task is in repo dask__dask; focused validation resembles repeated test commands

## Evidence Gate

Use only when current public evidence independently matches the repo cluster; do not copy a source-task patch.

## Actions

1. Prefer the repeated focused validation command when it matches the current issue.

## Validation Hint

cd /testbed && python -m pytest -q 'dask/dataframe/tests/test_groupby.py::test_cumulative_axis[disk-cumprod]' 'dask/dataframe/tests/test_groupby.py::test_cumulative_axis[tasks-cumprod]' 'dask/dataframe/tests/test_groupby.py::test_cumulative_axis[disk-cumsum]'

## Stop Condition

Stop using this repo candidate if the current traceback, symbol, path, or focused test does not match the cluster evidence.

## Support Summary

5 task events; 3 verifier-positive events; paths=none; tests=cd /testbed && python -m pytest -q 'dask/dataframe/tests/test_groupby.py::test_cumulative_axis[disk-cumprod]' 'dask/dataframe/tests/test_groupby.py::test_cumulative_axis[tasks-cumprod]' 'dask/dataframe/tests/test_groupby.py::test_cumulative_axis[disk-cumsum]'; failures=none

## Evaluator Summary

candidate passed deterministic verifier-proxy gate
