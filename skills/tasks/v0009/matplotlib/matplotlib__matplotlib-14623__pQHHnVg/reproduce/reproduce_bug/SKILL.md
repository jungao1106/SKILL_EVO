---
name: reproduce_bug
description: "Task-specific reproduce skill from swe-bench/matplotlib__matplotlib-14623. Use when Issue reports inverted axis limits not working for log scale.."
---

# reproduce_bug

- Memory version: `v0009`
- Parent version: `v0008`
- Source task: `swe-bench/matplotlib__matplotlib-14623`
- Source job: `smoke_skills_evo_novita_glm51_openai_compat_skills`
- Stage: `reproduce`
- Reward: `1.0`

## Trigger

Issue reports inverted axis limits not working for log scale.

## Actions

1. Run the provided matplotlib script with set_ylim(y.max(), y.min()) and set_yscale('log').

## Evidence

Visual or programmatic confirmation that the y-axis is not inverted for log scale.

## Stop Condition

Bug behavior confirmed.

## Source Skill Files


## Script Resources

- none

## Bundled Distilled Scripts

- `scripts/reproduce_inverted_log_axis.py`: Verify that setting inverted limits on a log scale axis fails to invert the axis.


## Control Points

- Trigger: Bug reproduction fails to show the issue.
- Action: Check matplotlib version or manually inspect limits.
- Evidence: Output of get_ylim().
- Stop: Confirmed that get_ylim() returns non-inverted limits for log scale.

## Harness Notes


## Avoid

