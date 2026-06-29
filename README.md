# SKILLS_EVO

Minimal inference scaffold extracted from `Marcronv1_SWE`.

It keeps:

- Harbor as the benchmark harness.
- E2B as the remote sandbox.
- Pi as the only agent adapter.
- A generic `openai` provider driven by `OPENAI_COMPAT_BASE_URL`, `OPENAI_COMPAT_MODEL`, and `OPENAI_COMPAT_API`.
- A separate `tinker` provider profile.
- `--use-skills` / `--no-skills` selection.
- The existing Pi tool-use harness in `scripts/check_pi_tool_harness.py`.
- One retained full job: `jobs/merged_pi_novita_glm51_skills_trace_stagger_swebench_20260519_1616`.

Directory naming:

- `skills/accepted/<version>/...` stores accepted repo scaffold and failure-mode skills.
- `swe_agent_skills/` is the retained legacy SWE-agent skill source. Evolution summaries may distill resources from a legacy skill only when a trace actually read that skill.
- Pi packages `skills/accepted/<active-memory-version>` into the sandbox when `--use-skills` is enabled and that directory exists.

## Setup

```bash
cd /vePFS-Mindverse/user/intern/jungao/SKILLS_EVO
cp .env.example .env
pip install -r requirements.txt
npm install -g @earendil-works/pi-coding-agent
```

Fill `.env` with provider and E2B credentials.

## Run

GLM 5.1 through the generic OpenAI-compatible profile:

```bash
LLM_PROVIDER=openai python scripts/run_benchmark.py \
  --use-skills \
  --n-tasks 1 \
  --job-name smoke_pi_openai_glm51_skills
```

GPT 5.5 or Claude use the same `openai` provider. Change only:

```bash
OPENAI_COMPAT_BASE_URL=...
OPENAI_COMPAT_MODEL=...
OPENAI_COMPAT_API=openai-completions
```

For a Responses-shaped GPT 5.5 endpoint, set `OPENAI_COMPAT_API=openai-responses`.

Tinker:

```bash
LLM_PROVIDER=tinker python scripts/run_benchmark.py \
  --use-skills \
  --n-tasks 1 \
  --job-name smoke_pi_tinker_skills
```

No skills:

```bash
python scripts/run_benchmark.py --no-skills --n-tasks 1 --job-name smoke_pi_noskills
```

## Terminal-Bench 2.0

Terminal-Bench 2.0 uses Harbor's package dataset path and E2B by default. The
Pi agent switches to a Terminal-Bench prompt when launched through this runner.

Small smoke run:

```bash
LLM_PROVIDER=openai python scripts/run_terminal_bench.py \
  --n-tasks 1 \
  --job-name tb2_smoke_pi_openai_noskills
```

Run selected tasks:

```bash
python scripts/run_terminal_bench.py \
  --include-task-name '<task-glob>' \
  --concurrency 2 \
  --job-name tb2_selected_pi_openai
```

Useful options:

- `--dataset terminal-bench/terminal-bench-2@latest` selects the TB2 package dataset.
- `--environment e2b` is the default; use `--environment docker` only for local
  Docker debugging.
- `--no-skills` is the default for TB2 because the retained skill memory is scoped
  to SWE-Bench task ids.
- Results and configs are written under `jobs/`, `logs/`, and `configs/` with the
  given job name.

To only render the Harbor config without downloading or running tasks:

```bash
python scripts/run_terminal_bench.py --dry-run --n-tasks 1
```

## Harness

Check Pi native tool-use traces:

```bash
python scripts/check_pi_tool_harness.py \
  --job-dir jobs/merged_pi_novita_glm51_skills_trace_stagger_swebench_20260519_1616
```

Smoke-check model endpoints without launching a benchmark:

```bash
python scripts/check_openai_compat.py
python scripts/check_tinker_models.py
```

## Skill/Harness Evolution Memory

`scripts/update_skill_harness_memory.py` builds an append-only, versioned memory
from completed Pi traces. Each memory entry is one SWE task evidence record,
organized by public signals such as touched paths, edited paths, focused tests,
case label, and failure signature. Task evidence is not a downstream skill.
Higher-level training artifacts are written under
`skills/accepted/<version>/_repos/...` and
`skills/accepted/<version>/_failure_modes/...`.

