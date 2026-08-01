---
name: conan-io-conan-repo-validate
description: repo skill accepted by the evaluator.
active: true
quality_score: 1.00
quality_tier: repo
risk_flags: []
use_policy: evidence-gated
level: repo
---

# conan-io-conan-repo-validate

- Run: `swegym_pi_novita_glm52_c15_resume_merged_20260709_101635`
- Evaluator decision: `accept`
- Proxy reward: `1.0`
- Confidence: `0.95`

## Trigger

current task is in repo conan-io__conan; focused validation resembles repeated test commands

## Evidence Gate

Use only when current public evidence independently matches the repo cluster; do not copy a source-task patch.

## Actions

1. Prefer the repeated focused validation command when it matches the current issue.

## Validation Hint

cd /testbed && python -m pytest -q conans/test/integration/command_v2/test_cache_save_restore.py::test_cache_save_restore_with_package_file conans/test/integration/command_v2/test_cache_save_restore.py::test_cache_save_restore conans/test/integration/command_v; cd /testbed && python -m pytest -q conans/test/integration/toolchains/cmake/test_cmaketoolchain_blocks.py::test_custom_block 'conans/test/unittests/tools/cmake/test_cmaketoolchain.py::test_fpic_when_shared_true[True]' conans/test/unittests/tools/cmake/test_cma

## Stop Condition

Stop using this repo candidate if the current traceback, symbol, path, or focused test does not match the cluster evidence.

## Support Summary

5 task events; 5 positive events; paths=none; tests=cd /testbed && python -m pytest -q conans/test/integration/command_v2/test_cache_save_restore.py::test_cache_save_restore_with_package_file conans/test/integration/command_v2/test_cache_save_restore.py::test_cache_save_restore conans/test/integration/command_v, cd /testbed && python -m pytest -q conans/test/integration/toolchains/cmake/test_cmaketoolchain_blocks.py::test_custom_block 'conans/test/unittests/tools/cmake/test_cmaketoolchain.py::test_fpic_when_shared_true[True]' conans/test/unittests/tools/cmake/test_cma; failures=none
