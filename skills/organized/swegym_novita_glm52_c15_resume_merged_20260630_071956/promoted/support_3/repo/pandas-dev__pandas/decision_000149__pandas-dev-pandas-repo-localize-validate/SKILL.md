---
name: "pandas-dev-pandas-repo-localize-validate"
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
source_decision_index: 149
repo: "pandas-dev__pandas"
confidence: 0.95
proxy_reward: 1.0
official_skill_path: "/vePFS-Mindverse/user/intern/jungao/SKILLS_EVO/skills/accepted/v0100/_repos/pandas-dev__pandas/candidate/pandas-dev-pandas-repo-localize-validate/SKILL.md"
---

# pandas-dev-pandas-repo-localize-validate

## Status

- Status: `promoted`
- Decision: `promote`
- Candidate decision: `accept`
- Support: `4` `verifier_positive_task_events`; bucket `support_3`
- Reason: candidate passed deterministic verifier-proxy gate
- Official promoted path: `/vePFS-Mindverse/user/intern/jungao/SKILLS_EVO/skills/accepted/v0100/_repos/pandas-dev__pandas/candidate/pandas-dev-pandas-repo-localize-validate/SKILL.md`

## Trigger

current task is in repo pandas-dev__pandas; public trace matches repeated owner paths or adjacent modules; focused validation resembles repeated test commands

## Evidence Gate

Use only when current public evidence independently matches the repo cluster; do not copy a source-task patch.

## Actions

1. Start localization from the repeated owner paths only after current evidence matches them.
2. Prefer the repeated focused validation command when it matches the current issue.

## Validation Hint

python -m pytest -q pandas/tests/io/json/test_ujson.py::TestNumpyJSONTests::test_array_long_double pandas/tests/io/json/test_ujson.py::TestNumpyJSONTests::test_0d_array 'pandas/tests/io/json/test_ujson.py::TestNumpyJSONTests::test_bool[False]' 'pandas/tests/io

## Stop Condition

Stop using this repo candidate if the current traceback, symbol, path, or focused test does not match the cluster evidence.

## Support Summary

5 task events; 4 verifier-positive events; paths=pandas, pandas/core/internals/managers.py; tests=python -m pytest -q pandas/tests/io/json/test_ujson.py::TestNumpyJSONTests::test_array_long_double pandas/tests/io/json/test_ujson.py::TestNumpyJSONTests::test_0d_array 'pandas/tests/io/json/test_ujson.py::TestNumpyJSONTests::test_bool[False]' 'pandas/tests/io; failures=none

## Evaluator Summary

candidate passed deterministic verifier-proxy gate
