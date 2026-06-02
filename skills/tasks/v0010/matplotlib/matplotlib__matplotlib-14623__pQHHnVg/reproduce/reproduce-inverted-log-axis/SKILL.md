---
name: reproduce-inverted-log-axis
description: "Task-specific reproduce skill from swe-bench/matplotlib__matplotlib-14623. Use when Issue reports inverted axis limits not working for log scale.."
---

# reproduce-inverted-log-axis

- Memory version: `v0010`
- Parent version: `v0009`
- Source task: `swe-bench/matplotlib__matplotlib-14623`
- Source job: `smoke_skills_evo_novita_glm51_openai_compat_skills`
- Stage: `reproduce`
- Reward: `1.0`

## Trigger

Issue reports inverted axis limits not working for log scale.

## Actions

1. Create a matplotlib plot with log scale and inverted limits (e.g., set_ylim(100000, 1)), print the resulting limits.

## Evidence

The resulting axis limits are not inverted.

## Stop Condition

Bug behavior confirmed.

## Source Skill Files

- swe_agent_skills/fix-build-agentops/testing-python/SKILL.md

## Script Resources

- none

## Bundled Distilled Scripts

- `scripts/reproduce_inverted_log.py`: Verify that inverted limits on a log scale axis are not respected.


## Control Points

## Harness Notes


## Avoid

