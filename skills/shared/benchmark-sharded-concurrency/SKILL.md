---
name: benchmark-sharded-concurrency
description: "Use when running large benchmark or evaluation workloads such as SWE-Bench, SWE-Gym, Harbor, E2B sandbox jobs, Pi agent runs, or OpenAI-compatible LLM inference where one runner process with very high concurrency is slow or unstable. Apply sharded multi-process execution: split the task set into ranges, run several independent runner processes, keep per-process concurrency moderate, set explicit timeouts, and monitor results/rewards without downloading heavy traces."
---

# Benchmark Sharded Concurrency

## Core Pattern

Prefer `N shards x moderate concurrency` over `1 process x huge concurrency` when a benchmark runner coordinates sandbox creation, agent execution, verification, logging, and network calls.

Example:

```text
Bad shape:  1 process x concurrency 100
Good shape: 5 processes x concurrency 20
```

The total nominal concurrency is the same, but the good shape gives each process its own event loop, client state, connection pool, log stream, and scheduler queue. This reduces head-of-line blocking and lets the OS distribute runner overhead across CPU cores.

## When To Use

Use this pattern when:

- A single benchmark runner becomes slow, bursty, or timeout-heavy at high concurrency.
- Tasks include sandbox/container lifecycle work, verifier jobs, repository checkout/builds, or streaming LLM calls.
- The backend model can handle the total request rate, but the local runner or orchestration layer cannot.
- You need predictable progress and partial failure isolation.

Do not assume this always beats a single process. Re-test when the backend, runner, dataset, or machine changes.

## Operating Rules

1. Split the dataset into stable, non-overlapping shards such as `1-100`, `101-200`, ..., `401-500`.
2. Keep per-process concurrency at the measured sweet spot. Start with `20` for Pi + Harbor/E2B style workloads unless local evidence says otherwise.
3. Set an explicit agent timeout, commonly `900` seconds for SWE-style repair tasks.
4. Give each shard a unique job name that includes provider, model, range, concurrency, timeout, and date.
5. Run each shard in its own tmux session or process group.
6. Stagger shard launches when the environment provider has sandbox/container startup limits. Start with `--stagger-sec 120` for E2B Pi/SWE-Bench bursts if many trials stay in `starting environment...`.
7. Prefer result-only mode when trajectories are not needed. Keep `result.json`, verifier output, reward, and exception files; avoid trace/log artifacts that increase I/O and storage pressure. Do not use result-only when intermediate traces must be preserved.
8. Monitor progress per shard with result count, reward count, reward distribution, exception count, and latest log timestamp.

## Why It Speeds Up

The bottleneck is often the runner side, not the model server:

- A single Python process has one event loop and shared coordination path.
- High single-process concurrency increases scheduler pressure, log flushing, filesystem churn, HTTP connection contention, and retry pileups.
- Slow sandbox creation or verifier phases can block unrelated tasks behind the same runner.
- Sharding creates multiple event loops, connection pools, and scheduler queues.
- Failures and timeouts stay local to one shard instead of slowing the whole queue.

The target is not "maximum configured concurrency"; it is "maximum completed results per hour with acceptable failure rate."

## Launch Helper

Use `scripts/launch_sharded_benchmark.py` when the benchmark runner accepts task-name/args files and can be launched from the shell.

Dry-run an execution plan first:

```bash
python skills/shared/benchmark-sharded-concurrency/scripts/launch_sharded_benchmark.py \
  --runner scripts/run_benchmark.py \
  --dataset swe-bench/swe-bench-verified@2 \
  --task-list data/my_tasks.txt \
  --ranges 1-100,101-200,201-300,301-400,401-500 \
  --job-prefix pi_novita_glm51_swebench \
  --agent pi \
  --concurrency 20 \
  --timeout 900 \
  --dry-run
```

Then launch:

```bash
python skills/shared/benchmark-sharded-concurrency/scripts/launch_sharded_benchmark.py \
  --runner scripts/run_benchmark.py \
  --dataset swe-bench/swe-bench-verified@2 \
  --task-list data/my_tasks.txt \
  --ranges 1-100,101-200,201-300,301-400,401-500 \
  --job-prefix pi_novita_glm51_swebench \
  --agent pi \
  --concurrency 20 \
  --timeout 900 \
  --result-only
```

For runners that do not accept a task-name file but do accept repeatable task flags, use repeat mode:

```bash
python skills/shared/benchmark-sharded-concurrency/scripts/launch_sharded_benchmark.py \
  --runner scripts/run_benchmark.py \
  --dataset swe-bench/swe-bench-verified@2 \
  --task-list data/my_tasks.txt \
  --ranges 1-100,101-200,201-300,301-400,401-500 \
  --job-prefix pi_novita_glm51_swebench \
  --workdir /path/to/repo \
  --concurrency 20 \
  --timeout 900 \
  --task-arg-mode repeat \
  --task-name-arg=--include-task-name \
  --stagger-sec 120 \
  --plan-file run_logs/pi_novita_glm51_swebench_plan.jsonl
```

If the local runner has different CLI flags, use the script's dry-run output as a template and adapt the command manually.

## Monitoring

Track each shard independently:

```bash
tmux ls
find jobs/<job-name> -mindepth 2 -maxdepth 2 -name result.json | wc -l
find jobs/<job-name> -mindepth 3 -maxdepth 3 -path '*/verifier/reward.txt' | wc -l
find jobs/<job-name> -mindepth 2 -maxdepth 2 -name exception.txt | wc -l
tail -n 50 logs/<job-name>.log
```

Report status as:

```text
range | session | result count | exception count | reward=1 | reward=0 | latest log time
```

For result-only runs, verify that heavy trace artifacts are absent:

```bash
find jobs/<job-name> \( -name pi-events.jsonl -o -name trajectory.json -o -name sharegpt.json \) | wc -l
```

## Merge Completed Shards

After a sharded run finishes, merge the shard job directories into one canonical job directory before reporting final results.

Use `scripts/merge_benchmark_jobs.py` from the repository root. It copies or hardlinks trial directories, deduplicates by `task_name`, rewrites copied `result.json`/`config.json` path metadata, regenerates the merged job-level `result.json`, and writes `merge_manifest.jsonl`.

Dry-run first:

```bash
python skills/shared/benchmark-sharded-concurrency/scripts/merge_benchmark_jobs.py \
  --jobs 'jobs/<job-prefix>_*' \
  --output-job merged_<job-prefix> \
  --copy-mode hardlink \
  --dry-run
```

Then write the merged job:

```bash
python skills/shared/benchmark-sharded-concurrency/scripts/merge_benchmark_jobs.py \
  --jobs 'jobs/<job-prefix>_*' \
  --output-job merged_<job-prefix> \
  --copy-mode hardlink
```

Use `--overwrite-output` only when intentionally replacing an existing merged directory.

When a retry job should replace failed or bad trials from earlier shards, put the original shard globs first and the retry job last, then use `--duplicate-strategy last`:

```bash
python skills/shared/benchmark-sharded-concurrency/scripts/merge_benchmark_jobs.py \
  --jobs 'jobs/<original-shard-prefix>_*' jobs/<retry-job-name> \
  --output-job merged_<job-prefix>_with_retry \
  --duplicate-strategy last \
  --copy-mode hardlink
```

Check the merge:

```bash
find jobs/merged_<job-prefix> -mindepth 2 -maxdepth 2 -name result.json | wc -l
wc -l jobs/merged_<job-prefix>/merge_manifest.jsonl
python -m json.tool jobs/merged_<job-prefix>/result.json >/dev/null
```

## Sharing Results

When summarizing a run, include:

- Total configured shape, for example `5 x 20 = 100 nominal concurrency`.
- Dataset size and shard ranges.
- Merged job directory and merge manifest path, if shards were merged.
- Wall-clock start/end time.
- Completed results, reward=1, reward=0, exceptions.
- Whether trajectories were disabled.
- Any systematic error class, such as build image failures or agent timeouts.
