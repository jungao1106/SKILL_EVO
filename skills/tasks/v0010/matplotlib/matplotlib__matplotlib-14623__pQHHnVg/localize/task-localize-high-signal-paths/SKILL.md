---
name: task-localize-high-signal-paths
description: "Task-specific localize skill from swe-bench/matplotlib__matplotlib-14623. Use when The reproduction points to a behavior-owning module.."
---

# task-localize-high-signal-paths

- Memory version: `v0010`
- Parent version: `v0009`
- Source task: `swe-bench/matplotlib__matplotlib-14623`
- Source job: `smoke_skills_evo_novita_glm51_openai_compat_skills`
- Stage: `localize`
- Reward: `1.0`

## Trigger

The reproduction points to a behavior-owning module.

## Actions

1. Inspect high-signal paths: lib/matplotlib/ticker.py, lib/matplotlib, lib/matplotlib/axes/_base.py, lib/matplotlib/transforms.py

## Evidence

The function or branch that owns the incorrect behavior.

## Stop Condition

One small source location explains the observed failure.

## Source Skill Files


## Script Resources

- none


## Control Points

## Harness Notes


## Avoid

