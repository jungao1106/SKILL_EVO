---
name: "pydantic-pydantic-repo-localize-validate-recover"
description: "candidate repo skill materialized from training candidate evidence."
active: false
status: "candidate"
level: "repo"
decision: "refresh"
candidate_decision: "accept"
support_bucket: 2
support_count: 2
support_unit: "verifier_positive_task_events"
run: "swegym_novita_glm52_c15_resume_merged_20260630_071956"
source_decision_index: 184
repo: "pydantic__pydantic"
confidence: 0.91
proxy_reward: 0.96
---

# pydantic-pydantic-repo-localize-validate-recover

## Status

- Status: `candidate`
- Decision: `refresh`
- Candidate decision: `accept`
- Support: `2` `verifier_positive_task_events`; bucket `support_2`
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

cat > /tmp/test_perm.py << 'EOF' import json from itertools import combinations, chain, permutations from typing import Any from typing_extensions import Annotated from pydantic import BaseModel, Field, PlainValidator, PlainSerializer import pytest SERIALIZER

## Stop Condition

Stop using this repo candidate if the current traceback, symbol, path, or focused test does not match the cluster evidence.

## Support Summary

5 task events; 2 verifier-positive events; paths=pydantic; tests=cat > /tmp/test_perm.py << 'EOF' import json from itertools import combinations, chain, permutations from typing import Any from typing_extensions import Annotated from pydantic import BaseModel, Field, PlainValidator, PlainSerializer import pytest SERIALIZER; failures=localization-drift

## Evaluator Summary

candidate passed deterministic verifier-proxy gate
