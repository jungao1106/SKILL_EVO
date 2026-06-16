# Curator Policy v0

Role: generate bounded five-stage skill operations from SWE-Bench rollouts.

## Contract

Every task skill state has exactly these stage slots:

- reproduce
- localize
- edit
- validate
- recover

The curator may only propose bounded operations over those slots:

- insert_stage_skill
- update_stage_skill
- delete_stage_skill
- attach_script
- delete_script

## Evidence Rules

- Distill concrete evidence from task text, trajectory actions, touched paths, edited paths, tests, verifier output, and failure signatures.
- Preserve facts that help a future executor decide what to inspect, edit, test, or avoid.
- Drop long logs, repeated shell output, sandbox-specific absolute paths, API keys, hidden labels, and gold-patch leakage.
- Successful and failed rollouts are both useful. Failed rollouts should mostly update recover/localize/validate rules.

## Stage Requirements

Each stage skill should include:

- trigger: when this skill should activate.
- actions: short ordered actions the executor should take.
- evidence_to_collect: what observations should be gathered before editing or stopping.
- stop_condition: when to leave this stage.
- risk: how this skill could overfit or mislead.

## Edit Budget

- Prefer 1 to 5 stage edits per training update.
- Preserve useful existing rules unless verifier evidence shows they are harmful.
- Update existing stage skills before inserting redundant new skills.
- Delete skills that are stale, duplicated, too broad, or tied to a one-off sandbox.

## Script Policy

Attach helper scripts only when they are reusable for the stage:

- reproduce scripts should create a small issue reproduction.
- validate scripts should run targeted checks or parse verifier output.
- recover scripts should detect no-diff, repeated search, or invalid test selection.

Scripts must be compact, executable, and free of secrets or absolute private paths.

<!-- skill-evo-policy-update:v0002_from_v0001:start -->
## TextGrad Update v0002_from_v0001: Fixed-Harness Curation Update

Source: compare `v0001` against `v0_noskills` and rewrite only according to the fixed top-level harness labels:

- positive: previous reward `0` -> current reward `1`; preserve or distill with at most one stage edit.
- strong_negative: previous reward `1` -> current reward `0`; delete, disable, or major rewrite up to five stage edits.
- weak_negative: previous reward `0` -> current reward `0`; bounded targeted rewrite up to two stage edits.

Observed selected cases:

- selected_cases: `117`
- case_types: `{"positive": 14, "strong_negative": 18, "weak_negative": 85}`
- allowed_edit_degrees: `{"bounded_targeted_rewrite": 85, "major_delete_or_rewrite": 18, "preserve_distill_only": 14}`
- transitions: `{"improved": 14, "regressed": 18, "still_zero": 85}`
- top_repos: `{"astropy": 8, "django": 50, "matplotlib": 8, "mwaskom": 1, "psf": 2, "pydata": 4, "pylint-dev": 7, "pytest-dev": 2, "scikit-learn": 5, "sphinx-doc": 7, "sympy": 23}`
- current_exceptions: `{"AgentTimeoutError": 5, "none": 112}`

Policy update:

- For positive cases, preserve the helpful stage behavior and distill it shorter. Do not generalize it into unrelated tasks or add new broad actions.
- For strong negatives, assume the skill harmed a previously solved task. Delete, disable, or rewrite the likely harmful stage first; generic localize/edit advice is the first suspect.
- For weak negatives, make a small targeted rewrite from the failed behavior. Prefer recover/validate stop conditions and narrower reproduction over broad new search plans.
- Editing strength is fixed by the harness case type and cannot be changed by curator policy text.
- Timeout-sensitive edits must reduce search breadth and add early stop conditions.
- Environment/setup failures should only affect recover/validate safeguards unless source-code evidence says otherwise.
<!-- skill-evo-policy-update:v0002_from_v0001:end -->

<!-- skill-evo-policy-update:v0003_from_v0002:start -->
## TextGrad Update v0003_from_v0002: Fixed-Harness Curation Update

Source: compare `v0002` against `v0001` and rewrite only according to the fixed top-level harness labels:

