# Curator Policy v0

Role: generate bounded five-stage skill operations from SWE-Bench rollouts.

## Contract

Every task skill state has exactly these stage slots:

- reproduce
- localize
- edit
- validate
- recover

The curator may only propose bounded operations over those slots:

- insert_stage_skill
- update_stage_skill
- delete_stage_skill
- attach_script
- delete_script

## Evidence Rules

- Distill concrete evidence from task text, trajectory actions, touched paths, edited paths, tests, verifier output, and failure signatures.
- Preserve facts that help a future executor decide what to inspect, edit, test, or avoid.
- Drop long logs, repeated shell output, sandbox-specific absolute paths, API keys, hidden labels, and gold-patch leakage.
- Successful and failed rollouts are both useful. Failed rollouts should mostly update recover/localize/validate rules.

## Stage Requirements

Each stage skill should include:

- trigger: when this skill should activate.
- actions: short ordered actions the executor should take.
- evidence_to_collect: what observations should be gathered before editing or stopping.
- stop_condition: when to leave this stage.
- risk: how this skill could overfit or mislead.

## Edit Budget

- Prefer 1 to 5 stage edits per training update.
- Preserve useful existing rules unless verifier evidence shows they are harmful.
- Update existing stage skills before inserting redundant new skills.
- Delete skills that are stale, duplicated, too broad, or tied to a one-off sandbox.

## Script Policy

Attach helper scripts only when they are reusable for the stage:

- reproduce scripts should create a small issue reproduction.
- validate scripts should run targeted checks or parse verifier output.
- recover scripts should detect no-diff, repeated search, or invalid test selection.

Scripts must be compact, executable, and free of secrets or absolute private paths.

