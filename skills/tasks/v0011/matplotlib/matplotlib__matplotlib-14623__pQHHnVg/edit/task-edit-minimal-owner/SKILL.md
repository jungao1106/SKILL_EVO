---
name: task-edit-minimal-owner
description: "Task-specific edit skill from swe-bench/matplotlib__matplotlib-14623. Use when A narrow owner location is identified.."
---

# task-edit-minimal-owner

- Memory version: `v0011`
- Parent version: `v0010`
- Source task: `swe-bench/matplotlib__matplotlib-14623`
- Source job: `smoke_skills_evo_novita_glm51_openai_compat_skills`
- Stage: `edit`
- Reward: `1.0`

## Trigger

A narrow owner location is identified.

## Actions

1. Patch only the owner location: lib/matplotlib/ticker.py

## Evidence

A small diff preserving existing API and style.

## Stop Condition

The diff addresses the reproduced behavior without broad rewrites.

## Source Skill Files


## Script Resources

- none


## Control Points

## Harness Notes


## Avoid