- positive: previous reward `0` -> current reward `1`; preserve or distill with at most one stage edit.
- strong_negative: previous reward `1` -> current reward `0`; delete, disable, or major rewrite up to five stage edits.
- weak_negative: previous reward `0` -> current reward `0`; bounded targeted rewrite up to two stage edits.

Observed selected cases:

- selected_cases: `0`
- case_types: `{}`
- allowed_edit_degrees: `{}`
- transitions: `{}`
- top_repos: `{}`
- current_exceptions: `{}`

Policy update:

- For positive cases, preserve the helpful stage behavior and distill it shorter. Do not generalize it into unrelated tasks or add new broad actions.
- For strong negatives, assume the skill harmed a previously solved task. Delete, disable, or rewrite the likely harmful stage first; generic localize/edit advice is the first suspect.
- For weak negatives, make a small targeted rewrite from the failed behavior. Prefer recover/validate stop conditions and narrower reproduction over broad new search plans.
- Editing strength is fixed by the harness case type and cannot be changed by curator policy text.
- Timeout-sensitive edits must reduce search breadth and add early stop conditions.
- Environment/setup failures should only affect recover/validate safeguards unless source-code evidence says otherwise.
<!-- skill-evo-policy-update:v0003_from_v0002:end -->

<!-- skill-evo-policy-update:v0004_from_v0003:start -->
## TextGrad Update v0004_from_v0003: Fixed-Harness Curation Update

Source: compare `v0003` against `v0002` and rewrite only according to the fixed top-level harness labels:

- positive: previous reward `0` -> current reward `1`; preserve or distill with at most one stage edit.
- strong_negative: previous reward `1` -> current reward `0`; delete, disable, or major rewrite up to five stage edits.
- weak_negative: previous reward `0` -> current reward `0`; bounded targeted rewrite up to two stage edits.

Observed selected cases:

- selected_cases: `1`
- case_types: `{"weak_negative": 1}`
- allowed_edit_degrees: `{"bounded_targeted_rewrite": 1}`
- transitions: `{"still_zero": 1}`
- top_repos: `{"django": 1}`
- current_exceptions: `{"none": 1}`

Policy update:

- For positive cases, preserve the helpful stage behavior and distill it shorter. Do not generalize it into unrelated tasks or add new broad actions.
- For strong negatives, assume the skill harmed a previously solved task. Delete, disable, or rewrite the likely harmful stage first; generic localize/edit advice is the first suspect.
- For weak negatives, make a small targeted rewrite from the failed behavior. Prefer recover/validate stop conditions and narrower reproduction over broad new search plans.
- Editing strength is fixed by the harness case type and cannot be changed by curator policy text.
- Timeout-sensitive edits must reduce search breadth and add early stop conditions.
- Environment/setup failures should only affect recover/validate safeguards unless source-code evidence says otherwise.
<!-- skill-evo-policy-update:v0004_from_v0003:end -->

<!-- skill-evo-policy-update:v0005_from_v0004:start -->
## TextGrad Update v0005_from_v0004: Fixed-Harness Curation Update

Source: compare `v0004` against `v0003` and rewrite only according to the fixed top-level harness labels:

- positive: previous reward `0` -> current reward `1`; preserve or distill with at most one stage edit.
- strong_negative: previous reward `1` -> current reward `0`; delete, disable, or major rewrite up to five stage edits.
- weak_negative: previous reward `0` -> current reward `0`; bounded targeted rewrite up to two stage edits.

Observed selected cases:

- selected_cases: `8`
- case_types: `{"weak_negative": 8}`
- allowed_edit_degrees: `{"bounded_targeted_rewrite": 8}`
- transitions: `{"still_zero": 8}`
- top_repos: `{"django": 4, "pydata": 1, "scikit-learn": 2, "sympy": 1}`
- current_exceptions: `{"none": 8}`

Policy update:

- For positive cases, preserve the helpful stage behavior and distill it shorter. Do not generalize it into unrelated tasks or add new broad actions.
- For strong negatives, assume the skill harmed a previously solved task. Delete, disable, or rewrite the likely harmful stage first; generic localize/edit advice is the first suspect.
- For weak negatives, make a small targeted rewrite from the failed behavior. Prefer recover/validate stop conditions and narrower reproduction over broad new search plans.
- Editing strength is fixed by the harness case type and cannot be changed by curator policy text.
- Timeout-sensitive edits must reduce search breadth and add early stop conditions.
- Environment/setup failures should only affect recover/validate safeguards unless source-code evidence says otherwise.
<!-- skill-evo-policy-update:v0005_from_v0004:end -->

