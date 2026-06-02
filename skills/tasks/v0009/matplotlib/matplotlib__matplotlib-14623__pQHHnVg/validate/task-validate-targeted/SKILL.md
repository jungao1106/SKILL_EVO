---
name: task-validate-targeted
description: "Task-specific validate skill from swe-bench/matplotlib__matplotlib-14623. Use when A patch is applied.."
---

# task-validate-targeted

- Memory version: `v0009`
- Parent version: `v0008`
- Source task: `swe-bench/matplotlib__matplotlib-14623`
- Source job: `smoke_skills_evo_novita_glm51_openai_compat_skills`
- Stage: `validate`
- Reward: `1.0`

## Trigger

A patch is applied.

## Actions

1. cd /testbed && python -m pytest lib/matplotlib/tests/test_ticker.py -x -q -k "log" 2>&1 | tail -30
2. cd /testbed && python -m pytest lib/matplotlib/tests/test_scale.py -x -q 2>&1 | tail -20

## Evidence

Passing focused command or clear remaining failure.

## Stop Condition

The reproduction passes and no nearby regression is visible.

## Source Skill Files


## Script Resources

- none


## Control Points

## Harness Notes


## Avoid

