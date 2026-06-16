# Skill Evolution Harness Policy v1

This file defines fixed top-level labels and edit budgets for policy iteration.
It is not a TextGrad target. Curator and reward policies may learn from the
examples selected by this harness, but they must not change these labels or edit
budgets.

## Fixed Case Labels

| Previous reward | Current reward | Case type | Training meaning |
| ---: | ---: | --- | --- |
| 0 | 1 | positive | The current skill helped. Preserve and distill the useful behavior. |
| 1 | 0 | strong_negative | The current skill harmed a previously solved task. Treat as a false accept. |
| 0 | 0 | weak_negative | The current skill failed to help. Treat as a weak negative, not a catastrophic regression. |

Cases with missing rewards or infrastructure exceptions may be logged as
diagnostic risk cases, but they must not override the fixed labels above when a
paired 0/1 reward transition is available.

## Fixed Edit Degree

| Case type | Allowed edit degree | Max stage edits | Allowed curator operations |
| --- | --- | ---: | --- |
| positive | preserve_distill_only | 1 | preserve_stage_skill, compress_stage_skill |
| strong_negative | major_delete_or_rewrite | 5 | delete_stage_skill, rewrite_stage_skill, add_stop_condition, disable_task_skill |
| weak_negative | bounded_targeted_rewrite | 2 | rewrite_stage_skill, add_stop_condition, compress_stage_skill |
| current_error_diagnostic | diagnostic_only | 1 | add_stop_condition, compress_stage_skill |
| unpaired_current_zero | diagnostic_only | 1 | add_stop_condition, compress_stage_skill |

## Policy Update Use

- Reward policy update uses these labels to calibrate label-free accept/reject decisions.
- Curator policy update uses these labels to decide how much it may edit the
  next skill version.
- The edit budget is a harness constraint, not a learned preference.