<!-- skill-evo-policy-update:v0007_from_v0006:start -->
## TextGrad Update v0007_from_v0006: Fixed-Harness Curation Update

Source: compare `v0006` against `v0_noskills` and rewrite only according to the fixed top-level harness labels:

- positive: previous reward `0` -> current reward `1`; preserve or distill with at most one stage edit.
- strong_negative: previous reward `1` -> current reward `0`; delete, disable, or major rewrite up to five stage edits.
- weak_negative: previous reward `0` -> current reward `0`; bounded targeted rewrite up to two stage edits.

Observed selected cases:

- selected_cases: `108`
- case_types: `{"positive": 10, "strong_negative": 20, "weak_negative": 78}`
- allowed_edit_degrees: `{"bounded_targeted_rewrite": 78, "major_delete_or_rewrite": 20, "preserve_distill_only": 10}`
- transitions: `{"improved": 10, "regressed": 20, "still_zero": 78}`
- base_to_current_types: `{"current_error_diagnostic": 67, "positive": 10, "stable_positive": 318, "strong_negative": 20, "unpaired_current_one": 3, "unpaired_current_zero": 4, "weak_negative": 78}`
- previous_to_current_types: `{"current_error_diagnostic": 67, "positive": 10, "stable_positive": 318, "strong_negative": 20, "unpaired_current_one": 3, "unpaired_current_zero": 4, "weak_negative": 78}`
- top_repos: `{"astropy": 9, "django": 41, "matplotlib": 8, "psf": 4, "pydata": 3, "pylint-dev": 6, "pytest-dev": 2, "scikit-learn": 5, "sphinx-doc": 8, "sympy": 22}`
- current_exceptions: `{"none": 108}`
- reward_agent_target_stages: `{"edit": 20, "localize": 103, "recover": 20, "validate": 83}`
- reward_agent_risk_signals: `{"generic_skill_language": 89, "missing_concrete_owner_paths": 1, "missing_focused_validation": 32, "private_or_sandbox_path_leakage": 88}`

Policy update:

- For positive cases, preserve the helpful stage behavior and distill it shorter. Do not generalize it into unrelated tasks or add new broad actions.
- For strong negatives, assume the skill harmed a previously solved task. Delete, disable, or rewrite the likely harmful stage first; generic localize/edit advice is the first suspect.
- For weak negatives, make a small targeted rewrite from the failed behavior. Prefer recover/validate stop conditions and narrower reproduction over broad new search plans.
- Stable positives are policy-only anchors. They should mostly prevent unnecessary edits.
- Before editing a task skill, inspect whether current is better than both previous and base. If current is worse than base, prefer narrowing or disabling over preserving.
- Editing strength is fixed by the harness case type and cannot be changed by curator policy text.
- Timeout-sensitive edits must reduce search breadth and add early stop conditions.
- Environment/setup failures should only affect recover/validate safeguards unless source-code evidence says otherwise.
- Use each reward case's `curator_feedback` as the per-task rewrite contract: obey `target_stages`, preserve concrete paths/tests, and remove the listed risky signals before adding new prose.
<!-- skill-evo-policy-update:v0007_from_v0006:end -->

<!-- skill-evo-policy-update:v0008_from_v0007:start -->
## TextGrad Update v0008_from_v0007: Fixed-Harness Curation Update

Source: compare `v0007` against `v0006` and rewrite only according to the fixed top-level harness labels:

- positive: previous reward `0` -> current reward `1`; preserve or distill with at most one stage edit.
- strong_negative: previous reward `1` -> current reward `0`; delete, disable, or major rewrite up to five stage edits.
- weak_negative: previous reward `0` -> current reward `0`; bounded targeted rewrite up to two stage edits.

Observed selected cases:

- selected_cases: `56`
- case_types: `{"positive": 9, "strong_negative": 4, "weak_negative": 43}`
- allowed_edit_degrees: `{"bounded_targeted_rewrite": 43, "major_delete_or_rewrite": 4, "preserve_distill_only": 9}`
- transitions: `{"improved": 9, "regressed": 4, "still_zero": 43}`
- base_to_current_types: `{"current_error_diagnostic": 270, "positive": 5, "stable_positive": 161, "strong_negative": 14, "unpaired_current_one": 3, "unpaired_current_zero": 4, "weak_negative": 43}`
- previous_to_current_types: `{"current_error_diagnostic": 270, "positive": 9, "stable_positive": 138, "strong_negative": 4, "unpaired_current_one": 22, "unpaired_current_zero": 14, "weak_negative": 43}`
- top_repos: `{"astropy": 6, "django": 21, "matplotlib": 4, "psf": 3, "pydata": 1, "pylint-dev": 2, "pytest-dev": 1, "scikit-learn": 2, "sphinx-doc": 4, "sympy": 12}`
- current_exceptions: `{"NonZeroAgentExitCodeError": 1, "none": 55}`
- reward_agent_target_stages: `{"edit": 4, "localize": 51, "recover": 4, "validate": 48}`
- reward_agent_risk_signals: `{"generic_skill_language": 48, "missing_concrete_owner_paths": 1, "missing_focused_validation": 17, "private_or_sandbox_path_leakage": 44}`

Policy update:

- For positive cases, preserve the helpful stage behavior and distill it shorter. Do not generalize it into unrelated tasks or add new broad actions.
- For strong negatives, assume the skill harmed a previously solved task. Delete, disable, or rewrite the likely harmful stage first; generic localize/edit advice is the first suspect.
- For weak negatives, make a small targeted rewrite from the failed behavior. Prefer recover/validate stop conditions and narrower reproduction over broad new search plans.
- Stable positives are policy-only anchors. They should mostly prevent unnecessary edits.
- Before editing a task skill, inspect whether current is better than both previous and base. If current is worse than base, prefer narrowing or disabling over preserving.
- Editing strength is fixed by the harness case type and cannot be changed by curator policy text.
- Timeout-sensitive edits must reduce search breadth and add early stop conditions.
- Environment/setup failures should only affect recover/validate safeguards unless source-code evidence says otherwise.
- Use each reward case's `curator_feedback` as the per-task rewrite contract: obey `target_stages`, preserve concrete paths/tests, and remove the listed risky signals before adding new prose.
<!-- skill-evo-policy-update:v0008_from_v0007:end -->

<!-- skill-evo-policy-update:v0009_from_v0008:start -->
## TextGrad Update v0009_from_v0008: Fixed-Harness Curation Update

Source: compare `v0008` against `v0007` and rewrite only according to the fixed top-level harness labels:

- positive: previous reward `0` -> current reward `1`; preserve or distill with at most one stage edit.
- strong_negative: previous reward `1` -> current reward `0`; delete, disable, or major rewrite up to five stage edits.
- weak_negative: previous reward `0` -> current reward `0`; bounded targeted rewrite up to two stage edits.

Observed selected cases:

- selected_cases: `36`
- case_types: `{"positive": 5, "strong_negative": 2, "weak_negative": 29}`
- allowed_edit_degrees: `{"bounded_targeted_rewrite": 29, "major_delete_or_rewrite": 2, "preserve_distill_only": 5}`
- transitions: `{"improved": 5, "regressed": 2, "still_zero": 29}`
- base_to_current_types: `{"current_error_diagnostic": 31, "positive": 10, "stable_positive": 341, "strong_negative": 14, "unpaired_current_one": 7, "unpaired_current_zero": 7, "weak_negative": 90}`
- previous_to_current_types: `{"current_error_diagnostic": 31, "positive": 5, "stable_positive": 91, "strong_negative": 2, "unpaired_current_one": 262, "unpaired_current_zero": 80, "weak_negative": 29}`
- top_repos: `{"astropy": 6, "django": 11, "matplotlib": 1, "psf": 1, "pydata": 1, "pylint-dev": 3, "pytest-dev": 2, "scikit-learn": 1, "sphinx-doc": 2, "sympy": 8}`
- current_exceptions: `{"none": 36}`
- reward_agent_target_stages: `{"edit": 2, "localize": 32, "recover": 2, "validate": 33}`
- reward_agent_risk_signals: `{"generic_skill_language": 33, "missing_concrete_owner_paths": 2, "missing_focused_validation": 10, "private_or_sandbox_path_leakage": 26}`

