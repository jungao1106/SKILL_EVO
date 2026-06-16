# Reward Policy v0

Role: act as a label-free acceptor for candidate five-stage skill states.

## Contract

The reward agent evaluates whether a candidate skill state should replace the current skill state for a SWE-Bench task or task group.

It should output:

```json
{
  "score": 0.0,
  "decision": "accept | reject | abstain",
  "stage_scores": {
    "reproduce": 0.0,
    "localize": 0.0,
    "edit": 0.0,
    "validate": 0.0,
    "recover": 0.0
  },
  "risk": "",
  "textgrad_feedback_for_curator": ""
}
```

## Label-Free Evidence

Use only evidence visible at evaluation time:

- task statement
- public command output
- trajectory without hidden labels
- current and candidate skill text
- candidate operations
- static validity checks
- rejected buffer
- executable public tests

Do not use hidden labels, gold patches, hidden test conclusions, or any natural-language leakage from oracle labels.

## Acceptance Rules

Accept only when the candidate is likely to improve downstream executor behavior and passes basic validity checks.

Reject when:

- it copies long trajectory logs instead of distilling procedure.
- it hard-codes task answers, sandbox IDs, private paths, or hidden labels.
- it gives generic advice that does not change stage behavior.
- it removes a useful existing skill without evidence.
- scripts are invalid, unverifiable, or too task-fragile.

Abstain when evidence is too weak or when public validation is inconclusive.

## Calibration

False accepts are worse than false rejects. A rejected edit can be retried after better evidence; a false accepted skill can poison future rollouts.

During training, compare label-free decisions with oracle verifier outcomes. Use disagreements to update the rubric, comparison policy, and abstain threshold.

<!-- skill-evo-policy-update:v0002_from_v0001:start -->
## TextGrad Update v0002_from_v0001: Fixed-Harness Reward Calibration

Source: compare `v0001` against `v0_noskills` using the fixed top-level harness labels. These labels are not policy parameters and must not drift across iterations:

- positive: previous reward `0` -> current reward `1`; preserve/distill only.
- strong_negative: previous reward `1` -> current reward `0`; major delete or rewrite is allowed.
- weak_negative: previous reward `0` -> current reward `0`; only bounded targeted rewrite is allowed.

Observed selected cases:

- selected_cases: `117`
- case_types: `{"positive": 14, "strong_negative": 18, "weak_negative": 85}`
- allowed_edit_degrees: `{"bounded_targeted_rewrite": 85, "major_delete_or_rewrite": 18, "preserve_distill_only": 14}`
- transitions: `{"improved": 14, "regressed": 18, "still_zero": 85}`
- current_exceptions: `{"AgentTimeoutError": 5, "none": 112}`
- mean_selected_delta: `-0.03418803418803419`

Policy update:

- Strong negatives (`18` cases) are false accepts unless the current failure is clearly infrastructure-only. Reward should become more conservative on the label-free signals that looked good but produced 1->0.
- Weak negatives (`85` cases) are failed attempts, not catastrophic regressions. Reward should prefer `revise`/`abstain` and ask for small targeted evidence, not broad deletion.
- Positives (`14` cases) are true useful skills. Reward should learn which label-free evidence predicted 0->1 and keep those signals without turning them into generic acceptance rules.
- Runtime/setup errors are diagnostic risk signals. Penalize skills that increase timeout or setup fragility, but do not let infrastructure noise override the fixed positive/negative labels.
- The reward agent may recommend accept/revise/reject, but the allowed edit degree is fixed by the harness label above.
- `textgrad_feedback_for_curator` must name the case type, risky or useful stage, and the fixed edit budget to apply.
<!-- skill-evo-policy-update:v0002_from_v0001:end -->

<!-- skill-evo-policy-update:v0003_from_v0002:start -->
## TextGrad Update v0003_from_v0002: Fixed-Harness Reward Calibration

Source: compare `v0002` against `v0001` using the fixed top-level harness labels. These labels are not policy parameters and must not drift across iterations:

- positive: previous reward `0` -> current reward `1`; preserve/distill only.
- strong_negative: previous reward `1` -> current reward `0`; major delete or rewrite is allowed.
- weak_negative: previous reward `0` -> current reward `0`; only bounded targeted rewrite is allowed.

Observed selected cases:

- selected_cases: `0`
- case_types: `{}`
- allowed_edit_degrees: `{}`
- transitions: `{}`
- current_exceptions: `{}`
- mean_selected_delta: `None`

Policy update:

- Strong negatives (`0` cases) are false accepts unless the current failure is clearly infrastructure-only. Reward should become more conservative on the label-free signals that looked good but produced 1->0.
- Weak negatives (`0` cases) are failed attempts, not catastrophic regressions. Reward should prefer `revise`/`abstain` and ask for small targeted evidence, not broad deletion.
- Positives (`0` cases) are true useful skills. Reward should learn which label-free evidence predicted 0->1 and keep those signals without turning them into generic acceptance rules.
- Runtime/setup errors are diagnostic risk signals. Penalize skills that increase timeout or setup fragility, but do not let infrastructure noise override the fixed positive/negative labels.
- The reward agent may recommend accept/revise/reject, but the allowed edit degree is fixed by the harness label above.
- `textgrad_feedback_for_curator` must name the case type, risky or useful stage, and the fixed edit budget to apply.
<!-- skill-evo-policy-update:v0003_from_v0002:end -->

