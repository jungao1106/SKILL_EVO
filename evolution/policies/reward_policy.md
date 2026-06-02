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