Policy update:

- For positive cases, preserve the helpful stage behavior and distill it shorter. Do not generalize it into unrelated tasks or add new broad actions.
- For strong negatives, assume the skill harmed a previously solved task. Delete, disable, or rewrite the likely harmful stage first; generic localize/edit advice is the first suspect.
- For weak negatives, make a small targeted rewrite from the failed behavior. Prefer recover/validate stop conditions and narrower reproduction over broad new search plans.
- Stable positives are policy-only anchors. They should mostly prevent unnecessary edits.
- Before editing a task skill, inspect whether current is better than both previous and base. If current is worse than base, prefer narrowing or disabling over preserving.
- Editing strength is fixed by the harness case type and cannot be changed by curator policy text.
- Timeout-sensitive edits must reduce search breadth and add early stop conditions.
- Environment/setup failures should only affect recover/validate safeguards unless source-code evidence says otherwise.
- Use each reward case's `curator_feedback` as the per-task rewrite contract: obey `target_stages`, preserve concrete paths/tests, and remove the listed risky signals before adding new prose.
<!-- skill-evo-policy-update:v0009_from_v0008:end -->

<!-- skill-evo-policy-update:v0010_from_v0009:start -->
## TextGrad Update v0010_from_v0009: Fixed-Harness Curation Update

Source: compare `v0009` against `v0008` and rewrite only according to the fixed top-level harness labels:

- positive: previous reward `0` -> current reward `1`; preserve or distill with at most one stage edit.
- strong_negative: previous reward `1` -> current reward `0`; delete, disable, or major rewrite up to five stage edits.
- weak_negative: previous reward `0` -> current reward `0`; bounded targeted rewrite up to two stage edits.

Observed selected cases:

- selected_cases: `117`
- case_types: `{"positive": 7, "strong_negative": 14, "weak_negative": 96}`
- allowed_edit_degrees: `{"bounded_targeted_rewrite": 96, "major_delete_or_rewrite": 14, "preserve_distill_only": 7}`
- transitions: `{"improved": 7, "regressed": 14, "still_zero": 96}`
- base_to_current_types: `{"current_error_diagnostic": 35, "positive": 11, "stable_positive": 332, "strong_negative": 25, "unpaired_current_one": 3, "unpaired_current_zero": 6, "weak_negative": 88}`
- previous_to_current_types: `{"current_error_diagnostic": 35, "positive": 7, "stable_positive": 328, "strong_negative": 14, "unpaired_current_one": 11, "unpaired_current_zero": 9, "weak_negative": 96}`
- top_repos: `{"astropy": 10, "django": 47, "matplotlib": 8, "psf": 3, "pydata": 4, "pylint-dev": 6, "pytest-dev": 3, "scikit-learn": 4, "sphinx-doc": 9, "sympy": 23}`
- current_exceptions: `{"none": 117}`
- reward_agent_target_stages: `{"edit": 14, "localize": 111, "recover": 14, "validate": 102}`
- reward_agent_risk_signals: `{"generic_skill_language": 98, "missing_concrete_owner_paths": 3, "missing_focused_validation": 43, "private_or_sandbox_path_leakage": 97}`

Policy update:

- For positive cases, preserve the helpful stage behavior and distill it shorter. Do not generalize it into unrelated tasks or add new broad actions.
- For strong negatives, assume the skill harmed a previously solved task. Delete, disable, or rewrite the likely harmful stage first; generic localize/edit advice is the first suspect.
- For weak negatives, make a small targeted rewrite from the failed behavior. Prefer recover/validate stop conditions and narrower reproduction over broad new search plans.
- Stable positives are policy-only anchors. They should mostly prevent unnecessary edits.
- Before editing a task skill, inspect whether current is better than both previous and base. If current is worse than base, prefer narrowing or disabling over preserving.
- Editing strength is fixed by the harness case type and cannot be changed by curator policy text.
- Timeout-sensitive edits must reduce search breadth and add early stop conditions.
- Environment/setup failures should only affect recover/validate safeguards unless source-code evidence says otherwise.
- Use each reward case's `curator_feedback` as the per-task rewrite contract: obey `target_stages`, preserve concrete paths/tests, and remove the listed risky signals before adding new prose.
<!-- skill-evo-policy-update:v0010_from_v0009:end -->