<!-- skill-evo-policy-update:v0004_from_v0003:start -->
## TextGrad Update v0004_from_v0003: Fixed-Harness Reward Calibration

Source: compare `v0003` against `v0002` using the fixed top-level harness labels. These labels are not policy parameters and must not drift across iterations:

- positive: previous reward `0` -> current reward `1`; preserve/distill only.
- strong_negative: previous reward `1` -> current reward `0`; major delete or rewrite is allowed.
- weak_negative: previous reward `0` -> current reward `0`; only bounded targeted rewrite is allowed.

Observed selected cases:

- selected_cases: `1`
- case_types: `{"weak_negative": 1}`
- allowed_edit_degrees: `{"bounded_targeted_rewrite": 1}`
- transitions: `{"still_zero": 1}`
- current_exceptions: `{"none": 1}`
- mean_selected_delta: `0.0`

Policy update:

- Strong negatives (`0` cases) are false accepts unless the current failure is clearly infrastructure-only. Reward should become more conservative on the label-free signals that looked good but produced 1->0.
- Weak negatives (`1` cases) are failed attempts, not catastrophic regressions. Reward should prefer `revise`/`abstain` and ask for small targeted evidence, not broad deletion.
- Positives (`0` cases) are true useful skills. Reward should learn which label-free evidence predicted 0->1 and keep those signals without turning them into generic acceptance rules.
- Runtime/setup errors are diagnostic risk signals. Penalize skills that increase timeout or setup fragility, but do not let infrastructure noise override the fixed positive/negative labels.
- The reward agent may recommend accept/revise/reject, but the allowed edit degree is fixed by the harness label above.
- `textgrad_feedback_for_curator` must name the case type, risky or useful stage, and the fixed edit budget to apply.
<!-- skill-evo-policy-update:v0004_from_v0003:end -->

<!-- skill-evo-policy-update:v0005_from_v0004:start -->
## TextGrad Update v0005_from_v0004: Fixed-Harness Reward Calibration

Source: compare `v0004` against `v0003` using the fixed top-level harness labels. These labels are not policy parameters and must not drift across iterations:

- positive: previous reward `0` -> current reward `1`; preserve/distill only.
- strong_negative: previous reward `1` -> current reward `0`; major delete or rewrite is allowed.
- weak_negative: previous reward `0` -> current reward `0`; only bounded targeted rewrite is allowed.

Observed selected cases:

- selected_cases: `8`
- case_types: `{"weak_negative": 8}`
- allowed_edit_degrees: `{"bounded_targeted_rewrite": 8}`
- transitions: `{"still_zero": 8}`
- current_exceptions: `{"none": 8}`
- mean_selected_delta: `0.0`

Policy update:

- Strong negatives (`0` cases) are false accepts unless the current failure is clearly infrastructure-only. Reward should become more conservative on the label-free signals that looked good but produced 1->0.
- Weak negatives (`8` cases) are failed attempts, not catastrophic regressions. Reward should prefer `revise`/`abstain` and ask for small targeted evidence, not broad deletion.
- Positives (`0` cases) are true useful skills. Reward should learn which label-free evidence predicted 0->1 and keep those signals without turning them into generic acceptance rules.
- Runtime/setup errors are diagnostic risk signals. Penalize skills that increase timeout or setup fragility, but do not let infrastructure noise override the fixed positive/negative labels.
- The reward agent may recommend accept/revise/reject, but the allowed edit degree is fixed by the harness label above.
- `textgrad_feedback_for_curator` must name the case type, risky or useful stage, and the fixed edit budget to apply.
<!-- skill-evo-policy-update:v0005_from_v0004:end -->

<!-- skill-evo-policy-update:v0007_from_v0006:start -->
## TextGrad Update v0007_from_v0006: Fixed-Harness Reward Calibration

Source: compare `v0006` against `v0_noskills` using the fixed top-level harness labels. These labels are not policy parameters and must not drift across iterations:

- positive: previous reward `0` -> current reward `1`; preserve/distill only.
- strong_negative: previous reward `1` -> current reward `0`; major delete or rewrite is allowed.
- weak_negative: previous reward `0` -> current reward `0`; only bounded targeted rewrite is allowed.

Observed selected cases:

- selected_cases: `108`
- case_types: `{"positive": 10, "strong_negative": 20, "weak_negative": 78}`
- allowed_edit_degrees: `{"bounded_targeted_rewrite": 78, "major_delete_or_rewrite": 20, "preserve_distill_only": 10}`
- transitions: `{"improved": 10, "regressed": 20, "still_zero": 78}`
- base_to_current_types: `{"current_error_diagnostic": 67, "positive": 10, "stable_positive": 318, "strong_negative": 20, "unpaired_current_one": 3, "unpaired_current_zero": 4, "weak_negative": 78}`
- previous_to_current_types: `{"current_error_diagnostic": 67, "positive": 10, "stable_positive": 318, "strong_negative": 20, "unpaired_current_one": 3, "unpaired_current_zero": 4, "weak_negative": 78}`
- current_exceptions: `{"none": 108}`
- mean_selected_delta: `-0.09259259259259259`
- reward_agent_cases: `108`
- reward_agent_label_free_decisions: `{"abstain": 28, "accept": 58, "reject": 3, "revise": 19}`
- reward_agent_label_known_errors: `{"calibrated": 42, "false_accept": 14, "false_reject": 3, "under_informed_abstain": 5, "weak_signal_accept": 44}`
- reward_agent_risk_signals: `{"generic_skill_language": 89, "missing_concrete_owner_paths": 1, "missing_focused_validation": 32, "private_or_sandbox_path_leakage": 88}`

Policy update:

- Strong negatives (`20` cases) are false accepts unless the current failure is clearly infrastructure-only. Reward should become more conservative on the label-free signals that looked good but produced 1->0.
- Weak negatives (`78` cases) are failed attempts, not catastrophic regressions. Reward should prefer `revise`/`abstain` and ask for small targeted evidence, not broad deletion.
- Positives (`10` cases) are true useful skills. Reward should learn which label-free evidence predicted 0->1 and keep those signals without turning them into generic acceptance rules.
- Stable positives (`0` policy-only cases) are anchors for preserving already-good behavior. Use them to avoid policy drift, not to create new task edits.
- Runtime/setup errors are diagnostic risk signals. Penalize skills that increase timeout or setup fragility, but do not let infrastructure noise override the fixed positive/negative labels.
- Always compare current behavior against both the previous skill version and the no-skill/base reference. A current improvement over previous is not necessarily a good policy signal if it is still worse than base.
- The reward agent may recommend accept/revise/reject, but the allowed edit degree is fixed by the harness label above.
- `textgrad_feedback_for_curator` must name the case type, risky or useful stage, and the fixed edit budget to apply.

Reward-agent calibration lessons:

- For 0->0 cases, prefer revise or abstain over accept; require a narrow new evidence hook before allowing curation. (`44` cases)
- Keep the existing calibration for cases where static risk and verifier transition agree. (`42` cases)
- Do not accept generic workflow improvements as useful skill evidence; require task-specific reproduction, ownership, or validation details. (`10` cases)
- Abstention should include the missing evidence that would change the decision, especially owner path, reproduction, or validation. (`5` cases)
- For 1->0 regressions, make reward judgment conservative unless the candidate clearly preserves the previous successful path. (`4` cases)
- When a 0->1 case contains concrete files, tests, or patch-shape evidence, preserve that evidence even if surrounding wording is imperfect. (`3` cases)
<!-- skill-evo-policy-update:v0007_from_v0006:end -->

<!-- skill-evo-policy-update:v0008_from_v0007:start -->
## TextGrad Update v0008_from_v0007: Fixed-Harness Reward Calibration

Source: compare `v0007` against `v0006` using the fixed top-level harness labels. These labels are not policy parameters and must not drift across iterations:

- positive: previous reward `0` -> current reward `1`; preserve/distill only.
- strong_negative: previous reward `1` -> current reward `0`; major delete or rewrite is allowed.
- weak_negative: previous reward `0` -> current reward `0`; only bounded targeted rewrite is allowed.

Observed selected cases:

- selected_cases: `56`
- case_types: `{"positive": 9, "strong_negative": 4, "weak_negative": 43}`
- allowed_edit_degrees: `{"bounded_targeted_rewrite": 43, "major_delete_or_rewrite": 4, "preserve_distill_only": 9}`
- transitions: `{"improved": 9, "regressed": 4, "still_zero": 43}`
- base_to_current_types: `{"current_error_diagnostic": 270, "positive": 5, "stable_positive": 161, "strong_negative": 14, "unpaired_current_one": 3, "unpaired_current_zero": 4, "weak_negative": 43}`
- previous_to_current_types: `{"current_error_diagnostic": 270, "positive": 9, "stable_positive": 138, "strong_negative": 4, "unpaired_current_one": 22, "unpaired_current_zero": 14, "weak_negative": 43}`
- current_exceptions: `{"NonZeroAgentExitCodeError": 1, "none": 55}`
- mean_selected_delta: `0.08928571428571429`
- reward_agent_cases: `56`
- reward_agent_label_free_decisions: `{"abstain": 14, "accept": 30, "reject": 2, "revise": 10}`
- reward_agent_label_known_errors: `{"calibrated": 21, "false_accept": 2, "false_reject": 5, "under_informed_abstain": 1, "weak_signal_accept": 27}`
- reward_agent_risk_signals: `{"generic_skill_language": 48, "missing_concrete_owner_paths": 1, "missing_focused_validation": 17, "private_or_sandbox_path_leakage": 44}`

