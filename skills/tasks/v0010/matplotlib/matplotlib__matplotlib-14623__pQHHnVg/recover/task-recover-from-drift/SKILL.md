---
name: task-recover-from-drift
description: "Task-specific recover skill from swe-bench/matplotlib__matplotlib-14623. Use when Search, editing, or validation repeats without new evidence.."
---

# task-recover-from-drift

- Memory version: `v0010`
- Parent version: `v0009`
- Source task: `swe-bench/matplotlib__matplotlib-14623`
- Source job: `smoke_skills_evo_novita_glm51_openai_compat_skills`
- Stage: `recover`
- Reward: `1.0`

## Trigger

Search, editing, or validation repeats without new evidence.

## Actions

1. Stop the loop, restate the failing observation, and re-localize from the strongest evidence.

## Evidence

The last command that changed the hypothesis or invalidated it.

## Stop Condition

A new concrete owner hypothesis is available or the previous edit is reverted.

## Source Skill Files


## Script Resources

- none


## Control Points

## Harness Notes


## Avoid