```bash
set -a; . .env; set +a
python scripts/update_skill_harness_memory.py \
  --job-dir jobs/smoke_skills_evo_novita_glm51_openai_compat_skills \
  --summarize-with-backbone
```

The backbone summarizer uses the provider/model recorded in each trace metadata.
For each task, it receives a bounded trace digest plus trace-selected
skill/script resources: task excerpt, tool sequence, touched/edited paths,
tests, reward, selected SKILL.md excerpts, and selected `*.py`/`*.sh` excerpts.
The primary output is task evidence; legacy task/stage SKILL.md generation is
available only through an explicit compatibility flag.
Set
`PI_USE_SKILL_HARNESS_MEMORY=false` to disable prompt injection, or set
`PI_SKILL_HARNESS_MEMORY_PATH` to choose a different memory file.

## v2 Skill Evolution Loop

The v2 scaffold treats SWE-Bench Verified as a direct training/evaluation
stream:

1. run a no-skills baseline on the selected benchmark tasks;
2. build task evidence from the baseline trajectories;
3. evaluate the same tasks with the evidence memory and accepted skill pack;
4. write a before/after score report.

This setup supports full-distribution skill evolution on SWE-Bench Verified:
use `--all-verified` when running against the complete Verified task selection.

Full Verified run:

```bash
python scripts/run_skill_evo_verified.py \
  --run-name verified_glm51_full_round1 \
  --dataset swe-bench/swe-bench-verified@2 \
  --provider openai \
  --all-verified \
  --concurrency 2 \
  --summarize-with-backbone
```

Small round:

```bash
python scripts/run_skill_evo_verified.py \
  --run-name verified_glm51_round1 \
  --dataset swe-bench/swe-bench-verified@2 \
  --provider openai \
  --n-tasks 10 \
  --concurrency 2
```

The run writes orchestration artifacts under `run_logs/evolution/<run-name>/`:

- `training/generator_policy.md`
- `training/evaluator_policy.md`
- `training/skill_harness_memory.json`
- `training/task_evidence_cards/<version>/...`
- `evaluation/score_report.json`
- `evaluation/score_report.md`

Accepted skills are archived under versioned directories:

- `skills/accepted/<version>/...`
- `skills/accepted/VERSIONS.json`

The runner picks the next unused `vXXXX` version by scanning existing
`skills/accepted` directories, the version index, and git-tracked historical
versions. To pin a version explicitly:

```bash
python scripts/run_skill_evo_verified.py \
  --run-name verified_glm51_round1 \
  --skill-version-id v0012 \
  --n-tasks 10
```

For a smoke test without launching E2B:

```bash
python scripts/run_skill_evo_verified.py \
  --run-name dryrun_demo \
  --n-tasks 1 \
  --dry-run
```

## SWEGym Transfer Loop

The SWEGym loop implements the proposed flow:

1. train on SWEGym training repos;
2. validate on a repo-isolated SWEGym holdout, selected to be closest to 5% of
   the tasks.

Smoke dry-run:

```bash
python scripts/run_swegym_skill_evo_loop.py \
  --run-name swegym_loop_dryrun \
  --smoke \
  --dry-run \
  --no-wandb
```

Pi + Novita GLM 5.1 smoke run:

```bash
WANDB_API_KEY=... \
OPENAI_COMPAT_API_KEY=... \
E2B_API_KEY=... \
python scripts/run_swegym_skill_evo_loop.py \
  --run-name swegym_loop_smoke_glm51 \
  --smoke \
  --provider openai \
  --provider-base-url https://api.novita.ai/v3/openai \
  --provider-model zai-org/glm-5.1 \
  --provider-api openai-completions \
  --concurrency 1 \
  --summarize-with-backbone
```

Artifacts are written under `run_logs/swegym_skill_evo/<run-name>/`. W&B logging
is enabled unless `--no-wandb` or `--dry-run` is passed.

To reuse existing jobs while testing the orchestration:

```bash
python scripts/run_skill_evo_verified.py \
  --run-name reuse_existing \
  --baseline-job-dir jobs/<baseline-job> \
  --eval-job-dir jobs/<eval-job>
```