Policy update:

- Strong negatives (`4` cases) are false accepts unless the current failure is clearly infrastructure-only. Reward should become more conservative on the label-free signals that looked good but produced 1->0.
- Weak negatives (`43` cases) are failed attempts, not catastrophic regressions. Reward should prefer `revise`/`abstain` and ask for small targeted evidence, not broad deletion.
- Positives (`9` cases) are true useful skills. Reward should learn which label-free evidence predicted 0->1 and keep those signals without turning them into generic acceptance rules.
- Stable positives (`0` policy-only cases) are anchors for preserving already-good behavior. Use them to avoid policy drift, not to create new task edits.
- Runtime/setup errors are diagnostic risk signals. Penalize skills that increase timeout or setup fragility, but do not let infrastructure noise override the fixed positive/negative labels.
- Always compare current behavior against both the previous skill version and the no-skill/base reference. A current improvement over previous is not necessarily a good policy signal if it is still worse than base.
- The reward agent may recommend accept/revise/reject, but the allowed edit degree is fixed by the harness label above.
- `textgrad_feedback_for_curator` must name the case type, risky or useful stage, and the fixed edit budget to apply.

Reward-agent calibration lessons:

- For 0->0 cases, prefer revise or abstain over accept; require a narrow new evidence hook before allowing curation. (`27` cases)
- Keep the existing calibration for cases where static risk and verifier transition agree. (`21` cases)
- When a 0->1 case contains concrete files, tests, or patch-shape evidence, preserve that evidence even if surrounding wording is imperfect. (`5` cases)
- Do not accept generic workflow improvements as useful skill evidence; require task-specific reproduction, ownership, or validation details. (`2` cases)
- Abstention should include the missing evidence that would change the decision, especially owner path, reproduction, or validation. (`1` cases)
<!-- skill-evo-policy-update:v0008_from_v0007:end -->

<!-- skill-evo-policy-update:v0009_from_v0008:start -->
## TextGrad Update v0009_from_v0008: Fixed-Harness Reward Calibration

Source: compare `v0008` against `v0007` using the fixed top-level harness labels. These labels are not policy parameters and must not drift across iterations:

- positive: previous reward `0` -> current reward `1`; preserve/distill only.
- strong_negative: previous reward `1` -> current reward `0`; major delete or rewrite is allowed.
- weak_negative: previous reward `0` -> current reward `0`; only bounded targeted rewrite is allowed.

Observed selected cases:

- selected_cases: `36`
- case_types: `{"positive": 5, "strong_negative": 2, "weak_negative": 29}`
- allowed_edit_degrees: `{"bounded_targeted_rewrite": 29, "major_delete_or_rewrite": 2, "preserve_distill_only": 5}`
- transitions: `{"improved": 5, "regressed": 2, "still_zero": 29}`
- base_to_current_types: `{"current_error_diagnostic": 31, "positive": 10, "stable_positive": 341, "strong_negative": 14, "unpaired_current_one": 7, "unpaired_current_zero": 7, "weak_negative": 90}`
- previous_to_current_types: `{"current_error_diagnostic": 31, "positive": 5, "stable_positive": 91, "strong_negative": 2, "unpaired_current_one": 262, "unpaired_current_zero": 80, "weak_negative": 29}`
- current_exceptions: `{"none": 36}`
- mean_selected_delta: `0.08333333333333333`
- reward_agent_cases: `36`
- reward_agent_label_free_decisions: `{"abstain": 8, "accept": 19, "reject": 3, "revise": 6}`
- reward_agent_label_known_errors: `{"calibrated": 18, "false_accept": 2, "false_reject": 1, "weak_signal_accept": 15}`
- reward_agent_risk_signals: `{"generic_skill_language": 33, "missing_concrete_owner_paths": 2, "missing_focused_validation": 10, "private_or_sandbox_path_leakage": 26}`

Policy update:

- Strong negatives (`2` cases) are false accepts unless the current failure is clearly infrastructure-only. Reward should become more conservative on the label-free signals that looked good but produced 1->0.
- Weak negatives (`29` cases) are failed attempts, not catastrophic regressions. Reward should prefer `revise`/`abstain` and ask for small targeted evidence, not broad deletion.
- Positives (`5` cases) are true useful skills. Reward should learn which label-free evidence predicted 0->1 and keep those signals without turning them into generic acceptance rules.
- Stable positives (`0` policy-only cases) are anchors for preserving already-good behavior. Use them to avoid policy drift, not to create new task edits.
- Runtime/setup errors are diagnostic risk signals. Penalize skills that increase timeout or setup fragility, but do not let infrastructure noise override the fixed positive/negative labels.
- Always compare current behavior against both the previous skill version and the no-skill/base reference. A current improvement over previous is not necessarily a good policy signal if it is still worse than base.
- The reward agent may recommend accept/revise/reject, but the allowed edit degree is fixed by the harness label above.
- `textgrad_feedback_for_curator` must name the case type, risky or useful stage, and the fixed edit budget to apply.

