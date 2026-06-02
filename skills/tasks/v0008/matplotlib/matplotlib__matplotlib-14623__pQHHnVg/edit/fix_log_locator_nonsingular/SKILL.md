---
name: fix_log_locator_nonsingular
description: "Task-specific edit skill from swe-bench/matplotlib__matplotlib-14623. Use when LogLocator.nonsingular needs to handle inverted limits.."
---

# fix_log_locator_nonsingular

- Memory version: `v0008`
- Parent version: `v0007`
- Source task: `swe-bench/matplotlib__matplotlib-14623`
- Source job: `smoke_skills_evo_novita_glm51_openai_compat_skills`
- Stage: `edit`
- Reward: `1.0`

## Trigger

LogLocator.nonsingular needs to handle inverted limits.

## Actions

1. Modify nonsingular in lib/matplotlib/ticker.py to swap vmin and vmax if vmin > vmax, process them, and swap back before returning.

## Evidence

The edited code in ticker.py.

## Stop Condition

Code modified to handle inverted limits.

## Source Skill Files

- swe_agent_skills/fix-build-agentops

## Script Resources

- none


## Control Points

## Harness Notes


## Avoid