<!-- skill-evo-policy-update:v0013_from_v0012:start -->
## TextGrad Update v0013_from_v0012: Fixed-Harness Curation Update

Source: compare `v0012` against `v0_noskills` and rewrite only according to the fixed top-level harness labels:

- positive: previous reward `0` -> current reward `1`; preserve or distill with at most one stage edit.
- strong_negative: previous reward `1` -> current reward `0`; delete, disable, or major rewrite up to five stage edits.
- weak_negative: previous reward `0` -> current reward `0`; bounded targeted rewrite up to two stage edits.

Observed selected cases:

- selected_cases: `104`
- case_types: `{"positive": 9, "weak_negative": 95}`
- allowed_edit_degrees: `{"bounded_targeted_rewrite": 95, "preserve_distill_only": 9}`
- transitions: `{"improved": 9, "still_zero": 95}`
- base_to_current_types: `{"current_error_diagnostic": 12, "missing_current": 370, "positive": 9, "unpaired_current_one": 8, "unpaired_current_zero": 6, "weak_negative": 95}`
- previous_to_current_types: `{"current_error_diagnostic": 12, "missing_current": 370, "positive": 9, "unpaired_current_one": 8, "unpaired_current_zero": 6, "weak_negative": 95}`
- top_repos: `{"astropy": 9, "django": 39, "matplotlib": 8, "psf": 1, "pydata": 4, "pylint-dev": 7, "pytest-dev": 2, "scikit-learn": 4, "sphinx-doc": 9, "sympy": 21}`
- current_exceptions: `{"NonZeroAgentExitCodeError": 1, "none": 103}`
- reward_agent_target_stages: `{"localize": 97, "validate": 102}`
- reward_agent_risk_signals: `{"generic_skill_language": 56, "missing_focused_validation": 26, "private_or_sandbox_path_leakage": 84}`

Policy update:

- For positive cases, preserve the helpful stage behavior and distill it shorter. Do not generalize it into unrelated tasks or add new broad actions.
- For strong negatives, assume the skill harmed a previously solved task. Delete, disable, or rewrite the likely harmful stage first; generic localize/edit advice is the first suspect.
- For weak negatives, make a small targeted rewrite from the failed behavior. Prefer recover/validate stop conditions and narrower reproduction over broad new search plans.
- Stable positives are policy-only anchors. They should mostly prevent unnecessary edits.
- Before editing a task skill, inspect whether current is better than both previous and base. If current is worse than base, prefer narrowing or disabling over preserving.
- Editing strength is fixed by the harness case type and cannot be changed by curator policy text.
- Timeout-sensitive edits must reduce search breadth and add early stop conditions.
- Environment/setup failures should only affect recover/validate safeguards unless source-code evidence says otherwise.
- Use each reward case's `curator_feedback` as the per-task rewrite contract: obey `target_stages`, preserve concrete paths/tests, and remove the listed risky signals before adding new prose.
<!-- skill-evo-policy-update:v0013_from_v0012:end -->

<!-- skill-evo-policy-update:v0014_from_v0013:start -->
## TextGrad Update v0014_from_v0013: Fixed-Harness Curation Update

Source: compare `v0013` against `v0012` and rewrite only according to the fixed top-level harness labels:

- positive: previous reward `0` -> current reward `1`; preserve or distill with at most one stage edit.
- strong_negative: previous reward `1` -> current reward `0`; delete, disable, or major rewrite up to five stage edits.
- weak_negative: previous reward `0` -> current reward `0`; bounded targeted rewrite up to two stage edits.

Observed selected cases:

- selected_cases: `103`
- case_types: `{"positive": 9, "strong_negative": 7, "weak_negative": 87}`
- allowed_edit_degrees: `{"bounded_targeted_rewrite": 87, "major_delete_or_rewrite": 7, "preserve_distill_only": 9}`
- transitions: `{"improved": 9, "regressed": 7, "still_zero": 87}`
- base_to_current_types: `{"current_error_diagnostic": 17, "missing_current": 370, "positive": 13, "unpaired_current_one": 4, "unpaired_current_zero": 7, "weak_negative": 89}`
- previous_to_current_types: `{"current_error_diagnostic": 17, "other": 370, "positive": 9, "stable_positive": 6, "strong_negative": 7, "unpaired_current_one": 2, "unpaired_current_zero": 2, "weak_negative": 87}`
- top_repos: `{"astropy": 10, "django": 40, "matplotlib": 7, "psf": 1, "pydata": 4, "pylint-dev": 7, "pytest-dev": 2, "scikit-learn": 4, "sphinx-doc": 8, "sympy": 20}`
- current_exceptions: `{"none": 103}`
- reward_agent_target_stages: `{"edit": 7, "localize": 98, "recover": 7, "validate": 92}`
- reward_agent_risk_signals: `{"generic_skill_language": 55, "missing_concrete_owner_paths": 2, "missing_focused_validation": 29, "private_or_sandbox_path_leakage": 82}`

Policy update:

- For positive cases, preserve the helpful stage behavior and distill it shorter. Do not generalize it into unrelated tasks or add new broad actions.
- For strong negatives, assume the skill harmed a previously solved task. Delete, disable, or rewrite the likely harmful stage first; generic localize/edit advice is the first suspect.
- For weak negatives, make a small targeted rewrite from the failed behavior. Prefer recover/validate stop conditions and narrower reproduction over broad new search plans.
- Stable positives are policy-only anchors. They should mostly prevent unnecessary edits.
- Before editing a task skill, inspect whether current is better than both previous and base. If current is worse than base, prefer narrowing or disabling over preserving.
- Editing strength is fixed by the harness case type and cannot be changed by curator policy text.
- Timeout-sensitive edits must reduce search breadth and add early stop conditions.
- Environment/setup failures should only affect recover/validate safeguards unless source-code evidence says otherwise.
- Use each reward case's `curator_feedback` as the per-task rewrite contract: obey `target_stages`, preserve concrete paths/tests, and remove the listed risky signals before adding new prose.
<!-- skill-evo-policy-update:v0014_from_v0013:end -->

<!-- skill-evo-policy-update:v0015_from_v0014:start -->
## TextGrad Update v0015_from_v0014: Fixed-Harness Curation Update

Source: compare `v0014` against `v0013` and rewrite only according to the fixed top-level harness labels:

- positive: previous reward `0` -> current reward `1`; preserve or distill with at most one stage edit.
- strong_negative: previous reward `1` -> current reward `0`; delete, disable, or major rewrite up to five stage edits.
- weak_negative: previous reward `0` -> current reward `0`; bounded targeted rewrite up to two stage edits.

Observed selected cases:

- selected_cases: `95`
- case_types: `{"positive": 5, "strong_negative": 4, "weak_negative": 86}`
- allowed_edit_degrees: `{"bounded_targeted_rewrite": 86, "major_delete_or_rewrite": 4, "preserve_distill_only": 5}`
- transitions: `{"improved": 5, "regressed": 4, "still_zero": 86}`
- base_to_current_types: `{"current_error_diagnostic": 11, "missing_current": 370, "positive": 14, "unpaired_current_one": 9, "unpaired_current_zero": 7, "weak_negative": 89}`
- previous_to_current_types: `{"current_error_diagnostic": 11, "other": 370, "positive": 5, "stable_positive": 12, "strong_negative": 4, "unpaired_current_one": 6, "unpaired_current_zero": 6, "weak_negative": 86}`
- top_repos: `{"astropy": 8, "django": 35, "matplotlib": 7, "psf": 1, "pydata": 4, "pylint-dev": 7, "pytest-dev": 3, "scikit-learn": 3, "sphinx-doc": 8, "sympy": 19}`
- current_exceptions: `{"none": 95}`
- reward_agent_target_stages: `{"edit": 4, "localize": 91, "recover": 4, "validate": 90}`
- reward_agent_risk_signals: `{"generic_skill_language": 55, "missing_concrete_owner_paths": 1, "missing_focused_validation": 26, "private_or_sandbox_path_leakage": 77}`

Policy update:

- For positive cases, preserve the helpful stage behavior and distill it shorter. Do not generalize it into unrelated tasks or add new broad actions.
- For strong negatives, assume the skill harmed a previously solved task. Delete, disable, or rewrite the likely harmful stage first; generic localize/edit advice is the first suspect.
- For weak negatives, make a small targeted rewrite from the failed behavior. Prefer recover/validate stop conditions and narrower reproduction over broad new search plans.
- Stable positives are policy-only anchors. They should mostly prevent unnecessary edits.
- Before editing a task skill, inspect whether current is better than both previous and base. If current is worse than base, prefer narrowing or disabling over preserving.
- Editing strength is fixed by the harness case type and cannot be changed by curator policy text.
- Timeout-sensitive edits must reduce search breadth and add early stop conditions.
- Environment/setup failures should only affect recover/validate safeguards unless source-code evidence says otherwise.
- Use each reward case's `curator_feedback` as the per-task rewrite contract: obey `target_stages`, preserve concrete paths/tests, and remove the listed risky signals before adding new prose.
<!-- skill-evo-policy-update:v0015_from_v0014:end -->

<!-- skill-evo-policy-update:v0016_from_v0015:start -->
## TextGrad Update v0016_from_v0015: Fixed-Harness Curation Update

Source: compare `v0015` against `v0014` and rewrite only according to the fixed top-level harness labels:

- positive: previous reward `0` -> current reward `1`; preserve or distill with at most one stage edit.
- strong_negative: previous reward `1` -> current reward `0`; delete, disable, or major rewrite up to five stage edits.
- weak_negative: previous reward `0` -> current reward `0`; bounded targeted rewrite up to two stage edits.

Observed selected cases:

- selected_cases: `103`
- case_types: `{"positive": 12, "strong_negative": 10, "weak_negative": 81}`
- allowed_edit_degrees: `{"bounded_targeted_rewrite": 81, "major_delete_or_rewrite": 10, "preserve_distill_only": 12}`
- transitions: `{"improved": 12, "regressed": 10, "still_zero": 81}`
- base_to_current_types: `{"current_error_diagnostic": 10, "missing_current": 370, "positive": 17, "unpaired_current_one": 7, "unpaired_current_zero": 10, "weak_negative": 86}`
- previous_to_current_types: `{"current_error_diagnostic": 10, "other": 370, "positive": 12, "stable_positive": 12, "strong_negative": 10, "unpaired_current_zero": 5, "weak_negative": 81}`
- top_repos: `{"astropy": 9, "django": 41, "matplotlib": 8, "pydata": 4, "pylint-dev": 7, "pytest-dev": 3, "scikit-learn": 3, "sphinx-doc": 9, "sympy": 19}`
- current_exceptions: `{"none": 103}`
- reward_agent_target_stages: `{"edit": 10, "localize": 96, "recover": 10, "validate": 88}`
- reward_agent_risk_signals: `{"generic_skill_language": 58, "missing_concrete_owner_paths": 1, "missing_focused_validation": 30, "private_or_sandbox_path_leakage": 84}`

Policy update:

- For positive cases, preserve the helpful stage behavior and distill it shorter. Do not generalize it into unrelated tasks or add new broad actions.
- For strong negatives, assume the skill harmed a previously solved task. Delete, disable, or rewrite the likely harmful stage first; generic localize/edit advice is the first suspect.
- For weak negatives, make a small targeted rewrite from the failed behavior. Prefer recover/validate stop conditions and narrower reproduction over broad new search plans.
- Stable positives are policy-only anchors. They should mostly prevent unnecessary edits.
- Before editing a task skill, inspect whether current is better than both previous and base. If current is worse than base, prefer narrowing or disabling over preserving.
- Editing strength is fixed by the harness case type and cannot be changed by curator policy text.
- Timeout-sensitive edits must reduce search breadth and add early stop conditions.
- Environment/setup failures should only affect recover/validate safeguards unless source-code evidence says otherwise.
- Use each reward case's `curator_feedback` as the per-task rewrite contract: obey `target_stages`, preserve concrete paths/tests, and remove the listed risky signals before adding new prose.
<!-- skill-evo-policy-update:v0016_from_v0015:end -->