Reward-agent calibration lessons:

- Keep the existing calibration for cases where static risk and verifier transition agree. (`18` cases)
- For 0->0 cases, prefer revise or abstain over accept; require a narrow new evidence hook before allowing curation. (`15` cases)
- Do not accept generic workflow improvements as useful skill evidence; require task-specific reproduction, ownership, or validation details. (`2` cases)
- When a 0->1 case contains concrete files, tests, or patch-shape evidence, preserve that evidence even if surrounding wording is imperfect. (`1` cases)
<!-- skill-evo-policy-update:v0009_from_v0008:end -->

<!-- skill-evo-policy-update:v0010_from_v0009:start -->
## TextGrad Update v0010_from_v0009: Fixed-Harness Reward Calibration

Source: compare `v0009` against `v0008` using the fixed top-level harness labels. These labels are not policy parameters and must not drift across iterations:

- positive: previous reward `0` -> current reward `1`; preserve/distill only.
- strong_negative: previous reward `1` -> current reward `0`; major delete or rewrite is allowed.
- weak_negative: previous reward `0` -> current reward `0`; only bounded targeted rewrite is allowed.

Observed selected cases:

- selected_cases: `117`
- case_types: `{"positive": 7, "strong_negative": 14, "weak_negative": 96}`
- allowed_edit_degrees: `{"bounded_targeted_rewrite": 96, "major_delete_or_rewrite": 14, "preserve_distill_only": 7}`
- transitions: `{"improved": 7, "regressed": 14, "still_zero": 96}`
- base_to_current_types: `{"current_error_diagnostic": 35, "positive": 11, "stable_positive": 332, "strong_negative": 25, "unpaired_current_one": 3, "unpaired_current_zero": 6, "weak_negative": 88}`
- previous_to_current_types: `{"current_error_diagnostic": 35, "positive": 7, "stable_positive": 328, "strong_negative": 14, "unpaired_current_one": 11, "unpaired_current_zero": 9, "weak_negative": 96}`
- current_exceptions: `{"none": 117}`
- mean_selected_delta: `-0.05982905982905983`
- reward_agent_cases: `117`
- reward_agent_label_free_decisions: `{"abstain": 30, "accept": 59, "reject": 8, "revise": 20}`
- reward_agent_label_known_errors: `{"calibrated": 55, "false_accept": 10, "false_reject": 1, "under_informed_abstain": 4, "weak_signal_accept": 47}`
- reward_agent_risk_signals: `{"generic_skill_language": 98, "missing_concrete_owner_paths": 3, "missing_focused_validation": 43, "private_or_sandbox_path_leakage": 97}`

Policy update:

- Strong negatives (`14` cases) are false accepts unless the current failure is clearly infrastructure-only. Reward should become more conservative on the label-free signals that looked good but produced 1->0.
- Weak negatives (`96` cases) are failed attempts, not catastrophic regressions. Reward should prefer `revise`/`abstain` and ask for small targeted evidence, not broad deletion.
- Positives (`7` cases) are true useful skills. Reward should learn which label-free evidence predicted 0->1 and keep those signals without turning them into generic acceptance rules.
- Stable positives (`0` policy-only cases) are anchors for preserving already-good behavior. Use them to avoid policy drift, not to create new task edits.
- Runtime/setup errors are diagnostic risk signals. Penalize skills that increase timeout or setup fragility, but do not let infrastructure noise override the fixed positive/negative labels.
- Always compare current behavior against both the previous skill version and the no-skill/base reference. A current improvement over previous is not necessarily a good policy signal if it is still worse than base.
- The reward agent may recommend accept/revise/reject, but the allowed edit degree is fixed by the harness label above.
- `textgrad_feedback_for_curator` must name the case type, risky or useful stage, and the fixed edit budget to apply.

Reward-agent calibration lessons:

- Keep the existing calibration for cases where static risk and verifier transition agree. (`55` cases)
- For 0->0 cases, prefer revise or abstain over accept; require a narrow new evidence hook before allowing curation. (`47` cases)
- Do not accept generic workflow improvements as useful skill evidence; require task-specific reproduction, ownership, or validation details. (`8` cases)
- Abstention should include the missing evidence that would change the decision, especially owner path, reproduction, or validation. (`4` cases)
- For 1->0 regressions, make reward judgment conservative unless the candidate clearly preserves the previous successful path. (`2` cases)
- When a 0->1 case contains concrete files, tests, or patch-shape evidence, preserve that evidence even if surrounding wording is imperfect. (`1` cases)
<!-- skill-evo-policy-update:v0010_from_v0009:end -->

<!-- skill-evo-policy-update:v0013_from_v0012:start -->
## TextGrad Update v0013_from_v0012: Fixed-Harness Reward Calibration

Source: compare `v0012` against `v0_noskills` using the fixed top-level harness labels. These labels are not policy parameters and must not drift across iterations:

- positive: previous reward `0` -> current reward `1`; preserve/distill only.
- strong_negative: previous reward `1` -> current reward `0`; major delete or rewrite is allowed.
- weak_negative: previous reward `0` -> current reward `0`; only bounded targeted rewrite is allowed.

Observed selected cases:

- selected_cases: `104`
- case_types: `{"positive": 9, "weak_negative": 95}`
- allowed_edit_degrees: `{"bounded_targeted_rewrite": 95, "preserve_distill_only": 9}`
- transitions: `{"improved": 9, "still_zero": 95}`
- base_to_current_types: `{"current_error_diagnostic": 12, "missing_current": 370, "positive": 9, "unpaired_current_one": 8, "unpaired_current_zero": 6, "weak_negative": 95}`
- previous_to_current_types: `{"current_error_diagnostic": 12, "missing_current": 370, "positive": 9, "unpaired_current_one": 8, "unpaired_current_zero": 6, "weak_negative": 95}`
- current_exceptions: `{"NonZeroAgentExitCodeError": 1, "none": 103}`
- mean_selected_delta: `0.08653846153846154`
- reward_agent_cases: `104`
- reward_agent_label_free_decisions: `{"abstain": 19, "accept": 78, "reject": 1, "revise": 6}`
- reward_agent_label_known_errors: `{"calibrated": 29, "false_reject": 2, "weak_signal_accept": 73}`
- reward_agent_risk_signals: `{"generic_skill_language": 56, "missing_focused_validation": 26, "private_or_sandbox_path_leakage": 84}`

Policy update:

- Strong negatives (`0` cases) are false accepts unless the current failure is clearly infrastructure-only. Reward should become more conservative on the label-free signals that looked good but produced 1->0.
- Weak negatives (`95` cases) are failed attempts, not catastrophic regressions. Reward should prefer `revise`/`abstain` and ask for small targeted evidence, not broad deletion.
- Positives (`9` cases) are true useful skills. Reward should learn which label-free evidence predicted 0->1 and keep those signals without turning them into generic acceptance rules.
- Stable positives (`0` policy-only cases) are anchors for preserving already-good behavior. Use them to avoid policy drift, not to create new task edits.
- Runtime/setup errors are diagnostic risk signals. Penalize skills that increase timeout or setup fragility, but do not let infrastructure noise override the fixed positive/negative labels.
- Always compare current behavior against both the previous skill version and the no-skill/base reference. A current improvement over previous is not necessarily a good policy signal if it is still worse than base.
- The reward agent may recommend accept/revise/reject, but the allowed edit degree is fixed by the harness label above.
- `textgrad_feedback_for_curator` must name the case type, risky or useful stage, and the fixed edit budget to apply.

Reward-agent calibration lessons:

- For 0->0 cases, prefer revise or abstain over accept; require a narrow new evidence hook before allowing curation. (`73` cases)
- Keep the existing calibration for cases where static risk and verifier transition agree. (`29` cases)
- When a 0->1 case contains concrete files, tests, or patch-shape evidence, preserve that evidence even if surrounding wording is imperfect. (`2` cases)
<!-- skill-evo-policy-update:v0013_from_v0012:end -->

<!-- skill-evo-policy-update:v0014_from_v0013:start -->
## TextGrad Update v0014_from_v0013: Fixed-Harness Reward Calibration

Source: compare `v0013` against `v0012` using the fixed top-level harness labels. These labels are not policy parameters and must not drift across iterations:

- positive: previous reward `0` -> current reward `1`; preserve/distill only.
- strong_negative: previous reward `1` -> current reward `0`; major delete or rewrite is allowed.
- weak_negative: previous reward `0` -> current reward `0`; only bounded targeted rewrite is allowed.

Observed selected cases:

- selected_cases: `103`
- case_types: `{"positive": 9, "strong_negative": 7, "weak_negative": 87}`
- allowed_edit_degrees: `{"bounded_targeted_rewrite": 87, "major_delete_or_rewrite": 7, "preserve_distill_only": 9}`
- transitions: `{"improved": 9, "regressed": 7, "still_zero": 87}`
- base_to_current_types: `{"current_error_diagnostic": 17, "missing_current": 370, "positive": 13, "unpaired_current_one": 4, "unpaired_current_zero": 7, "weak_negative": 89}`
- previous_to_current_types: `{"current_error_diagnostic": 17, "other": 370, "positive": 9, "stable_positive": 6, "strong_negative": 7, "unpaired_current_one": 2, "unpaired_current_zero": 2, "weak_negative": 87}`
- current_exceptions: `{"none": 103}`
- mean_selected_delta: `0.019417475728155338`
- reward_agent_cases: `103`
- reward_agent_label_free_decisions: `{"abstain": 18, "accept": 77, "reject": 3, "revise": 5}`
- reward_agent_label_known_errors: `{"calibrated": 28, "false_accept": 6, "false_reject": 2, "under_informed_abstain": 1, "weak_signal_accept": 66}`
- reward_agent_risk_signals: `{"generic_skill_language": 55, "missing_concrete_owner_paths": 2, "missing_focused_validation": 29, "private_or_sandbox_path_leakage": 82}`

