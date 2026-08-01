---
name: iterative-dvc-repo-localize-validate-recover
description: repo skill accepted by the evaluator.
active: true
quality_score: 1.00
quality_tier: repo
risk_flags: []
use_policy: evidence-gated
level: repo
---

# iterative-dvc-repo-localize-validate-recover

- Run: `swegym_pi_novita_glm52_c15_resume_merged_20260709_101635`
- Evaluator decision: `accept`
- Proxy reward: `1.0`
- Confidence: `0.95`

## Trigger

current task is in repo iterative__dvc; public trace matches repeated owner paths or adjacent modules; focused validation resembles repeated test commands; failure signature matches repeated repo failures

## Evidence Gate

Use only when current public evidence independently matches the repo cluster; do not copy a source-task patch.

## Actions

1. Start localization from the repeated owner paths only after current evidence matches them.
2. Prefer the repeated focused validation command when it matches the current issue.
3. If the repeated failure signature appears, recover before broadening the edit.

## Validation Hint

cd /testbed && cat > tests/test_move_inside_tmp.py << 'EOF' import os from dvc.main import main from dvc.stage import Stage from tests.basic_env import TestDvc from tests.utils import load_stage_file class TestMoveFileInsideDirectory(TestDvc): def test(self):

## Stop Condition

Stop using this repo candidate if the current traceback, symbol, path, or focused test does not match the cluster evidence.

## Support Summary

5 task events; 4 positive events; paths=dvc/stage/__init__.py, dvc; tests=cd /testbed && cat > tests/test_move_inside_tmp.py << 'EOF' import os from dvc.main import main from dvc.stage import Stage from tests.basic_env import TestDvc from tests.utils import load_stage_file class TestMoveFileInsideDirectory(TestDvc): def test(self):; failures=localization-drift
