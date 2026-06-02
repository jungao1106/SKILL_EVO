---
name: search_log_scale_limits
description: "Task-specific localize skill from swe-bench/matplotlib__matplotlib-14623. Use when Need to find where log scale limits are processed.."
---

# search_log_scale_limits

- Memory version: `v0008`
- Parent version: `v0007`
- Source task: `swe-bench/matplotlib__matplotlib-14623`
- Source job: `smoke_skills_evo_novita_glm51_openai_compat_skills`
- Stage: `localize`
- Reward: `1.0`

## Trigger

Need to find where log scale limits are processed.

## Actions

1. Grep for limit_range_for_scale, nonsingular, set_ylim in matplotlib codebase.

## Evidence

File and line numbers where limits are adjusted for scale.

## Stop Condition

Identify LogLocator.nonsingular in lib/matplotlib/ticker.py as the culprit.

## Source Skill Files


## Script Resources

- none


## Control Points

- Trigger: Search returns multiple files (scale.py, _base.py, ticker.py).
- Action: Read the nonsingular method in ticker.py and scale.py to understand how they handle vmin > vmax.
- Evidence: Code snippets showing nonsingular logic.
- Stop: Find that LogLocator.nonsingular does not swap vmin/vmax when vmin > vmax.

## Harness Notes


## Avoid