Policy update:

- Strong negatives (`7` cases) are false accepts unless the current failure is clearly infrastructure-only. Reward should become more conservative on the label-free signals that looked good but produced 1->0.
- Weak negatives (`87` cases) are failed attempts, not catastrophic regressions. Reward should prefer `revise`/`abstain` and ask for small targeted evidence, not broad deletion.
- Positives (`9` cases) are true useful skills. Reward should learn which label-free evidence predicted 0->1 and keep those signals without turning them into generic acceptance rules.
- Stable positives (`0` policy-only cases) are anchors for preserving already-good behavior. Use them to avoid policy drift, not to create new task edits.
- Runtime/setup errors are diagnostic risk signals. Penalize skills that increase timeout or setup fragility, but do not let infrastructure noise override the fixed positive/negative labels.
- Always compare current behavior against both the previous skill version and the no-skill/base reference. A current improvement over previous is not necessarily a good policy signal if it is still worse than base.
- The reward agent may recommend accept/revise/reject, but the allowed edit degree is fixed by the harness label above.
- `textgrad_feedback_for_curator` must name the case type, risky or useful stage, and the fixed edit budget to apply.

Reward-agent calibration lessons:

- For 0->0 cases, prefer revise or abstain over accept; require a narrow new evidence hook before allowing curation. (`66` cases)
- Keep the existing calibration for cases where static risk and verifier transition agree. (`28` cases)
- For 1->0 regressions, make reward judgment conservative unless the candidate clearly preserves the previous successful path. (`5` cases)
- When a 0->1 case contains concrete files, tests, or patch-shape evidence, preserve that evidence even if surrounding wording is imperfect. (`2` cases)
- Abstention should include the missing evidence that would change the decision, especially owner path, reproduction, or validation. (`1` cases)
- Do not accept generic workflow improvements as useful skill evidence; require task-specific reproduction, ownership, or validation details. (`1` cases)
<!-- skill-evo-policy-update:v0014_from_v0013:end -->

<!-- skill-evo-policy-update:v0015_from_v0014:start -->
## TextGrad Update v0015_from_v0014: Fixed-Harness Reward Calibration

Source: compare `v0014` against `v0013` using the fixed top-level harness labels. These labels are not policy parameters and must not drift across iterations:

- positive: previous reward `0` -> current reward `1`; preserve/distill only.
- strong_negative: previous reward `1` -> current reward `0`; major delete or rewrite is allowed.
- weak_negative: previous reward `0` -> current reward `0`; only bounded targeted rewrite is allowed.

Observed selected cases:

- selected_cases: `95`
- case_types: `{"positive": 5, "strong_negative": 4, "weak_negative": 86}`
- allowed_edit_degrees: `{"bounded_targeted_rewrite": 86, "major_delete_or_rewrite": 4, "preserve_distill_only": 5}`
- transitions: `{"improved": 5, "regressed": 4, "still_zero": 86}`
- base_to_current_types: `{"current_error_diagnostic": 11, "missing_current": 370, "positive": 14, "unpaired_current_one": 9, "unpaired_current_zero": 7, "weak_negative": 89}`
- previous_to_current_types: `{"current_error_diagnostic": 11, "other": 370, "positive": 5, "stable_positive": 12, "strong_negative": 4, "unpaired_current_one": 6, "unpaired_current_zero": 6, "weak_negative": 86}`
- current_exceptions: `{"none": 95}`
- mean_selected_delta: `0.010526315789473684`
- reward_agent_cases: `95`
- reward_agent_label_free_decisions: `{"abstain": 18, "accept": 70, "reject": 3, "revise": 4}`
- reward_agent_label_known_errors: `{"calibrated": 24, "false_accept": 2, "false_reject": 2, "under_informed_abstain": 2, "weak_signal_accept": 65}`
- reward_agent_risk_signals: `{"generic_skill_language": 55, "missing_concrete_owner_paths": 1, "missing_focused_validation": 26, "private_or_sandbox_path_leakage": 77}`

Policy update:

- Strong negatives (`4` cases) are false accepts unless the current failure is clearly infrastructure-only. Reward should become more conservative on the label-free signals that looked good but produced 1->0.
- Weak negatives (`86` cases) are failed attempts, not catastrophic regressions. Reward should prefer `revise`/`abstain` and ask for small targeted evidence, not broad deletion.
- Positives (`5` cases) are true useful skills. Reward should learn which label-free evidence predicted 0->1 and keep those signals without turning them into generic acceptance rules.
- Stable positives (`0` policy-only cases) are anchors for preserving already-good behavior. Use them to avoid policy drift, not to create new task edits.
- Runtime/setup errors are diagnostic risk signals. Penalize skills that increase timeout or setup fragility, but do not let infrastructure noise override the fixed positive/negative labels.
- Always compare current behavior against both the previous skill version and the no-skill/base reference. A current improvement over previous is not necessarily a good policy signal if it is still worse than base.
- The reward agent may recommend accept/revise/reject, but the allowed edit degree is fixed by the harness label above.
- `textgrad_feedback_for_curator` must name the case type, risky or useful stage, and the fixed edit budget to apply.

