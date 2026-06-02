---
name: reproduce_bug
description: "Task-specific reproduce skill from swe-bench/matplotlib__matplotlib-14623. Use when Issue reports inability to invert log scale axis using limits.."
---

# reproduce_bug

- Memory version: `v0008`
- Parent version: `v0007`
- Source task: `swe-bench/matplotlib__matplotlib-14623`
- Source job: `smoke_skills_evo_novita_glm51_openai_compat_skills`
- Stage: `reproduce`
- Reward: `1.0`

## Trigger

Issue reports inability to invert log scale axis using limits.

## Actions

1. Create a Python script using matplotlib to plot with inverted y-limits on a log scale and print the resulting limits.

## Evidence

Console output showing that the log scale axis limits are not inverted despite set_ylim(y.max(), y.min()).

## Stop Condition

Bug behavior confirmed (axis not inverted for log scale).

## Source Skill Files


## Script Resources

- none

## Bundled Distilled Scripts

- `scripts/reproduce_bug.py`: Verify that setting inverted limits on a log scale axis does not invert the axis.


## Control Points

- Trigger: Bug reproduction script runs but doesn't clearly show the issue.
- Action: Print the actual ylim values to confirm they are not inverted for log scale.
- Evidence: Console output of ylim values.
- Stop: Confirmed that ylim is not inverted for log scale.

## Harness Notes


## Avoid

