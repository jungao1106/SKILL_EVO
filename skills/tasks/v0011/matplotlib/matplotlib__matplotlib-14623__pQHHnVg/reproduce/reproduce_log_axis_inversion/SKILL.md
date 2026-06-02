---
name: reproduce_log_axis_inversion
description: "Task-specific reproduce skill from swe-bench/matplotlib__matplotlib-14623. Use when Issue reports that setting inverted limits on a log scale axis does not invert the axis.."
---

# reproduce_log_axis_inversion

- Memory version: `v0011`
- Parent version: `v0010`
- Source task: `swe-bench/matplotlib__matplotlib-14623`
- Source job: `smoke_skills_evo_novita_glm51_openai_compat_skills`
- Stage: `reproduce`
- Reward: `1.0`

## Trigger

Issue reports that setting inverted limits on a log scale axis does not invert the axis.

## Actions

1. Create a Python script using matplotlib to plot with inverted limits on both linear and log scales.

## Evidence

Visual or numerical confirmation that linear scale inverts but log scale does not.

## Stop Condition

Bug behavior confirmed.

## Source Skill Files

- swe_agent_skills/fix-build-agentops/testing-python/SKILL.md

## Script Resources

- none

## Bundled Distilled Scripts

- `scripts/reproduce_inverted_log_axis.py`: Verify that inverted limits work for linear but not log scale.


## Control Points

- Trigger: Reproduction script runs.
- Action: Check printed ylims. Linear should be inverted (max, min), log should incorrectly be (min, max).
- Evidence: Printed ylims for both scales.
- Stop: Log scale ylim is not inverted.

## Harness Notes


## Avoid

