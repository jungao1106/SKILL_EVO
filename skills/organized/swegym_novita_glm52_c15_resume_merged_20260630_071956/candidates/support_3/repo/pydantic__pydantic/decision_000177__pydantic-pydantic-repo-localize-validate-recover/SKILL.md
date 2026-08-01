---
name: "pydantic-pydantic-repo-localize-validate-recover"
description: "candidate repo skill materialized from training candidate evidence."
active: false
status: "candidate"
level: "repo"
decision: "refresh"
candidate_decision: "accept"
support_bucket: 3
support_count: 4
support_unit: "verifier_positive_task_events"
run: "swegym_novita_glm52_c15_resume_merged_20260630_071956"
source_decision_index: 177
repo: "pydantic__pydantic"
confidence: 0.95
proxy_reward: 1.0
---

# pydantic-pydantic-repo-localize-validate-recover

## Status

- Status: `candidate`
- Decision: `refresh`
- Candidate decision: `accept`
- Support: `4` `verifier_positive_task_events`; bucket `support_3`
- Reason: accepted duplicate repo candidate already promoted in this version

## Trigger

current task is in repo pydantic__pydantic; public trace matches repeated owner paths or adjacent modules; focused validation resembles repeated test commands; failure signature matches repeated repo failures

## Evidence Gate

Use only when current public evidence independently matches the repo cluster; do not copy a source-task patch.

## Actions

1. Start localization from the repeated owner paths only after current evidence matches them.
2. Prefer the repeated focused validation command when it matches the current issue.
3. If the repeated failure signature appears, recover before broadening the edit.

## Validation Hint

cd /testbed && python -c " from pydantic import BaseModel, ImportString import math, os, pytest class M(BaseModel): thing: ImportString cases = [ ('math:cos', 'math.cos', 'json'), ('tests.test_types.pytest', 'pytest', 'json'), ('os.path', 'posixpath', 'json'),; python -m pytest -q tests/test_json_schema.py::test_by_alias tests/test_json_schema.py::test_ipv6network_type tests/test_json_schema.py::test_override_generate_json_schema 'tests/test_json_schema.py::test_secret_types[SecretStr-string]' tests/test_json_schema.

## Stop Condition

Stop using this repo candidate if the current traceback, symbol, path, or focused test does not match the cluster evidence.

## Support Summary

5 task events; 4 verifier-positive events; paths=pydantic/types.py, tests/test_types.py, pydantic/_internal/_generate_schema.py; tests=cd /testbed && python -c " from pydantic import BaseModel, ImportString import math, os, pytest class M(BaseModel): thing: ImportString cases = [ ('math:cos', 'math.cos', 'json'), ('tests.test_types.pytest', 'pytest', 'json'), ('os.path', 'posixpath', 'json'),, python -m pytest -q tests/test_json_schema.py::test_by_alias tests/test_json_schema.py::test_ipv6network_type tests/test_json_schema.py::test_override_generate_json_schema 'tests/test_json_schema.py::test_secret_types[SecretStr-string]' tests/test_json_schema.; failures=localization-drift

## Evaluator Summary

candidate passed deterministic verifier-proxy gate
