---
name: locate_log_locator_nonsingular
description: "Task-specific localize skill from swe-bench/matplotlib__matplotlib-14623. Use when Need to find where log scale limits are processed and potentially forced to be non-inverted.."
---

# locate_log_locator_nonsingular

- Memory version: `v0011`
- Parent version: `v0010`
- Source task: `swe-bench/matplotlib__matplotlib-14623`
- Source job: `smoke_skills_evo_novita_glm51_openai_compat_skills`
- Stage: `localize`
- Reward: `1.0`

## Trigger

Need to find where log scale limits are processed and potentially forced to be non-inverted.

## Actions

1. Grep for limit_range_for_scale and nonsingular in matplotlib. Read ticker.py focusing on LogLocator.nonsingular.

## Evidence

Code of LogLocator.nonsingular showing it doesn't handle vmin > vmax.

## Stop Condition

Found LogLocator.nonsingular in lib/matplotlib/ticker.py which returns vmin, vmax without respecting inversion.

## Source Skill Files

- swe_agent_skills/fix-build-agentops/testing-python/SKILL.md

## Script Resources

- none


## Control Points

- Trigger: Searching for limit handling in log scale.
- Action: Read lib/matplotlib/ticker.py around LogLocator.nonsingular.
- Evidence: The implementation of nonsingular method.
- Stop: Identified that nonsingular lacks logic to preserve inverted limits.

## Harness Notes


## Avoid