Reward-agent calibration lessons:

- For 0->0 cases, prefer revise or abstain over accept; require a narrow new evidence hook before allowing curation. (`65` cases)
- Keep the existing calibration for cases where static risk and verifier transition agree. (`24` cases)
- Abstention should include the missing evidence that would change the decision, especially owner path, reproduction, or validation. (`2` cases)
- When a 0->1 case contains concrete files, tests, or patch-shape evidence, preserve that evidence even if surrounding wording is imperfect. (`2` cases)
- For 1->0 regressions, make reward judgment conservative unless the candidate clearly preserves the previous successful path. (`1` cases)
- Do not accept generic workflow improvements as useful skill evidence; require task-specific reproduction, ownership, or validation details. (`1` cases)
<!-- skill-evo-policy-update:v0015_from_v0014:end -->

<!-- skill-evo-policy-update:v0016_from_v0015:start -->
## TextGrad Update v0016_from_v0015: Fixed-Harness Reward Calibration

Source: compare `v0015` against `v0014` using the fixed top-level harness labels. These labels are not policy parameters and must not drift across iterations:

- positive: previous reward `0` -> current reward `1`; preserve/distill only.
- strong_negative: previous reward `1` -> current reward `0`; major delete or rewrite is allowed.
- weak_negative: previous reward `0` -> current reward `0`; only bounded targeted rewrite is allowed.

Observed selected cases:

- selected_cases: `103`
- case_types: `{"positive": 12, "strong_negative": 10, "weak_negative": 81}`
- allowed_edit_degrees: `{"bounded_targeted_rewrite": 81, "major_delete_or_rewrite": 10, "preserve_distill_only": 12}`
- transitions: `{"improved": 12, "regressed": 10, "still_zero": 81}`
- base_to_current_types: `{"current_error_diagnostic": 10, "missing_current": 370, "positive": 17, "unpaired_current_one": 7, "unpaired_current_zero": 10, "weak_negative": 86}`
- previous_to_current_types: `{"current_error_diagnostic": 10, "other": 370, "positive": 12, "stable_positive": 12, "strong_negative": 10, "unpaired_current_zero": 5, "weak_negative": 81}`
- current_exceptions: `{"none": 103}`
- mean_selected_delta: `0.019417475728155338`
- reward_agent_cases: `103`
- reward_agent_label_free_decisions: `{"abstain": 20, "accept": 74, "reject": 2, "revise": 7}`
- reward_agent_label_known_errors: `{"calibrated": 29, "false_accept": 7, "false_reject": 3, "under_informed_abstain": 3, "weak_signal_accept": 61}`
- reward_agent_risk_signals: `{"generic_skill_language": 58, "missing_concrete_owner_paths": 1, "missing_focused_validation": 30, "private_or_sandbox_path_leakage": 84}`

Policy update:

- Strong negatives (`10` cases) are false accepts unless the current failure is clearly infrastructure-only. Reward should become more conservative on the label-free signals that looked good but produced 1->0.
- Weak negatives (`81` cases) are failed attempts, not catastrophic regressions. Reward should prefer `revise`/`abstain` and ask for small targeted evidence, not broad deletion.
- Positives (`12` cases) are true useful skills. Reward should learn which label-free evidence predicted 0->1 and keep those signals without turning them into generic acceptance rules.
- Stable positives (`0` policy-only cases) are anchors for preserving already-good behavior. Use them to avoid policy drift, not to create new task edits.
- Runtime/setup errors are diagnostic risk signals. Penalize skills that increase timeout or setup fragility, but do not let infrastructure noise override the fixed positive/negative labels.
- Always compare current behavior against both the previous skill version and the no-skill/base reference. A current improvement over previous is not necessarily a good policy signal if it is still worse than base.
- The reward agent may recommend accept/revise/reject, but the allowed edit degree is fixed by the harness label above.
- `textgrad_feedback_for_curator` must name the case type, risky or useful stage, and the fixed edit budget to apply.

Reward-agent calibration lessons:

- For 0->0 cases, prefer revise or abstain over accept; require a narrow new evidence hook before allowing curation. (`61` cases)
- Keep the existing calibration for cases where static risk and verifier transition agree. (`29` cases)
- For 1->0 regressions, make reward judgment conservative unless the candidate clearly preserves the previous successful path. (`5` cases)
- Abstention should include the missing evidence that would change the decision, especially owner path, reproduction, or validation. (`3` cases)
- When a 0->1 case contains concrete files, tests, or patch-shape evidence, preserve that evidence even if surrounding wording is imperfect. (`3` cases)
- Do not accept generic workflow improvements as useful skill evidence; require task-specific reproduction, ownership, or validation details. (`2` cases)
<!-- skill-evo-policy-update:v0016_from_v0015:end -->
