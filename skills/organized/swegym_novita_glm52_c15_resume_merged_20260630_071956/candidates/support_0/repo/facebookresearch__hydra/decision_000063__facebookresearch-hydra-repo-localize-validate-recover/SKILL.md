---
name: "facebookresearch-hydra-repo-localize-validate-recover"
description: "candidate repo skill materialized from training candidate evidence."
active: false
status: "candidate"
level: "repo"
decision: "stage"
candidate_decision: "memory_only"
support_bucket: 0
support_count: 0
support_unit: "verifier_positive_task_events"
run: "swegym_novita_glm52_c15_resume_merged_20260630_071956"
source_decision_index: 63
repo: "facebookresearch__hydra"
confidence: 0.51
proxy_reward: 0.56
---

# facebookresearch-hydra-repo-localize-validate-recover

## Status

- Status: `candidate`
- Decision: `stage`
- Candidate decision: `memory_only`
- Support: `0` `verifier_positive_task_events`; bucket `support_0`
- Reason: insufficient verifier-positive repo support

## Trigger

current task is in repo facebookresearch__hydra; public trace matches repeated owner paths or adjacent modules; focused validation resembles repeated test commands; failure signature matches repeated repo failures

## Evidence Gate

Use only when current public evidence independently matches the repo cluster; do not copy a source-task patch.

## Actions

1. Start localization from the repeated owner paths only after current evidence matches them.
2. Prefer the repeated focused validation command when it matches the current issue.
3. If the repeated failure signature appears, recover before broadening the edit.

## Validation Hint

cd /testbed/tools/configen && python -m pytest -q "tests/test_generate.py::test_instantiate_classes[DictValues]" "tests/test_generate.py::test_instantiate_classes[Tuples]" tests/test_generate.py::test_generated_code "tests/test_generate.py::test_generated_code; cd /testbed && python -m pytest -q 'tests/test_hydra.py::test_hydra_main_rerun' 'tests/test_hydra.py::test_module_env_override[FB_PAR_MAIN_MODULE]' 'tests/test_hydra.py::test_config_name_and_path_overrides' 'tests/test_hydra.py::test_hydra_output_dir' 'tests/t; cd /testbed && python -m pytest -q 'tests/test_hydra.py::test_searchpath_config' 'tests/test_hydra.py::test_hydra_main_rerun[None-tests.test_apps.hydra_main_rerun.my_app]' 'tests/test_hydra.py::test_module_env_override[FB_PAR_MAIN_MODULE]' 'tests/test_hydra.py

## Stop Condition

Stop using this repo candidate if the current traceback, symbol, path, or focused test does not match the cluster evidence.

## Support Summary

5 task events; 0 verifier-positive events; paths=hydra/_internal/hydra.py, tests/test_hydra.py, hydra, hydra/_internal/config_loader_impl.py; tests=cd /testbed/tools/configen && python -m pytest -q "tests/test_generate.py::test_instantiate_classes[DictValues]" "tests/test_generate.py::test_instantiate_classes[Tuples]" tests/test_generate.py::test_generated_code "tests/test_generate.py::test_generated_code, cd /testbed && python -m pytest -q 'tests/test_hydra.py::test_hydra_main_rerun' 'tests/test_hydra.py::test_module_env_override[FB_PAR_MAIN_MODULE]' 'tests/test_hydra.py::test_config_name_and_path_overrides' 'tests/test_hydra.py::test_hydra_output_dir' 'tests/t, cd /testbed && python -m pytest -q 'tests/test_hydra.py::test_searchpath_config' 'tests/test_hydra.py::test_hydra_main_rerun[None-tests.test_apps.hydra_main_rerun.my_app]' 'tests/test_hydra.py::test_module_env_override[FB_PAR_MAIN_MODULE]' 'tests/test_hydra.py; failures=localization-drift

## Evaluator Summary

insufficient verifier-positive repo support
