#!/usr/bin/env python
import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from itertools import combinations
from pathlib import Path
from typing import Any

from dotenv import load_dotenv


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from evolution.score import compare_jobs, first_reward_value, summarize_job, trial_result_paths, write_report
from agents.skill_evaluator import calibration_event, evaluate_candidate
from agents.skill_writer import (
    build_failure_cluster,
    build_repo_cluster,
    write_candidate_skill,
    write_failure_mode_candidate,
    write_repo_candidate,
)
from scripts.run_skill_evo_verified import (
    archived_skill_versions,
    next_skill_version_id,
    read_active_version,
)


DEFAULT_SWEGYM_DATASET = ROOT / "data" / "harbor_swegym_500_uniform"
DEFAULT_EVO_ROOT = ROOT / "run_logs" / "swegym_skill_evo"
DEFAULT_SKILL_ARCHIVE_ROOT = ROOT / "skills" / "accepted"
DEFAULT_WANDB_PROJECT = "skills-evo-swegym"
DEFAULT_VERIFIER_BUFFER_SEC = 900
DEFAULT_REPO_UPDATE_BATCH_SIZE = 5
DEFAULT_REPO_MIN_POSITIVE_SUPPORT = 2
DEFAULT_FAILURE_MODE_UPDATE_EVERY_REPO_UPDATES = 4
DEFAULT_FAILURE_MODE_MIN_REPO_SUPPORT = 3
DEFAULT_TRANSIENT_RETRY_EXCEPTIONS = [
    "RemoteProtocolError",
    "ConnectException",
    "SandboxException",
]
SWE_STAGES = ("reproduce", "localize", "edit", "validate", "recover")


def utc_stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def log(message: str) -> None:
    try:
        print(f"[swegym-loop] {message}", flush=True)
    except BrokenPipeError:
        raise SystemExit(0)


def list_env(name: str) -> list[str] | None:
    value = os.getenv(name)
    if value is None or value.strip() == "":
        return None
    items = [item.strip() for item in value.split(",")]
    return [item for item in items if item]


def normalize_provider(value: str | None) -> str:
    provider = str(value or "openai").strip().lower()
    if provider in {"novita", "openai-compatible", "openai_compatible"}:
        return "openai"
    return provider


def flatten_list_values(values: list[str] | None) -> list[str] | None:
    if not values:
        return None
    items: list[str] = []
    for value in values:
        items.extend(item.strip() for item in value.split(","))
    return [item for item in items if item] or None


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True) + "\n")


def write_lines(path: Path, lines: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + ("\n" if lines else ""))


def read_lines(path: Path) -> list[str]:
    return [
        line.strip()
        for line in path.read_text(errors="replace").splitlines()
        if line.strip() and not line.strip().startswith("#")
    ]


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in rows)
    )


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(errors="replace"))


def job_result_is_complete(job_dir: Path) -> bool:
    result_path = job_dir / "result.json"
    if not result_path.exists():
        return False
    try:
        result = read_json(result_path)
    except json.JSONDecodeError:
        return False
    return result.get("finished_at") is not None and "trial_results" not in result


def version_id_with_offset(base_version_id: str, offset: int) -> str:
    match = re.fullmatch(r"v(\d+)", base_version_id)
    if not match:
        return f"{base_version_id}_{offset:04d}"
    return f"v{int(match.group(1)) + offset:04d}"


def memory_base_version(memory_path: Path, fallback: str) -> str:
    if not memory_path.exists():
        return fallback
    memory = read_json(memory_path)
    versions = memory.get("versions")
    if not isinstance(versions, dict):
        return fallback
    numbered: list[tuple[int, str]] = []
    for version_id in versions:
        match = re.fullmatch(r"v(\d+)", str(version_id))
        if match:
            numbered.append((int(match.group(1)), str(version_id)))
    if not numbered:
        return str(memory.get("active_version") or fallback)
    return min(numbered)[1]


def safe_slug(value: str, *, fallback: str = "unknown", limit: int = 120) -> str:
    slug = re.sub(r"[^A-Za-z0-9_.-]+", "_", value).strip("_")
    return (slug or fallback)[:limit]


def mask_command(command: list[str]) -> str:
    masked: list[str] = []
    skip_next = False
    secret_flags = {"--provider-api-key", "--wandb-api-key"}
    for token in command:
        if skip_next:
            masked.append("***")
            skip_next = False
            continue
        masked.append(token)
        if token in secret_flags:
            skip_next = True
    return " ".join(masked)


def run_command(
    command: list[str],
    *,
    env: dict[str, str],
    dry_run: bool,
    cwd: Path = ROOT,
    timeout_sec: float | None = None,
) -> None:
    timeout_note = f" timeout_sec={timeout_sec}" if timeout_sec else ""
    log(mask_command(command) + timeout_note)
    if dry_run:
        return
    try:
        subprocess.run(command, cwd=cwd, env=env, check=True, timeout=timeout_sec)
    except subprocess.TimeoutExpired as exc:
        raise SystemExit(
            f"Command timed out after {timeout_sec} seconds: {mask_command(command)}"
        ) from exc
    except subprocess.CalledProcessError as exc:
        raise SystemExit(
            f"Command failed with exit status {exc.returncode}: {mask_command(command)}"
        ) from exc


def _env_present(env: dict[str, str], name: str) -> bool:
    return bool(str(env.get(name) or "").strip())


def missing_runtime_env(args: argparse.Namespace, env: dict[str, str], *, benchmark_runs: bool) -> list[str]:
    missing: list[str] = []
    if benchmark_runs and not _env_present(env, "E2B_API_KEY"):
        missing.append("E2B_API_KEY")
    if args.provider == "openai":
        provider_names = (
            "OPENAI_COMPAT_API_KEY",
            "OPENAI_COMPAT_BASE_URL",
            "OPENAI_COMPAT_MODEL",
        )
    else:
        provider_names = ("TINKER_API_KEY", "TINKER_BASE_URL", "TINKER_MODEL")
    if benchmark_runs:
        missing.extend(name for name in provider_names if not _env_present(env, name))
    if (
        not args.skip_training_update
        and args.summarize_with_backbone
        and args.baseline_train_job_dir is not None
    ):
        missing.extend(name for name in provider_names if not _env_present(env, name))
    return list(dict.fromkeys(missing))


def parse_simple_toml_strings(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    section = ""
    for raw_line in path.read_text(errors="replace").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("[") and line.endswith("]"):
            section = line.strip("[]")
            continue
        if "=" not in line:
            continue
        key, raw_value = line.split("=", 1)
        key = key.strip()
        value = raw_value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        values[f"{section}.{key}" if section else key] = value
    return values


def repo_slug_from_repo(repo: str) -> str:
    return repo.replace("/", "__")


def repo_slug_from_instance(instance_id: str) -> str:
    if "__" not in instance_id:
        return ""
    org, rest = instance_id.split("__", 1)
    repo = rest.rsplit("-", 1)[0]
    return f"{org}__{repo}" if repo else org


@dataclass(frozen=True)
class SwegymTask:
    task_dir: Path
    instance_id: str
    task_name: str
    repo: str
    repo_slug: str
    base_commit: str
    source_split: str
    instruction_chars: int
    fail_to_pass: int
    pass_to_pass: int


def load_swegym_tasks(dataset: Path) -> list[SwegymTask]:
    if not dataset.exists():
        raise FileNotFoundError(f"Missing SWEGym dataset path: {dataset}")

    tasks: list[SwegymTask] = []
    for task_toml in sorted(dataset.glob("*/task.toml")):
        task_dir = task_toml.parent
        config_path = task_dir / "tests" / "config.json"
        instruction_path = task_dir / "instruction.md"
        if not config_path.exists() or not instruction_path.exists():
            continue
        config = read_json(config_path)
        toml_values = parse_simple_toml_strings(task_toml)
        instance_id = str(config.get("instance_id") or toml_values.get("metadata.instance_id") or task_dir.name)
        repo = str(config.get("repo") or toml_values.get("metadata.repo") or "")
        repo_slug = repo_slug_from_repo(repo) if repo else repo_slug_from_instance(instance_id)
        tasks.append(
            SwegymTask(
                task_dir=task_dir,
                instance_id=instance_id,
                task_name=str(toml_values.get("task.name") or f"swegym/{instance_id}"),
                repo=repo,
                repo_slug=repo_slug,
                base_commit=str(config.get("base_commit") or toml_values.get("metadata.base_commit") or ""),
                source_split=str(config.get("source_split") or toml_values.get("metadata.source_split") or ""),
                instruction_chars=instruction_path.stat().st_size,
                fail_to_pass=len(config.get("fail_to_pass") or []),
                pass_to_pass=len(config.get("pass_to_pass") or []),
            )
        )
    return tasks


def choose_validation_repos(tasks: list[SwegymTask], ratio: float) -> tuple[set[str], dict[str, int]]:
    repo_counts: dict[str, int] = {}
    for task in tasks:
        repo_counts[task.repo_slug] = repo_counts.get(task.repo_slug, 0) + 1
    repos = sorted(repo_counts)
    target = max(1, round(len(tasks) * ratio))

    best_subset: tuple[str, ...] = ()
    best_score: tuple[int, int, int] | None = None
    for size in range(1, len(repos) + 1):
        for subset in combinations(repos, size):
            count = sum(repo_counts[repo] for repo in subset)
            score = (abs(count - target), size, -count)
            if best_score is None or score < best_score:
                best_score = score
                best_subset = subset
    return set(best_subset), repo_counts


def write_split(
    *,
    tasks: list[SwegymTask],
    validation_repos: set[str],
    split_dir: Path,
    smoke_train_tasks: int | None,
    smoke_validation_tasks: int | None,
) -> dict[str, Any]:
    full_train = sorted(
        (task for task in tasks if task.repo_slug not in validation_repos),
        key=lambda task: (task.repo_slug, task.instance_id),
    )
    full_validation = sorted(
        (task for task in tasks if task.repo_slug in validation_repos),
        key=lambda task: (task.repo_slug, task.instance_id),
    )
    train = select_smoke_tasks(full_train, smoke_train_tasks)
    validation = select_smoke_tasks(full_validation, smoke_validation_tasks)

    # Harbor local datasets identify tasks by directory/instance id, even when
    # task.toml contains a display name such as `swegym/<instance_id>`.
    train_names = [task.instance_id for task in train]
    validation_names = [task.instance_id for task in validation]
    write_lines(split_dir / "train_tasks.txt", train_names)
    write_lines(split_dir / "validation_tasks.txt", validation_names)
    write_lines(split_dir / "validation_repos.txt", sorted(validation_repos))

    rows = [
        {
            "instance_id": task.instance_id,
            "harbor_task_name": task.instance_id,
            "task_name": task.task_name,
            "repo": task.repo,
            "repo_slug": task.repo_slug,
            "split": "validation" if task.repo_slug in validation_repos else "train",
            "base_commit": task.base_commit,
            "instruction_chars": task.instruction_chars,
            "fail_to_pass": task.fail_to_pass,
            "pass_to_pass": task.pass_to_pass,
            "task_dir": str(task.task_dir),
        }
        for task in tasks
    ]
    write_json(split_dir / "task_manifest.json", rows)
    return {
        "n_total": len(tasks),
        "n_train_full": len(full_train),
        "n_validation_full": len(full_validation),
        "validation_ratio_full": len(full_validation) / len(tasks) if tasks else 0,
        "n_train": len(train_names),
        "n_validation": len(validation_names),
        "validation_ratio_actual": len(validation_names) / len(tasks) if tasks else 0,
        "validation_repos": sorted(validation_repos),
        "train_tasks_file": str(split_dir / "train_tasks.txt"),
        "validation_tasks_file": str(split_dir / "validation_tasks.txt"),
        "manifest": str(split_dir / "task_manifest.json"),
    }


def select_smoke_tasks(tasks: list[SwegymTask], limit: int | None) -> list[SwegymTask]:
    if limit is None:
        return list(tasks)
    if limit <= 0:
        return []

    buckets: dict[str, list[SwegymTask]] = {}
    repo_order: list[str] = []
    for task in tasks:
        if task.repo_slug not in buckets:
            buckets[task.repo_slug] = []
            repo_order.append(task.repo_slug)
        buckets[task.repo_slug].append(task)

    selected: list[SwegymTask] = []
    while len(selected) < limit:
        progressed = False
        for repo_slug in repo_order:
            bucket = buckets[repo_slug]
            if not bucket:
                continue
            selected.append(bucket.pop(0))
            progressed = True
            if len(selected) >= limit:
                break
        if not progressed:
            break
    return selected


def write_repo_training_batches(
    *,
    train_tasks_file: Path,
    tasks: list[SwegymTask],
    batch_size: int,
    out_dir: Path,
) -> list[dict[str, Any]]:
    task_by_id = {task.instance_id: task for task in tasks}
    train_task_ids = read_lines(train_tasks_file)
    batches: list[dict[str, Any]] = []
    by_repo: dict[str, list[str]] = defaultdict(list)
    for task_id in train_task_ids:
        task = task_by_id.get(task_id)
        repo_slug = task.repo_slug if task is not None else repo_slug_from_instance(task_id)
        by_repo[repo_slug].append(task_id)

    out_dir.mkdir(parents=True, exist_ok=True)
    update_size = max(1, batch_size)
    for repo_slug in sorted(by_repo):
        repo_task_ids = by_repo[repo_slug]
        for start in range(0, len(repo_task_ids), update_size):
            batch_task_ids = repo_task_ids[start : start + update_size]
            batch_index = len(batches) + 1
            task_file = out_dir / f"train_batch_{batch_index:04d}_{safe_slug(repo_slug)}.txt"
            write_lines(task_file, batch_task_ids)
            batches.append(
                {
                    "batch_index": batch_index,
                    "repo_slug": repo_slug,
                    "task_count": len(batch_task_ids),
                    "task_names_file": str(task_file),
                    "tasks": batch_task_ids,
                }
            )
    write_json(out_dir / "training_batches.json", {"batches": batches})
    return batches


def ensure_stage_skill_seed(skill_root: Path, version_id: str, run_name: str) -> Path:
    version_root = skill_root / version_id
    for stage in SWE_STAGES:
        skill_dir = version_root / "_general" / "swe" / stage / f"seed-{stage}"
        skill_dir.mkdir(parents=True, exist_ok=True)
        skill_md = skill_dir / "SKILL.md"
        if skill_md.exists():
            continue
        skill_md.write_text(
            "\n".join(
                [
                    "---",
                    f"name: seed-{stage}",
                    f"description: \"Frozen seed {stage} skill for SWE-like debugging loops generated by {run_name}.\"",
                    "active: true",
                    "quality_score: 0.62",
                    "quality_tier: seed",
                    "risk_flags: []",
                    "use_policy: evidence-gated",
                    "---",
                    "",
                    f"# Seed {stage} Skill",
                    "",
                    "Use this only as a weak process hint after inspecting repository evidence.",
                    "Do not copy task-specific paths or patches from training tasks.",
                    "",
                    "## Actions",
                    "",
                    "- Collect concrete repository evidence before editing.",
                    "- Prefer a minimal source-code change tied to the observed failure.",
                    "- Run the narrowest relevant check before broad validation.",
                    "- Stop using this skill if the current task evidence does not match it.",
                    "",
                ]
            )
        )
    return version_root


def materialize_general_skill_seeds(
    *,
    skill_root: Path,
    version_id: str,
    run_name: str,
) -> Path:
    return ensure_stage_skill_seed(skill_root, version_id, run_name)


def render_harness_policy(args: argparse.Namespace) -> str:
    max_task_evidence_edits = getattr(
        args,
        "max_task_evidence_edits_per_task",
        getattr(args, "max_task_skill_edits_per_task", 3),
    )
    return f"""# Hierarchical Skill Evolution Harness Policy

Status: fixed, human-defined, and not learned.

This harness is the boundary condition for skill evolution. It maps verifier transitions and runtime diagnostics into update labels, step sizes, promotion gates, and anti-inflation budgets. The Skill Writer policy and Skill Evaluator policy must not redefine these labels or update clocks.

## Upward Flow

```text
rollout trace / failure trace
  -> task evidence records, conditioned on one task trace
  -> repo skill candidates, conditioned on multiple task evidence records from the same repo
  -> repo skill state, filtered by the evaluator policy and repo-level evidence
  -> failure-mode skill candidates, conditioned on repeated repo-level summaries across repos
  -> failure-mode skill state, filtered by the evaluator policy and cross-repo evidence
  -> writer/evaluator policy updates, conditioned only on aggregated accepted/rejected histories
```

Repo skills must not be generated from a single raw trace. Failure-mode skills must not be generated from a single task or a single repo. Writer and evaluator policies must not update from an individual case.

## Fixed Case Labels

| Case | Definition | Meaning | Primary Use |
| --- | --- | --- | --- |
| Strong Positive | Previous attempt failed and current attempt passed (`0 -> 1`) | The current skill/update is likely causally helpful. | Keep, compress, and pass upward as evidence. |
| Weak Positive | Previous attempt passed and current attempt still passed (`1 -> 1`), especially if faster, shorter, or more stable | The update did not break success and may improve efficiency or stability. | Slightly increase confidence; use as a stable anchor. |
| Strong Negative | Previous attempt passed and current attempt failed (`1 -> 0`) | The current skill/update is likely harmful. | Disable, roll back, rewrite, and penalize false accept. |
| Weak Negative | Previous attempt failed and current attempt still failed (`0 -> 0`) | No visible utility yet, but not necessarily harmful. | Small rewrite or keep inactive. |
| Diagnostic | Missing reward, timeout, setup error, verifier error, upload error, or sandbox/runtime blocker | Runtime risk should not pollute semantic skills. | Update recover/validate safeguards and runtime risk judgment only. |

Cold-start training has no paired previous attempt. In that case, solved traces map to Weak Positive and unsolved traces map to Weak Negative unless a runtime diagnostic is present.

## Update Step Sizes

| Case | Task evidence update | Repo skill update | Failure-mode update | Writer policy update | Evaluator policy update |
| --- | --- | --- | --- | --- | --- |
| Strong Positive | Record or refresh at most {max_task_evidence_edits} task evidence items. | Add support only; promote after accumulated batch evidence passes. | Add pattern evidence only after repo-level confirmation. | After {args.policy_update_every_failure_mode_triggers} failure-mode triggers, add or rewrite at most 1 positive writing rule. | After {args.policy_update_every_failure_mode_triggers} failure-mode triggers, add or rewrite at most 1 positive evaluation rule. |
| Weak Positive | Compress or raise confidence only. | Add stability evidence only. | Usually no semantic update. | Usually no update. | Use as a false-reject calibration anchor. |
| Strong Negative | Mark regression or diagnostic evidence; do not promote raw task evidence. | Reduce support or mark review if a repo skill fired. | Add negative evidence; do not create a new global rule. | After {args.policy_update_every_failure_mode_triggers} failure-mode triggers, add at most 1 avoid rule. | After {args.policy_update_every_failure_mode_triggers} failure-mode triggers, prioritize false-accept penalties. |
| Weak Negative | Record failed-attempt evidence only. | Do not promote; record neutral/negative evidence. | Do not promote. | No update unless repeated failures aggregate. | May update abstain/request-rewrite conditions. |
| Diagnostic | Add at most 1 recover/validate guard. | No semantic repo update. | Update runtime/failure taxonomy only if repeated across repos. | No semantic writing update. | Update runtime risk judgment. |

## Update Clocks

- Task evidence updates after each task attempt.
- Repo skills update every `repo_update_batch_size` task-level updates within the same repo.
- Failure-mode skills update every `failure_mode_update_every_repo_updates` repo-level updates.
- Writer and evaluator policies update every `policy_update_every_failure_mode_triggers` failure-mode triggers, then freeze for validation/test.
- Test-time verifier labels are unavailable; this implementation exports frozen failure-mode transfer artifacts and does not run test-time skill evolution.

Default hyperparameters for this run:

```json
{{
  "repo_update_batch_size": {args.repo_update_batch_size},
  "failure_mode_update_every_repo_updates": {args.failure_mode_update_every_repo_updates},
  "failure_mode_min_repo_support": {args.failure_mode_min_repo_support},
  "policy_update_every_failure_mode_triggers": {args.policy_update_every_failure_mode_triggers},
  "max_repo_skill_updates_per_batch": {args.max_repo_skill_updates_per_batch},
  "max_failure_mode_updates_per_trigger": {args.max_failure_mode_updates_per_trigger},
  "policy_max_bullet_updates": {args.policy_max_bullet_updates}
}}
```

## Anti-Inflation Budgets

- Task layer: task traces are evidence records, not reusable skills; keep only public signals needed for repo/failure-mode aggregation.
- Repo layer: each repo batch emits at most 1 repo candidate; it writes a new repo skill only when the repo promotion budget is positive.
- Failure-mode layer: at most {args.max_failure_mode_updates_per_trigger} promoted skills per failure-mode trigger; accepted candidates beyond that cap remain staged evidence.
- Policy layer: at most {args.policy_max_bullet_updates} bullet edits per policy update window; prefer rewriting existing rules over appending.
- No task id, exact patch, private path, hidden verifier detail, or benchmark-specific answer may enter repo/failure-mode skills or policy documents.
"""


def render_empty_policy_doc(*, title: str, run_name: str, version_id: str) -> str:
    return f"""# {title}

Status: empty learned policy.

Run: `{run_name}`
Skill version: `{version_id}`

No learned rules have been accepted yet. This document may only be updated after aggregated failure-mode trigger windows from accepted/rejected skill histories. It must not memorize task ids, exact patches, private paths, or hidden verifier details.
"""


def write_policy_documents(
    *,
    args: argparse.Namespace,
    train_dir: Path,
    run_name: str,
    version_id: str,
) -> dict[str, Path]:
    train_dir.mkdir(parents=True, exist_ok=True)
    paths = {
        "harness_policy": train_dir / "harness_policy.md",
        "generator_policy": train_dir / "generator_policy.md",
        "evaluator_policy": train_dir / "evaluator_policy.md",
    }
    paths["harness_policy"].write_text(render_harness_policy(args).rstrip() + "\n")
    paths["generator_policy"].write_text(
        render_empty_policy_doc(
            title="Skill Writer Policy",
            run_name=run_name,
            version_id=version_id,
        ).rstrip()
        + "\n"
    )
    paths["evaluator_policy"].write_text(
        render_empty_policy_doc(
            title="Skill Evaluator Policy",
            run_name=run_name,
            version_id=version_id,
        ).rstrip()
        + "\n"
    )
    return paths


def failure_mode_policy_rules(
    *,
    task_events: list[dict[str, Any]],
    promotion_decisions: list[dict[str, Any]],
    args: argparse.Namespace,
) -> dict[str, list[str]]:
    if not task_events:
        return {"writer": [], "evaluator": []}

    case_counts = Counter(str(event.get("case_label") or "unknown") for event in task_events)
    repo_promotions = sum(
        1
        for decision in promotion_decisions
        if decision.get("level") == "repo" and decision.get("decision") == "promote"
    )
    failure_promotions = sum(
        1
        for decision in promotion_decisions
        if decision.get("level") == "failure_mode" and decision.get("decision") == "promote"
    )

    writer_rules = [
        "Extract task evidence only from trace-visible owner paths, public commands, public outputs, and explicit stop conditions; never promote raw task evidence directly to downstream skills.",
        "Treat cold-start solved traces as weak-positive local evidence: keep them task-scoped and evidence-gated until same-repo batching supplies repeated support.",
        f"Update generator/evaluator policy only after {args.policy_update_every_failure_mode_triggers} failure-mode triggers, using the current window of accepted and rejected histories rather than a single trace.",
    ]
    if case_counts.get("weak_negative", 0) or case_counts.get("diagnostic", 0):
        writer_rules.append(
            "For unresolved or diagnostic traces, prefer narrow recover/validate guards over semantic edit instructions, and keep the skill inactive unless the evaluator finds concrete reusable evidence."
        )
    if repo_promotions or failure_promotions:
        writer_rules.append(
            "When drafting higher-level skills, summarize only repeated paths, validation commands, or failure signatures that survived the lower-level gate."
        )

    evaluator_rules = [
        "Treat task-level outputs as evidence records only; never accept them as reusable downstream skills.",
        "Downgrade unresolved or exception-sourced skills to memory-only unless they are narrow recover/validate guards with a concrete diagnostic trigger.",
        f"Require `{args.repo_update_batch_size}` same-repo task-level events plus repeated path or validation evidence before repo promotion; require `{args.failure_mode_min_repo_support}` distinct repos before failure-mode promotion.",
        f"Calibrate policy after every `{args.policy_update_every_failure_mode_triggers}` failure-mode triggers, not per task or per repo update.",
    ]
    if case_counts.get("strong_negative", 0):
        evaluator_rules.insert(
            0,
            "Penalize false accepts first: if a previously solved case regresses, disable or request rewrite before increasing confidence anywhere upstream.",
        )

    return {
        "writer": writer_rules[: args.policy_max_bullet_updates],
        "evaluator": evaluator_rules[: args.policy_max_bullet_updates],
    }


def append_failure_mode_policy_updates(
    *,
    policy_paths: dict[str, Path],
    task_events: list[dict[str, Any]],
    promotion_decisions: list[dict[str, Any]],
    args: argparse.Namespace,
    update_index: int,
    failure_mode_trigger_count: int,
    tail_window: bool = False,
) -> dict[str, Any]:
    rules = failure_mode_policy_rules(
        task_events=task_events,
        promotion_decisions=promotion_decisions,
        args=args,
    )
    update_state = {
        "writer_policy_updates": 0,
        "evaluator_policy_updates": 0,
        "writer_rules": rules["writer"],
        "evaluator_rules": rules["evaluator"],
        "update_index": update_index,
        "failure_mode_trigger_count": failure_mode_trigger_count,
        "tail_window": tail_window,
    }
    if rules["writer"]:
        with policy_paths["generator_policy"].open("a") as handle:
            handle.write(f"\n## Failure-Mode Calibration {update_index}")
            handle.write(" (tail)\n\n" if tail_window else "\n\n")
            handle.write("Learned from aggregated histories under the fixed harness policy window:\n\n")
            for rule in rules["writer"]:
                handle.write(f"- {rule}\n")
        update_state["writer_policy_updates"] = 1
    if rules["evaluator"]:
        with policy_paths["evaluator_policy"].open("a") as handle:
            handle.write(f"\n## Failure-Mode Calibration {update_index}")
            handle.write(" (tail)\n\n" if tail_window else "\n\n")
            handle.write("Learned from aggregated histories under the fixed harness policy window:\n\n")
            for rule in rules["evaluator"]:
                handle.write(f"- {rule}\n")
        update_state["evaluator_policy_updates"] = 1
    return update_state


def memory_version_entries(memory_path: Path, version_id: str | None = None) -> tuple[str, list[dict[str, Any]]]:
    if not memory_path.exists():
        return version_id or "", []
    memory = read_json(memory_path)
    active_version = version_id or str(memory.get("active_version") or "")
    versions = memory.get("versions") or {}
    version = versions.get(active_version) if isinstance(versions, dict) else None
    if not isinstance(version, dict):
        return active_version, []
    entries: list[dict[str, Any]] = []
    seen_entry_ids: set[str] = set()
    chain: list[str] = []
    seen_versions: set[str] = set()
    current_version = active_version
    while current_version and current_version not in seen_versions:
        seen_versions.add(current_version)
        current = versions.get(current_version) if isinstance(versions, dict) else None
        if not isinstance(current, dict):
            break
        chain.append(current_version)
        parent = current.get("parent_version")
        current_version = str(parent) if parent else ""
    for chain_version in reversed(chain):
        current = versions.get(chain_version) if isinstance(versions, dict) else None
        if not isinstance(current, dict):
            continue
        for entry in current.get("entries") or []:
            if not isinstance(entry, dict):
                continue
            entry_id = str(entry.get("entry_id") or entry.get("task_name") or id(entry))
            if entry_id in seen_entry_ids:
                continue
            seen_entry_ids.add(entry_id)
            entries.append(entry)
    return active_version, entries


def entry_task_slug(entry: dict[str, Any]) -> str:
    task_name = str(entry.get("task_name") or entry.get("task_skill_id") or "")
    leaf = task_name.split("/")[-1]
    if "__" in leaf:
        return leaf
    task_skill_id = str(entry.get("task_skill_id") or "")
    return task_skill_id.split("/")[-1]


def entry_repo_scope(entry: dict[str, Any]) -> str:
    task_slug = entry_task_slug(entry)
    repo_slug = repo_slug_from_instance(task_slug)
    if repo_slug:
        return repo_slug
    return safe_slug(str(entry.get("repo") or "unknown"), fallback="unknown")


def entry_reward(entry: dict[str, Any]) -> float | None:
    try:
        value = entry.get("reward")
        return None if value is None else float(value)
    except (TypeError, ValueError):
        return None


def entry_previous_reward(entry: dict[str, Any]) -> float | None:
    for key in ("previous_reward", "baseline_reward", "previous_verifier_reward"):
        if key not in entry:
            continue
        try:
            value = entry.get(key)
            return None if value is None else float(value)
        except (TypeError, ValueError):
            return None
    return None


def case_label_for_entry(entry: dict[str, Any]) -> str:
    current = entry_reward(entry)
    previous = entry_previous_reward(entry)
    if entry.get("exception") or current is None:
        return "diagnostic"
    if previous is None:
        return "weak_positive" if current >= 1.0 else "weak_negative"
    if previous < 1.0 <= current:
        return "strong_positive"
    if previous >= 1.0 and current >= 1.0:
        return "weak_positive"
    if previous >= 1.0 > current:
        return "strong_negative"
    return "weak_negative"


def task_update_budget(case_label: str, args: argparse.Namespace) -> dict[str, Any]:
    if case_label == "strong_positive":
        return {"action": "record_or_refresh_task_evidence", "max_task_evidence_edits": args.max_task_evidence_edits_per_task}
    if case_label == "weak_positive":
        return {"action": "keep_as_weak_positive_evidence", "max_task_evidence_edits": 0}
    if case_label == "strong_negative":
        return {"action": "mark_regression_or_diagnostic_evidence", "max_task_evidence_edits": 2}
    if case_label == "weak_negative":
        return {"action": "record_failed_attempt_evidence", "max_task_evidence_edits": 1}
    return {"action": "record_recover_or_validate_evidence", "max_task_evidence_edits": 1}


def iter_entry_stage_skills(entry: dict[str, Any]) -> list[dict[str, Any]]:
    stage_items = entry.get("task_stage_skills")
    if not isinstance(stage_items, list):
        return []
    skills: list[dict[str, Any]] = []
    for stage_item in stage_items:
        if not isinstance(stage_item, dict):
            continue
        stage = str(stage_item.get("stage") or "recover")
        for skill in stage_item.get("skills") or []:
            if isinstance(skill, dict):
                skill_with_stage = dict(skill)
                skill_with_stage.setdefault("stage", stage)
                skills.append(skill_with_stage)
    return skills


def active_stage_skill_count(entry: dict[str, Any]) -> int:
    count = 0
    for skill in iter_entry_stage_skills(entry):
        quality = skill.get("quality_metadata") if isinstance(skill.get("quality_metadata"), dict) else {}
        active = skill.get("active")
        if active is None:
            active = quality.get("active")
        use_policy = str(skill.get("use_policy") or quality.get("use_policy") or "").lower()
        if active is True and use_policy not in {"memory-only", "disabled", "inactive"}:
            count += 1
    return count


def accepted_task_entry(entry: dict[str, Any]) -> bool:
    reward = entry_reward(entry)
    return (
        reward is not None
        and reward >= 1.0
        and not entry.get("exception")
        and bool(entry.get("edited_paths") or entry.get("test_commands") or entry.get("touched_paths"))
    )


def repeated_values(
    entries: list[dict[str, Any]],
    key: str,
    *,
    limit: int = 6,
    min_count: int = 2,
) -> list[str]:
    counter: Counter[str] = Counter()
    for entry in entries:
        values = entry.get(key) or []
        if isinstance(values, str):
            values = [values]
        if not isinstance(values, list):
            continue
        for value in values:
            text = str(value).strip()
            if text:
                counter[text] += 1
    return [value for value, count in counter.most_common(limit) if count >= min_count]


def entry_failure_signature(entry: dict[str, Any]) -> str:
    exception = str(entry.get("exception") or "").strip()
    if exception:
        return f"runtime-{safe_slug(exception.lower(), fallback='diagnostic', limit=48)}"
    reward = entry_reward(entry)
    if reward is None:
        return "runtime-missing-reward"
    if reward >= 1.0:
        return "resolved"
    if not entry.get("edited_paths"):
        return "no-diff-recovery"
    if not entry.get("test_commands"):
        return "weak-validation"
    return "localization-drift"


def repo_skill_decision(
    *,
    repo: str,
    batch: list[dict[str, Any]],
    update_index: int,
    args: argparse.Namespace,
    evaluator_policy: dict[str, Any] | None = None,
) -> dict[str, Any]:
    case_counts = Counter(case_label_for_entry(entry) for entry in batch)
    accepted_entries = [entry for entry in batch if accepted_task_entry(entry)]
    failure_counts = Counter(
        entry_failure_signature(entry)
        for entry in batch
        if case_label_for_entry(entry) != "diagnostic"
        and entry_failure_signature(entry) != "resolved"
    )
    diagnostic_counts = Counter(
        entry_failure_signature(entry)
        for entry in batch
        if case_label_for_entry(entry) == "diagnostic"
    )
    cluster = build_repo_cluster(
        repo=repo,
        entries=batch,
        update_index=update_index,
        case_counts=dict(case_counts),
        failure_signature_counts=dict(failure_counts),
        diagnostic_signature_counts=dict(diagnostic_counts),
        positive_entries=accepted_entries,
    )
    candidate = write_repo_candidate(cluster)
    evaluator_decision = evaluate_candidate(
        candidate=candidate,
        evidence=cluster,
        verifier_context={
            "case_counts": dict(case_counts),
            "positive_support": len(accepted_entries),
            "source": "training_verifier_rewards",
        },
        evaluator_policy=evaluator_policy,
        min_repo_positive_support=args.repo_min_positive_support,
        min_failure_repo_support=args.failure_mode_min_repo_support,
    )
    accepted = evaluator_decision.get("decision") == "accept"
    return {
        "created_at": utc_now(),
        "level": "repo",
        "repo": repo,
        "update_index": update_index,
        "batch_size": len(batch),
        "positive_task_evidence_count": len(accepted_entries),
        "case_counts": dict(case_counts),
        "failure_signature_counts": dict(failure_counts),
        "diagnostic_signature_counts": dict(diagnostic_counts),
        "repeated_paths": cluster.get("repeated_paths") or [],
        "repeated_tests": cluster.get("repeated_tests") or [],
        "repo_cluster": cluster,
        "candidate": candidate,
        "evaluator_decision": evaluator_decision,
        "decision": "promote" if accepted else "stage",
        "candidate_decision": evaluator_decision.get("decision"),
        "reason": evaluator_decision.get("reason") or ("accepted by evaluator" if accepted else "rejected by evaluator"),
    }

def _skill_record_from_decision(decision: dict[str, Any]) -> dict[str, Any]:
    return {
        "created_at": decision.get("created_at"),
        "level": decision.get("level"),
        "decision": decision.get("decision"),
        "repo": decision.get("repo"),
        "failure_signature": decision.get("failure_signature"),
        "support_count": decision.get("positive_task_evidence_count")
        or decision.get("repo_support_count")
        or decision.get("event_support_count"),
        "case_counts": decision.get("case_counts") or {},
        "skill_path": decision.get("skill_path"),
        "transfer_scope": (
            "downstream_transferable"
            if decision.get("level") == "failure_mode"
            else "training_scaffold_only"
        ),
        "reason": decision.get("reason"),
    }


def update_memory_hierarchical_state(
    *,
    memory_path: Path,
    version_id: str,
    task_events: list[dict[str, Any]],
    promotion_decisions: list[dict[str, Any]],
    policy_state: dict[str, Any],
) -> None:
    if not memory_path.exists():
        return
    memory = read_json(memory_path)
    versions = memory.get("versions")
    if not isinstance(versions, dict):
        return
    version = versions.get(version_id)
    if not isinstance(version, dict):
        return

    skill_state = version.setdefault(
        "skill_state",
        {
            "schema_version": 1,
            "task_evidence": {},
            "legacy_task_stage_skills": [],
            "repo_skills": [],
            "failure_mode_skills": [],
            "transfer_contract": {
                "task_evidence": "training_scaffold_only",
                "legacy_task_stage_skills": "compatibility_only_not_a_method_artifact",
                "repo_skills": "training_scaffold_only",
                "failure_mode_skills": "downstream_transferable",
            },
        },
    )
    if isinstance(skill_state, dict):
        skill_state["repo_skills"] = [
            _skill_record_from_decision(decision)
            for decision in promotion_decisions
            if decision.get("level") == "repo" and decision.get("decision") == "promote"
        ]
        skill_state["failure_mode_skills"] = [
            _skill_record_from_decision(decision)
            for decision in promotion_decisions
            if decision.get("level") == "failure_mode" and decision.get("decision") == "promote"
        ]
        skill_state.setdefault(
            "transfer_contract",
            {
                "task_evidence": "training_scaffold_only",
                "legacy_task_stage_skills": "compatibility_only_not_a_method_artifact",
                "repo_skills": "training_scaffold_only",
                "failure_mode_skills": "downstream_transferable",
            },
        )
        skill_state["task_evidence"] = {
            "task_events": len(task_events),
            "case_counts": dict(Counter(str(event.get("case_label") or "unknown") for event in task_events)),
        }
        skill_state["candidate_counts"] = {
            "repo_candidates": sum(
                1
                for decision in promotion_decisions
                if decision.get("level") == "repo" and isinstance(decision.get("candidate"), dict)
            ),
            "failure_mode_candidates": sum(
                1
                for decision in promotion_decisions
                if decision.get("level") == "failure_mode" and isinstance(decision.get("candidate"), dict)
            ),
        }

    decision_log = version.setdefault(
        "decision_log",
        {
            "schema_version": 1,
            "task_decisions": [],
            "repo_decisions": [],
            "failure_mode_decisions": [],
            "validation_gate_decisions": [],
        },
    )
    if isinstance(decision_log, dict):
        decision_log["task_decisions"] = task_events
        decision_log["repo_decisions"] = [
            decision
            for decision in promotion_decisions
            if decision.get("level") == "repo"
        ]
        decision_log["failure_mode_decisions"] = [
            decision
            for decision in promotion_decisions
            if decision.get("level") == "failure_mode"
        ]
        decision_log["repo_candidates"] = [
            decision.get("candidate")
            for decision in promotion_decisions
            if decision.get("level") == "repo" and isinstance(decision.get("candidate"), dict)
        ]
        decision_log["failure_mode_candidates"] = [
            decision.get("candidate")
            for decision in promotion_decisions
            if decision.get("level") == "failure_mode" and isinstance(decision.get("candidate"), dict)
        ]
        decision_log["evaluator_decisions"] = [
            decision.get("evaluator_decision")
            for decision in promotion_decisions
            if isinstance(decision.get("evaluator_decision"), dict)
        ]

    version["policy_state"] = {
        "schema_version": 1,
        "writer_policy": {
            "rules": (policy_state.get("policy_calibration") or {}).get("writer_rules") or [],
            "update_count": (policy_state.get("clocks") or {}).get("writer_policy_updates", 0),
            "path": (policy_state.get("policy_paths") or {}).get("generator_policy"),
        },
        "evaluator_policy": {
            "rules": (policy_state.get("policy_calibration") or {}).get("evaluator_rules") or [],
            "update_count": (policy_state.get("clocks") or {}).get("evaluator_policy_updates", 0),
            "path": (policy_state.get("policy_paths") or {}).get("evaluator_policy"),
        },
        "harness_policy": {
            "path": (policy_state.get("policy_paths") or {}).get("harness_policy"),
            "hyperparameters": policy_state.get("hyperparameters") or {},
        },
    }
    write_json(memory_path, memory)


def write_hierarchical_training_artifacts(
    *,
    args: argparse.Namespace,
    train_dir: Path,
    memory_path: Path,
    skill_archive_root: Path,
    version_id: str,
    run_name: str,
) -> dict[str, Any]:
    if not hasattr(args, "max_task_evidence_edits_per_task"):
        args.max_task_evidence_edits_per_task = getattr(args, "max_task_skill_edits_per_task", 3)
    policy_paths = write_policy_documents(
        args=args,
        train_dir=train_dir,
        run_name=run_name,
        version_id=version_id,
    )
    active_version, entries = memory_version_entries(memory_path, version_id)
    active_version = active_version or version_id
    version_root = skill_archive_root / active_version
    version_root.mkdir(parents=True, exist_ok=True)

    task_events: list[dict[str, Any]] = []
    task_event_by_entry_id: dict[str, dict[str, Any]] = {}
    entries_by_repo: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for index, entry in enumerate(entries, start=1):
        repo = entry_repo_scope(entry)
        label = case_label_for_entry(entry)
        entries_by_repo[repo].append(entry)
        entry_id = str(entry.get("entry_id") or index)
        event = {
            "created_at": utc_now(),
            "level": "task",
            "event_index": index,
            "repo": repo,
            "task_slug": entry_task_slug(entry),
            "entry_id": entry_id,
            "source_job": entry.get("source_job"),
            "current_reward": entry_reward(entry),
            "previous_reward": entry_previous_reward(entry),
            "exception": entry.get("exception"),
            "case_label": label,
            "accepted_for_repo_aggregation": accepted_task_entry(entry),
            "task_evidence_has_public_signal": bool(
                entry.get("edited_paths") or entry.get("test_commands") or entry.get("touched_paths")
            ),
            "failure_signature": entry_failure_signature(entry),
            "budget": task_update_budget(label, args),
        }
        task_events.append(event)
        task_event_by_entry_id[entry_id] = event

    promotion_decisions: list[dict[str, Any]] = []
    repo_update_buffer: list[dict[str, Any]] = []
    repo_clusters: list[dict[str, Any]] = []
    repo_candidates: list[dict[str, Any]] = []
    failure_clusters: list[dict[str, Any]] = []
    failure_candidates: list[dict[str, Any]] = []
    evaluator_decisions: list[dict[str, Any]] = []
    evaluator_calibration_events: list[dict[str, Any]] = []
    failure_mode_pool: dict[str, dict[str, list[dict[str, Any]]]] = defaultdict(lambda: defaultdict(list))
    promoted_repo_candidate_keys: set[str] = set()
    promoted_failure_signatures: set[str] = set()
    policy_task_events_buffer: list[dict[str, Any]] = []
    policy_promotion_buffer: list[dict[str, Any]] = []
    policy_update_history: list[dict[str, Any]] = []
    current_writer_policy: dict[str, Any] = {"rules": [], "update_count": 0}
    current_evaluator_policy: dict[str, Any] = {"rules": [], "update_count": 0}
    repo_update_count = 0
    repo_skill_promotions = 0
    failure_mode_trigger_count = 0
    failure_mode_skill_promotions = 0
    pending_repo_events: dict[str, int] = {}
    policy_update_count = 0

    for repo, repo_entries in sorted(entries_by_repo.items()):
        for start in range(0, len(repo_entries), args.repo_update_batch_size):
            batch = repo_entries[start : start + args.repo_update_batch_size]
            if len(batch) < args.repo_update_batch_size:
                pending_repo_events[repo] = len(batch)
                continue
            repo_update_count += 1
            for entry in batch:
                entry_id = str(entry.get("entry_id") or "")
                event = task_event_by_entry_id.get(entry_id)
                if event is not None:
                    policy_task_events_buffer.append(event)
            decision = repo_skill_decision(
                repo=repo,
                batch=batch,
                update_index=repo_update_count,
                args=args,
                evaluator_policy=current_evaluator_policy,
            )
            promotion_decisions.append(decision)
            repo_update_buffer.append(decision)
            policy_promotion_buffer.append(decision)
            if isinstance(decision.get("repo_cluster"), dict):
                repo_clusters.append(decision["repo_cluster"])
            if isinstance(decision.get("candidate"), dict):
                repo_candidates.append(decision["candidate"])
            if isinstance(decision.get("evaluator_decision"), dict):
                evaluator_decisions.append(decision["evaluator_decision"])
                evaluator_calibration_events.append(
                    calibration_event(
                        candidate=decision.get("candidate") or {},
                        decision=decision.get("evaluator_decision") or {},
                        verifier_context={
                            "source": "training_repo_candidate",
                            "case_counts": decision.get("case_counts") or {},
                            "positive_support": decision.get("positive_task_evidence_count"),
                        },
                    )
                )
            repo_batch_promotions = 0
            repo_promotion_budget = max(0, int(args.max_repo_skill_updates_per_batch))
            if decision["decision"] == "promote":
                candidate_name = str((decision.get("candidate") or {}).get("name") or "")
                repo_candidate_key = f"{repo}\t{candidate_name}"
                if repo_batch_promotions >= repo_promotion_budget:
                    decision["decision"] = "stage"
                    decision["reason"] = "repo promotion budget exhausted for this batch"
                    decision["budget_exhausted"] = True
                elif repo_candidate_key in promoted_repo_candidate_keys:
                    decision["decision"] = "refresh"
                    decision["reason"] = "accepted duplicate repo candidate already promoted in this version"
                else:
                    skill_path = write_candidate_skill(
                        version_root=version_root,
                        candidate=decision["candidate"],
                        decision=decision["evaluator_decision"],
                        run_name=run_name,
                    )
                    decision["skill_path"] = str(skill_path)
                    promoted_repo_candidate_keys.add(repo_candidate_key)
                    repo_skill_promotions += 1
                    repo_batch_promotions += 1

            repo_cluster = decision.get("repo_cluster") if isinstance(decision.get("repo_cluster"), dict) else {}
            for signature, count in (decision.get("failure_signature_counts") or {}).items():
                if not signature or signature == "resolved":
                    continue
                cluster_copy = dict(repo_cluster)
                cluster_copy.setdefault("repo", repo)
                cluster_copy.setdefault("failure_signature_counts", decision.get("failure_signature_counts") or {})
                cluster_copy["failure_signature_event_count"] = count
                failure_mode_pool[str(signature)][repo].append(cluster_copy)

            if (
                args.failure_mode_update_every_repo_updates > 0
                and repo_update_count % args.failure_mode_update_every_repo_updates == 0
            ):
                failure_mode_trigger_count += 1
                failure_decisions: list[dict[str, Any]] = []
                for signature, clusters_by_repo in sorted(failure_mode_pool.items()):
                    repo_clusters_for_signature = [
                        cluster
                        for clusters in clusters_by_repo.values()
                        for cluster in clusters
                    ]
                    failure_cluster = build_failure_cluster(
                        signature=signature,
                        repo_clusters=repo_clusters_for_signature,
                        trigger_index=failure_mode_trigger_count,
                    )
                    failure_clusters.append(failure_cluster)
                    failure_candidate = write_failure_mode_candidate(failure_cluster)
                    failure_candidates.append(failure_candidate)
                    failure_evaluator_decision = evaluate_candidate(
                        candidate=failure_candidate,
                        evidence=failure_cluster,
                        verifier_context={
                            "source": "training_failure_mode_pool",
                            "trigger_index": failure_mode_trigger_count,
                        },
                        evaluator_policy=current_evaluator_policy,
                        min_repo_positive_support=args.repo_min_positive_support,
                        min_failure_repo_support=args.failure_mode_min_repo_support,
                    )
                    evaluator_decisions.append(failure_evaluator_decision)
                    evaluator_calibration_events.append(
                        calibration_event(
                            candidate=failure_candidate,
                            decision=failure_evaluator_decision,
                            verifier_context={
                                "source": "training_failure_mode_candidate",
                                "trigger_index": failure_mode_trigger_count,
                                "repo_support_count": failure_cluster.get("repo_support_count"),
                            },
                        )
                    )
                    accepted_failure = (
                        failure_evaluator_decision.get("decision") == "accept"
                        and signature not in promoted_failure_signatures
                    )
                    failure_decision = {
                        "created_at": utc_now(),
                        "level": "failure_mode",
                        "trigger_index": failure_mode_trigger_count,
                        "failure_signature": signature,
                        "repo_support_count": failure_cluster.get("repo_support_count"),
                        "event_support_count": failure_cluster.get("event_support_count"),
                        "support_repos": failure_cluster.get("support_repos") or [],
                        "failure_cluster": failure_cluster,
                        "candidate": failure_candidate,
                        "evaluator_decision": failure_evaluator_decision,
                        "decision": "promote" if accepted_failure else "stage",
                        "candidate_decision": failure_evaluator_decision.get("decision"),
                        "reason": failure_evaluator_decision.get("reason"),
                    }
                    failure_decisions.append(failure_decision)
                if not failure_decisions:
                    failure_decisions.append(
                        {
                            "created_at": utc_now(),
                            "level": "failure_mode",
                            "trigger_index": failure_mode_trigger_count,
                            "decision": "stage",
                            "reason": "no failure signatures in cumulative pool",
                            "repo_update_count": repo_update_count,
                            "candidate_signatures": {},
                        }
                    )
                failure_decisions.sort(
                    key=lambda item: (
                        0 if item.get("decision") == "promote" else 1,
                        -int(item.get("repo_support_count") or 0),
                        -int(item.get("event_support_count") or 0),
                        str(item.get("failure_signature") or ""),
                    )
                )
                failure_trigger_promotions = 0
                failure_promotion_budget = max(0, int(args.max_failure_mode_updates_per_trigger))
                for failure_decision in failure_decisions:
                    if failure_decision.get("decision") == "promote":
                        if failure_trigger_promotions >= failure_promotion_budget:
                            failure_decision["decision"] = "stage"
                            failure_decision["reason"] = "failure-mode promotion budget exhausted for this trigger"
                            failure_decision["budget_exhausted"] = True
                        else:
                            skill_path = write_candidate_skill(
                                version_root=version_root,
                                candidate=failure_decision["candidate"],
                                decision=failure_decision["evaluator_decision"],
                                run_name=run_name,
                            )
                            failure_decision["skill_path"] = str(skill_path)
                            promoted_failure_signatures.add(str(failure_decision.get("failure_signature") or ""))
                            failure_mode_skill_promotions += 1
                            failure_trigger_promotions += 1
                    promotion_decisions.append(failure_decision)
                    policy_promotion_buffer.append(failure_decision)

                if (
                    args.policy_update_every_failure_mode_triggers > 0
                    and failure_mode_trigger_count % args.policy_update_every_failure_mode_triggers == 0
                ):
                    policy_update_count += 1
                    policy_update_state = append_failure_mode_policy_updates(
                        policy_paths=policy_paths,
                        task_events=policy_task_events_buffer,
                        promotion_decisions=policy_promotion_buffer,
                        args=args,
                        update_index=policy_update_count,
                        failure_mode_trigger_count=failure_mode_trigger_count,
                    )
                    policy_update_history.append(policy_update_state)
                    if policy_update_state.get("writer_policy_updates"):
                        current_writer_policy = {
                            "rules": policy_update_state.get("writer_rules") or [],
                            "update_count": current_writer_policy["update_count"] + 1,
                        }
                    if policy_update_state.get("evaluator_policy_updates"):
                        current_evaluator_policy = {
                            "rules": policy_update_state.get("evaluator_rules") or [],
                            "update_count": current_evaluator_policy["update_count"] + 1,
                        }
                    policy_task_events_buffer = []
                    policy_promotion_buffer = []

    if policy_task_events_buffer or policy_promotion_buffer:
        policy_update_count += 1
        policy_update_state = append_failure_mode_policy_updates(
            policy_paths=policy_paths,
            task_events=policy_task_events_buffer,
            promotion_decisions=policy_promotion_buffer,
            args=args,
            update_index=policy_update_count,
            failure_mode_trigger_count=failure_mode_trigger_count,
            tail_window=True,
        )
        policy_update_history.append(policy_update_state)
        if policy_update_state.get("writer_policy_updates"):
            current_writer_policy = {
                "rules": policy_update_state.get("writer_rules") or [],
                "update_count": current_writer_policy["update_count"] + 1,
            }
        if policy_update_state.get("evaluator_policy_updates"):
            current_evaluator_policy = {
                "rules": policy_update_state.get("evaluator_rules") or [],
                "update_count": current_evaluator_policy["update_count"] + 1,
            }

    if policy_update_history:
        final_policy_update_state = {
            "writer_policy_updates": sum(update["writer_policy_updates"] for update in policy_update_history),
            "evaluator_policy_updates": sum(update["evaluator_policy_updates"] for update in policy_update_history),
            "writer_rules": policy_update_history[-1]["writer_rules"],
            "evaluator_rules": policy_update_history[-1]["evaluator_rules"],
            "update_history": policy_update_history,
        }
    else:
        final_policy_update_state = {
            "writer_policy_updates": 0,
            "evaluator_policy_updates": 0,
            "writer_rules": [],
            "evaluator_rules": [],
            "update_history": [],
        }

    state = {
        "schema_version": 1,
        "run_name": run_name,
        "active_version": active_version,
        "created_at": utc_now(),
        "policy_paths": {name: str(path) for name, path in policy_paths.items()},
        "memory_path": str(memory_path),
        "skill_archive_root": str(skill_archive_root),
        "skill_pack_root": str(version_root),
        "hyperparameters": {
            "repo_update_batch_size": args.repo_update_batch_size,
            "repo_min_positive_support": args.repo_min_positive_support,
            "failure_mode_update_every_repo_updates": args.failure_mode_update_every_repo_updates,
            "failure_mode_min_repo_support": args.failure_mode_min_repo_support,
            "policy_update_every_failure_mode_triggers": args.policy_update_every_failure_mode_triggers,
            "max_task_evidence_edits_per_task": args.max_task_evidence_edits_per_task,
            "max_repo_skill_updates_per_batch": args.max_repo_skill_updates_per_batch,
            "max_failure_mode_updates_per_trigger": args.max_failure_mode_updates_per_trigger,
            "policy_max_bullet_updates": args.policy_max_bullet_updates,
        },
        "clocks": {
            "task_evidence_events": len(task_events),
            "repo_level_updates": repo_update_count,
            "failure_mode_update_triggers": failure_mode_trigger_count,
            "writer_policy_updates": final_policy_update_state["writer_policy_updates"],
            "evaluator_policy_updates": final_policy_update_state["evaluator_policy_updates"],
            "policy_update_windows": len(policy_update_history),
        },
        "promotions": {
            "repo_skill_promotions": repo_skill_promotions,
            "failure_mode_skill_promotions": failure_mode_skill_promotions,
        },
        "policy_calibration": {
            "writer_rules": final_policy_update_state["writer_rules"],
            "evaluator_rules": final_policy_update_state["evaluator_rules"],
            "writer_policy": current_writer_policy,
            "evaluator_policy": current_evaluator_policy,
            "update_history": policy_update_history,
        },
        "pending": {
            "repo_task_events_waiting_for_next_batch": pending_repo_events,
        },
        "case_counts": dict(Counter(event["case_label"] for event in task_events)),
        "writer_outputs": {
            "repo_clusters": len(repo_clusters),
            "repo_candidates": len(repo_candidates),
            "failure_clusters": len(failure_clusters),
            "failure_mode_candidates": len(failure_candidates),
        },
        "evaluator_outputs": {
            "decisions": len(evaluator_decisions),
            "calibration_events": len(evaluator_calibration_events),
        },
    }
    evidence_dir = train_dir / "evidence"
    candidates_dir = train_dir / "candidates"
    evaluator_dir = train_dir / "evaluator"
    write_jsonl(train_dir / "skill_update_events.jsonl", task_events)
    write_jsonl(evidence_dir / "task_events.jsonl", task_events)
    write_jsonl(evidence_dir / "repo_clusters.jsonl", repo_clusters)
    write_jsonl(evidence_dir / "failure_clusters.jsonl", failure_clusters)
    write_jsonl(candidates_dir / "repo_candidates.jsonl", repo_candidates)
    write_jsonl(candidates_dir / "failure_mode_candidates.jsonl", failure_candidates)
    write_jsonl(evaluator_dir / "evaluator_decisions.jsonl", evaluator_decisions)
    write_jsonl(evaluator_dir / "evaluator_calibration.jsonl", evaluator_calibration_events)
    write_jsonl(train_dir / "promotion_decisions.jsonl", promotion_decisions)
    write_json(train_dir / "policy_state.json", state)
    update_memory_hierarchical_state(
        memory_path=memory_path,
        version_id=active_version,
        task_events=task_events,
        promotion_decisions=promotion_decisions,
        policy_state=state,
    )
    return state


def create_seed_memory(memory_path: Path, version_id: str, run_name: str) -> None:
    memory = {
        "schema_version": 1,
        "active_version": version_id,
        "versions": {
            version_id: {
                "version_id": version_id,
                "parent_version": None,
                "created_at": utc_now(),
                "source": "seed_general_swe_skills",
                "run_name": run_name,
                "entries": [],
                "aggregates": {
                    "entries": 0,
                    "note": "Seed version contains general process SKILL.md files only.",
                },
            }
        },
    }
    write_json(memory_path, memory)


def export_inference_artifact(
    *,
    train_dir: Path,
    memory_path: Path,
    accepted_version: str,
    skill_pack_root: Path,
    run_name: str,
) -> Path:
    memory = read_json(memory_path)
    version = ((memory.get("versions") or {}).get(accepted_version) or {})
    skill_state = version.get("skill_state") if isinstance(version, dict) else {}
    if not isinstance(skill_state, dict):
        skill_state = {}
    policy_state = version.get("policy_state") if isinstance(version, dict) else {}
    if not isinstance(policy_state, dict):
        policy_state = {}
    artifact = {
        "schema_version": 1,
        "kind": "swegym_trained_downstream_inference_artifact",
        "run_name": run_name,
        "created_at": utc_now(),
        "source_training_memory": str(memory_path),
        "source_training_version": accepted_version,
        "skill_pack_root": str(skill_pack_root),
        "included_for_downstream": [
            "general_swe_skills",
            "accepted_failure_mode_skills",
            "writer_policy",
            "evaluator_policy",
            "harness_policy",
        ],
        "excluded_from_downstream": [
            "swegym_task_evidence",
            "swegym_legacy_task_stage_skills",
            "swegym_repo_scaffold_skills",
            "swegym_validation_verifier_labels",
            "training_trace_memory",
        ],
        "failure_mode_skills": skill_state.get("failure_mode_skills") or [],
        "policies": {
            "writer_policy": policy_state.get("writer_policy") or {},
            "evaluator_policy": policy_state.get("evaluator_policy") or {},
            "harness_policy": policy_state.get("harness_policy") or {},
        },
        "pi_skill_env": {
            "PI_SKILL_PACK_ROOT": str(skill_pack_root),
            "PI_SKILL_RETRIEVAL_SCOPE": "general,failure",
            "PI_USE_SKILL_HARNESS_MEMORY": "false",
        },
    }
    out = train_dir / "inference_artifact.json"
    write_json(out, artifact)
    return out


class WandbLogger:
    def __init__(
        self,
        *,
        enabled: bool,
        project: str,
        entity: str | None,
        run_name: str,
        api_key: str | None,
        config: dict[str, Any],
    ):
        self.enabled = enabled
        self._wandb = None
        self._run = None
        if not enabled:
            return
        try:
            import wandb  # type: ignore
        except ImportError as exc:
            raise SystemExit(
                "wandb is not installed. Run `pip install wandb` or pass --no-wandb."
            ) from exc
        if api_key:
            os.environ["WANDB_API_KEY"] = api_key
        wandb.login(key=api_key or os.getenv("WANDB_API_KEY"), relogin=False)
        self._wandb = wandb
        self._run = wandb.init(
            project=project,
            entity=entity,
            name=run_name,
            config=config,
            job_type="swegym_skill_evo_loop",
        )

    def log(self, data: dict[str, Any], step: int | None = None) -> None:
        if self._run is None:
            return
        self._wandb.log(data, step=step)

    def artifact(self, path: Path, name: str, artifact_type: str) -> None:
        if self._run is None or not path.exists():
            return
        artifact = self._wandb.Artifact(name=name, type=artifact_type)
        if path.is_dir():
            artifact.add_dir(str(path))
        else:
            artifact.add_file(str(path))
        self._run.log_artifact(artifact)

    def finish(self) -> None:
        if self._run is not None:
            self._run.finish()


def benchmark_command(
    args: argparse.Namespace,
    *,
    dataset: Path | str,
    benchmark_name: str,
    job_name: str,
    use_skills: bool,
    task_names_file: Path | None = None,
) -> list[str]:
    command = [
        sys.executable,
        str(ROOT / "scripts" / "run_benchmark.py"),
        "--dataset",
        str(dataset),
        "--benchmark-name",
        benchmark_name,
        "--provider",
        args.provider,
        "--job-name",
        job_name,
        "--concurrency",
        str(args.concurrency),
        "--max-retries",
        str(args.max_retries),
        "--retry-min-wait-sec",
        str(args.retry_min_wait_sec),
        "--retry-max-wait-sec",
        str(args.retry_max_wait_sec),
        "--agent-setup-timeout-sec",
        str(args.agent_setup_timeout_sec),
        "--e2b-sandbox-timeout-sec",
        str(args.e2b_sandbox_timeout_sec),
        "--verifier-buffer-sec",
        str(args.verifier_buffer_sec),
        "--override-cpus",
        str(args.override_cpus),
        "--override-memory-mb",
        str(args.override_memory_mb),
        "--override-storage-mb",
        str(args.override_storage_mb),
    ]
    for retry_include in flatten_list_values(args.retry_include) or []:
        command.extend(["--retry-include", retry_include])
    for retry_exclude in flatten_list_values(args.retry_exclude) or []:
        command.extend(["--retry-exclude", retry_exclude])
    if args.agent_timeout_sec is not None:
        command.extend(["--agent-timeout-sec", str(args.agent_timeout_sec)])
    if args.provider_base_url:
        command.extend(["--provider-base-url", args.provider_base_url])
    if args.provider_model:
        command.extend(["--provider-model", args.provider_model])
    # Keep provider keys in the subprocess environment instead of argv so they
    # do not appear in process listings or exception reprs.
    if args.provider_api:
        command.extend(["--provider-api", args.provider_api])
    if args.model_context_window is not None:
        command.extend(["--model-context-window", str(args.model_context_window)])
    if args.model_max_tokens is not None:
        command.extend(["--model-max-tokens", str(args.model_max_tokens)])
    if task_names_file is not None:
        command.extend(["--task-names-file", str(task_names_file)])
    if args.result_only:
        command.append("--result-only")
    if args.force_build:
        command.append("--force-build")
    if args.keep_sandboxes:
        command.append("--keep-sandboxes")
    command.append("--use-skills" if use_skills else "--no-skills")
    return command


def update_memory_command(
    args: argparse.Namespace,
    *,
    baseline_job_dir: Path,
    previous_job_dir: Path | None = None,
    task_names_file: Path | None = None,
    memory_path: Path,
    task_evidence_dir: Path,
    generated_skill_dir: Path,
    version_id: str,
    append: bool = False,
) -> list[str]:
    command = [
        sys.executable,
        str(ROOT / "scripts" / "update_skill_harness_memory.py"),
        "--job-dir",
        str(baseline_job_dir),
        "--out",
        str(memory_path),
        "--version-id",
        version_id,
        "--task-skill-dir",
        str(task_evidence_dir),
        "--generated-skill-dir",
        str(generated_skill_dir),
    ]
    if previous_job_dir is not None:
        command.extend(["--previous-job-dir", str(previous_job_dir)])
    if task_names_file is not None:
        command.extend(["--task-names-file", str(task_names_file)])
    if append:
        command.append("--append-generated-skill-files")
    if args.summarize_with_backbone:
        command.extend(
            [
                "--summarize-with-backbone",
                "--llm-max-tokens",
                str(args.llm_max_tokens),
                "--skill-resource-max-chars",
                str(args.skill_resource_max_chars),
                "--skill-resource-max-total-chars",
                str(args.skill_resource_max_total_chars),
            ]
        )
    return command


def job_metrics(job_dir: Path) -> dict[str, Any]:
    summary = summarize_job(job_dir)
    return {
        "job_dir": summary["job_dir"],
        "job_name": summary["job_name"],
        "n_trials": summary["n_trials"],
        "n_errors": summary["n_errors"],
        "resolved": summary["resolved"],
        "mean_reward": summary["mean_reward"],
    }


def effective_job_metrics(job_dir: Path) -> dict[str, Any]:
    job_dir = job_dir.expanduser().resolve()
    rows: list[dict[str, Any]] = []
    rewards: list[float] = []
    exception_counts: Counter[str] = Counter()
    for path in trial_result_paths(job_dir):
        result = read_json(path)
        verifier_rewards = (
            result.get("verifier_result", {}).get("rewards")
            if result.get("verifier_result")
            else None
        )
        reward = first_reward_value(verifier_rewards)
        exception_info = result.get("exception_info") or {}
        exception_type = exception_info.get("exception_type") if exception_info else None
        valid = reward is not None and not exception_type
        if exception_type:
            exception_counts[str(exception_type)] += 1
        if valid:
            rewards.append(float(reward))
        rows.append(
            {
                "task_name": result.get("task_name") or result.get("trial_name") or path.parent.name,
                "trial_name": result.get("trial_name") or path.parent.name,
                "reward": reward,
                "exception_type": exception_type,
                "valid": valid,
                "result_path": str(path),
            }
        )

    total = len(rows)
    effective = len(rewards)
    resolved = sum(1 for reward in rewards if reward >= 1.0)
    diagnostic = total - effective
    return {
        "job_dir": str(job_dir),
        "job_name": job_dir.name,
        "n_trials": total,
        "effective_trials": effective,
        "diagnostic_trials": diagnostic,
        "diagnostic_rate": diagnostic / total if total else None,
        "resolved": resolved,
        "unresolved": effective - resolved,
        "effective_mean_reward": sum(rewards) / effective if effective else None,
        "effective_resolved_rate": resolved / effective if effective else None,
        "exception_counts": dict(exception_counts),
        "tasks": rows,
    }


def set_memory_active_version(memory_path: Path, version_id: str) -> None:
    memory = read_json(memory_path)
    versions = memory.get("versions")
    if not isinstance(versions, dict) or version_id not in versions:
        raise SystemExit(f"Cannot activate missing memory version {version_id} in {memory_path}")
    memory["active_version"] = version_id
    write_json(memory_path, memory)


def memory_source_jobs(memory_path: Path) -> set[str]:
    if not memory_path.exists():
        return set()
    memory = read_json(memory_path)
    versions = memory.get("versions")
    if not isinstance(versions, dict):
        return set()
    jobs: set[str] = set()
    for version in versions.values():
        if not isinstance(version, dict):
            continue
        for source_job in version.get("source_jobs") or []:
            jobs.add(Path(str(source_job)).name)
    return jobs


def append_version_validation_decision(
    memory_path: Path,
    *,
    candidate_version: str,
    accepted_version_before: str,
    accepted_version_after: str,
    decision: dict[str, Any],
) -> None:
    if not memory_path.exists():
        return
    memory = read_json(memory_path)
    versions = memory.get("versions")
    if not isinstance(versions, dict):
        return
    version = versions.get(candidate_version)
    if not isinstance(version, dict):
        return
    decision_log = version.setdefault(
        "decision_log",
        {
            "schema_version": 1,
            "task_decisions": [],
            "repo_decisions": [],
            "failure_mode_decisions": [],
            "validation_gate_decisions": [],
        },
    )
    if not isinstance(decision_log, dict):
        decision_log = {
            "schema_version": 1,
            "task_decisions": [],
            "repo_decisions": [],
            "failure_mode_decisions": [],
            "validation_gate_decisions": [],
        }
        version["decision_log"] = decision_log
    decisions = decision_log.setdefault("validation_gate_decisions", [])
    if isinstance(decisions, list):
        decisions.append(
            {
                "created_at": utc_now(),
                "candidate_version": candidate_version,
                "accepted_version_before": accepted_version_before,
                "accepted_version_after": accepted_version_after,
                **decision,
            }
        )
    write_json(memory_path, memory)


def validation_gate_decision(
    *,
    candidate_metrics: dict[str, Any],
    reference_metrics: dict[str, Any] | None,
    baseline_metrics: dict[str, Any] | None,
    args: argparse.Namespace,
) -> dict[str, Any]:
    effective_trials = int(candidate_metrics.get("effective_trials") or 0)
    if effective_trials < args.validation_min_effective_trials:
        return {
            "gate": "reject",
            "reason": "insufficient_effective_validation_trials",
            "candidate_effective_trials": effective_trials,
            "min_effective_trials": args.validation_min_effective_trials,
        }

    diagnostic_rate = candidate_metrics.get("diagnostic_rate")
    if (
        diagnostic_rate is not None
        and args.validation_max_diagnostic_rate is not None
        and float(diagnostic_rate) > args.validation_max_diagnostic_rate
    ):
        return {
            "gate": "reject",
            "reason": "diagnostic_rate_too_high",
            "candidate_diagnostic_rate": diagnostic_rate,
            "max_diagnostic_rate": args.validation_max_diagnostic_rate,
        }

    reference = reference_metrics or baseline_metrics
    candidate_rate = candidate_metrics.get("effective_resolved_rate")
    reference_rate = reference.get("effective_resolved_rate") if reference else None
    if candidate_rate is None:
        return {"gate": "reject", "reason": "missing_candidate_effective_resolved_rate"}
    if reference_rate is None:
        return {"gate": "accept", "reason": "no_reference_validation_metric"}

    delta = float(candidate_rate) - float(reference_rate)
    if delta + args.validation_regression_tolerance < 0:
        return {
            "gate": "reject",
            "reason": "validation_regression",
            "candidate_effective_resolved_rate": candidate_rate,
            "reference_effective_resolved_rate": reference_rate,
            "delta": delta,
            "regression_tolerance": args.validation_regression_tolerance,
        }
    return {
        "gate": "accept",
        "reason": "validation_non_regression",
        "candidate_effective_resolved_rate": candidate_rate,
        "reference_effective_resolved_rate": reference_rate,
        "delta": delta,
        "regression_tolerance": args.validation_regression_tolerance,
    }


def write_phase_report(
    *,
    baseline_job_dir: Path,
    eval_job_dir: Path,
    out_dir: Path,
    extra: dict[str, Any],
) -> dict[str, Any]:
    report = compare_jobs(baseline_job_dir, eval_job_dir)
    report.update(extra)
    write_report(report, out_dir / "score_report.json", out_dir / "score_report.md")
    return report


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Run the proposed SWEGym skill-evolution train/val loop: "
            "train on SWEGym, then validate on a repo-isolated holdout."
        )
    )
    parser.add_argument("--run-name", default=None)
    parser.add_argument("--swegym-dataset", type=Path, default=DEFAULT_SWEGYM_DATASET)
    parser.add_argument("--verified-dataset", default=os.getenv("HARBOR_DATASET", "swe-bench/swe-bench-verified@2"))
    parser.add_argument("--validation-ratio", type=float, default=0.05)
    parser.add_argument(
        "--provider",
        choices=["openai", "tinker", "novita", "openai-compatible", "openai_compatible"],
        default=os.getenv("LLM_PROVIDER", "openai"),
        help="Provider profile. novita/openai-compatible aliases are normalized to openai-compatible mode.",
    )
    parser.add_argument("--provider-base-url", default=os.getenv("PROVIDER_BASE_URL"))
    parser.add_argument("--provider-model", default=os.getenv("PROVIDER_MODEL"))
    parser.add_argument("--provider-api-key", default=os.getenv("PROVIDER_API_KEY"))
    parser.add_argument("--provider-api", default=os.getenv("PROVIDER_API"))
    parser.add_argument("--concurrency", type=int, default=int(os.getenv("E2B_CONCURRENCY", "1")))
    parser.add_argument("--max-retries", type=int, default=int(os.getenv("HARBOR_MAX_RETRIES", "0")))
    parser.add_argument("--retry-min-wait-sec", type=float, default=float(os.getenv("HARBOR_RETRY_MIN_WAIT_SEC", "5")))
    parser.add_argument("--retry-max-wait-sec", type=float, default=float(os.getenv("HARBOR_RETRY_MAX_WAIT_SEC", "60")))
    parser.add_argument(
        "--retry-include",
        action="append",
        default=list_env("HARBOR_RETRY_INCLUDE") or DEFAULT_TRANSIENT_RETRY_EXCEPTIONS,
        help="Exception type to retry in Harbor jobs. Repeatable; comma-separated values are accepted.",
    )
    parser.add_argument(
        "--retry-exclude",
        action="append",
        default=list_env("HARBOR_RETRY_EXCLUDE"),
        help="Exception type to exclude from retry in Harbor jobs. Repeatable; comma-separated values are accepted.",
    )
    parser.add_argument("--benchmark-timeout-sec", type=float, default=None)
    parser.add_argument("--agent-timeout-sec", type=float, default=None)
    parser.add_argument("--agent-setup-timeout-sec", type=float, default=float(os.getenv("AGENT_SETUP_TIMEOUT_SEC", "1200")))
    parser.add_argument("--e2b-sandbox-timeout-sec", type=int, default=int(os.getenv("E2B_SANDBOX_TIMEOUT_SEC", "7200")))
    parser.add_argument(
        "--verifier-buffer-sec",
        type=float,
        default=float(os.getenv("VERIFIER_BUFFER_SEC", str(DEFAULT_VERIFIER_BUFFER_SEC))),
    )
    parser.add_argument("--override-cpus", type=int, default=int(os.getenv("E2B_OVERRIDE_CPUS", "1")))
    parser.add_argument("--override-memory-mb", type=int, default=int(os.getenv("E2B_OVERRIDE_MEMORY_MB", "4096")))
    parser.add_argument("--override-storage-mb", type=int, default=int(os.getenv("E2B_OVERRIDE_STORAGE_MB", "10240")))
    parser.add_argument("--model-context-window", type=int, default=None)
    parser.add_argument("--model-max-tokens", type=int, default=None)
    parser.add_argument("--result-only", action="store_true")
    parser.add_argument("--force-build", action="store_true")
    parser.add_argument("--keep-sandboxes", action="store_true")
    parser.add_argument("--summarize-with-backbone", action="store_true")
    parser.add_argument("--llm-max-tokens", type=int, default=2600)
    parser.add_argument("--skill-resource-max-chars", type=int, default=1200)
    parser.add_argument("--skill-resource-max-total-chars", type=int, default=18000)
    parser.add_argument("--skill-archive-root", type=Path, default=DEFAULT_SKILL_ARCHIVE_ROOT)
    parser.add_argument("--skill-version-id", default=None)
    parser.add_argument("--overwrite-skill-version", action="store_true")
    parser.add_argument(
        "--training-iterations",
        type=int,
        default=int(os.getenv("SKILL_EVO_TRAINING_ITERATIONS", "1")),
        help="Number of skill-assisted train/evolve passes after the no-skills baseline.",
    )
    parser.add_argument(
        "--repo-update-batch-size",
        type=int,
        default=int(os.getenv("SKILL_EVO_REPO_UPDATE_BATCH_SIZE", str(DEFAULT_REPO_UPDATE_BATCH_SIZE))),
        help="Run a repo-level update after this many task-level events from the same repo.",
    )
    parser.add_argument(
        "--repo-min-positive-support",
        type=int,
        default=int(os.getenv("SKILL_EVO_REPO_MIN_POSITIVE_SUPPORT", str(DEFAULT_REPO_MIN_POSITIVE_SUPPORT))),
        help="Minimum verifier-positive task evidence required before promoting a repo skill.",
    )
    parser.add_argument(
        "--failure-mode-update-every-repo-updates",
        type=int,
        default=int(
            os.getenv(
                "SKILL_EVO_FAILURE_MODE_UPDATE_EVERY_REPO_UPDATES",
                str(DEFAULT_FAILURE_MODE_UPDATE_EVERY_REPO_UPDATES),
            )
        ),
        help="Run one failure-mode update trigger after this many repo-level updates.",
    )
    parser.add_argument(
        "--failure-mode-min-repo-support",
        type=int,
        default=int(os.getenv("SKILL_EVO_FAILURE_MODE_MIN_REPO_SUPPORT", str(DEFAULT_FAILURE_MODE_MIN_REPO_SUPPORT))),
        help="Minimum distinct repo support required before promoting a failure-mode skill.",
    )
    parser.add_argument(
        "--max-task-skill-edits-per-task",
        type=int,
        default=int(
            os.getenv(
                "SKILL_EVO_MAX_TASK_EVIDENCE_EDITS_PER_TASK",
                os.getenv("SKILL_EVO_MAX_TASK_SKILL_EDITS_PER_TASK", "3"),
            )
        ),
        help="Compatibility name for the maximum task evidence edits recorded per task.",
    )
    parser.add_argument(
        "--max-repo-skill-updates-per-batch",
        type=int,
        default=int(os.getenv("SKILL_EVO_MAX_REPO_SKILL_UPDATES_PER_BATCH", "2")),
    )
    parser.add_argument(
        "--max-failure-mode-updates-per-trigger",
        type=int,
        default=int(os.getenv("SKILL_EVO_MAX_FAILURE_MODE_UPDATES_PER_TRIGGER", "2")),
    )
    parser.add_argument(
        "--policy-max-bullet-updates",
        type=int,
        default=int(os.getenv("SKILL_EVO_POLICY_MAX_BULLET_UPDATES", "3")),
    )
    parser.add_argument(
        "--policy-update-every-failure-mode-triggers",
        type=int,
        default=int(
            os.getenv(
                "SKILL_EVO_POLICY_UPDATE_EVERY_FAILURE_MODE_TRIGGERS",
                "2",
            )
        ),
        help="Append generator/evaluator policy calibration after this many failure-mode triggers.",
    )
    parser.add_argument(
        "--validation-every-batches",
        type=int,
        default=int(os.getenv("SKILL_EVO_VALIDATION_EVERY_BATCHES", "5")),
        help="Run repo-isolated validation after this many training batches; 0 disables mid-training gates.",
    )
    parser.add_argument(
        "--validation-min-effective-trials",
        type=int,
        default=int(os.getenv("SKILL_EVO_VALIDATION_MIN_EFFECTIVE_TRIALS", "5")),
        help="Reject a candidate version if validation has fewer valid reward-bearing trials.",
    )
    parser.add_argument(
        "--validation-max-diagnostic-rate",
        type=float,
        default=float(os.getenv("SKILL_EVO_VALIDATION_MAX_DIAGNOSTIC_RATE", "0.5")),
        help="Reject a candidate version if validation diagnostics exceed this fraction.",
    )
    parser.add_argument(
        "--validation-regression-tolerance",
        type=float,
        default=float(os.getenv("SKILL_EVO_VALIDATION_REGRESSION_TOLERANCE", "0.0")),
        help="Allowed drop in effective validation resolved rate before rollback.",
    )
    parser.add_argument(
        "--skip-validation-gate",
        action="store_true",
        help="Run validation metrics without rolling back rejected candidate versions.",
    )
    parser.add_argument(
        "--run-verified-test",
        action="store_true",
        help="After train-val, run final skill-assisted evaluation on SWEBench-Verified.",
    )
    parser.add_argument(
        "--seed-skill-root",
        type=Path,
        default=None,
        help=(
            "Root for seed general skills when --skip-training-update is used. "
            "Defaults to run-local training/seed_skills to avoid mutating the global archive."
        ),
    )
    parser.add_argument("--smoke", action="store_true", help="Use tiny splits and skip long full-dataset defaults.")
    parser.add_argument("--smoke-train-tasks", type=int, default=1)
    parser.add_argument("--smoke-validation-tasks", type=int, default=1)
    parser.add_argument("--smoke-verified-tasks-file", type=Path, default=ROOT / "run_logs" / "smoke_tasks_v0002.txt")
    parser.add_argument("--verified-task-names-file", type=Path, default=None)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--skip-training-update", action="store_true")
    parser.add_argument("--baseline-train-job-dir", type=Path, default=None)
    parser.add_argument("--validation-baseline-job-dir", type=Path, default=None)
    parser.add_argument("--validation-skill-job-dir", type=Path, default=None)
    parser.add_argument("--wandb-project", default=os.getenv("WANDB_PROJECT", DEFAULT_WANDB_PROJECT))
    parser.add_argument("--wandb-entity", default=os.getenv("WANDB_ENTITY"))
    parser.add_argument("--wandb-api-key", default=os.getenv("WANDB_API_KEY"))
    parser.add_argument("--no-wandb", action="store_true")
    return parser.parse_args()


def main() -> None:
    load_dotenv(ROOT / ".env", override=False)
    args = parse_args()
    args.max_task_evidence_edits_per_task = args.max_task_skill_edits_per_task
    args.provider = normalize_provider(args.provider)
    if args.provider == "openai":
        args.provider_base_url = args.provider_base_url or os.getenv("OPENAI_COMPAT_BASE_URL")
        args.provider_model = args.provider_model or os.getenv("OPENAI_COMPAT_MODEL")
        args.provider_api_key = args.provider_api_key or os.getenv("OPENAI_COMPAT_API_KEY")
        args.provider_api = args.provider_api or os.getenv("OPENAI_COMPAT_API")
    elif args.provider == "tinker":
        args.provider_base_url = args.provider_base_url or os.getenv("TINKER_BASE_URL")
        args.provider_model = args.provider_model or os.getenv("TINKER_MODEL")
        args.provider_api_key = args.provider_api_key or os.getenv("TINKER_API_KEY")
        args.provider_api = args.provider_api or os.getenv("TINKER_API")

    run_name = args.run_name or f"swegym_glm51_loop_{utc_stamp()}"
    run_dir = DEFAULT_EVO_ROOT / run_name
    split_dir = run_dir / "splits"
    train_dir = run_dir / "training"
    validation_dir = run_dir / "validation"
    for path in (split_dir, train_dir, validation_dir):
        path.mkdir(parents=True, exist_ok=True)

    tasks = load_swegym_tasks(args.swegym_dataset.expanduser().resolve())
    validation_repos, repo_counts = choose_validation_repos(tasks, args.validation_ratio)
    split_info = write_split(
        tasks=tasks,
        validation_repos=validation_repos,
        split_dir=split_dir,
        smoke_train_tasks=args.smoke_train_tasks if args.smoke else None,
        smoke_validation_tasks=args.smoke_validation_tasks if args.smoke else None,
    )
    if split_info["n_validation"] > 0:
        args.validation_min_effective_trials = min(
            args.validation_min_effective_trials,
            split_info["n_validation"],
        )
    repo_stats = {
        "repo_counts": repo_counts,
        "target_validation_tasks": round(len(tasks) * args.validation_ratio),
        **split_info,
    }
    write_json(split_dir / "repo_split_summary.json", repo_stats)

    skill_archive_root = args.skill_archive_root.expanduser().resolve()
    version_id = args.skill_version_id or next_skill_version_id(skill_archive_root)
    existing_versions = archived_skill_versions(skill_archive_root)
    if (
        version_id in existing_versions
        and not args.overwrite_skill_version
        and not args.dry_run
    ):
        raise SystemExit(
            f"Skill archive version already exists: {skill_archive_root / version_id}. "
            "Use --skill-version-id with a new value or --overwrite-skill-version."
        )

    env = os.environ.copy()
    env["SKILL_EVO_RUN_DIR"] = str(run_dir)
    if args.provider_api_key:
        if args.provider == "openai":
            env["OPENAI_COMPAT_API_KEY"] = args.provider_api_key
        elif args.provider == "tinker":
            env["TINKER_API_KEY"] = args.provider_api_key
    if args.provider_base_url:
        env["OPENAI_COMPAT_BASE_URL" if args.provider == "openai" else "TINKER_BASE_URL"] = args.provider_base_url
    if args.provider_model:
        env["OPENAI_COMPAT_MODEL" if args.provider == "openai" else "TINKER_MODEL"] = args.provider_model
    if args.provider_api:
        env["OPENAI_COMPAT_API" if args.provider == "openai" else "TINKER_API"] = args.provider_api

    train_job_name = f"{run_name}_swegym_train_noskills"
    default_train_job_dir = ROOT / "jobs" / train_job_name
    provided_train_job_dir = (
        args.baseline_train_job_dir.expanduser().resolve()
        if args.baseline_train_job_dir
        else None
    )
    train_job_dir = default_train_job_dir
    train_job_complete = not args.dry_run and job_result_is_complete(train_job_dir)
    if not train_job_complete and provided_train_job_dir and job_result_is_complete(provided_train_job_dir):
        train_job_dir = provided_train_job_dir
        train_job_complete = True

    val_base_name = f"{run_name}_swegym_validation_noskills"
    default_val_base_dir = ROOT / "jobs" / val_base_name
    provided_val_base_dir = (
        args.validation_baseline_job_dir.expanduser().resolve()
        if args.validation_baseline_job_dir
        else None
    )
    provided_val_skill_dir = (
        args.validation_skill_job_dir.expanduser().resolve()
        if args.validation_skill_job_dir
        else None
    )
    val_base_dir = default_val_base_dir
    if not job_result_is_complete(val_base_dir) and provided_val_base_dir and job_result_is_complete(provided_val_base_dir):
        val_base_dir = provided_val_base_dir
    final_val_skill_name = f"{run_name}_swegym_validation_accepted_skills"
    final_val_skill_dir = ROOT / "jobs" / final_val_skill_name
    if (
        not job_result_is_complete(final_val_skill_dir)
        and provided_val_skill_dir
        and job_result_is_complete(provided_val_skill_dir)
    ):
        final_val_skill_dir = provided_val_skill_dir
    validation_jobs_complete = not args.dry_run and job_result_is_complete(val_base_dir)
    benchmark_runs = not args.dry_run and (
        not train_job_complete
        or not validation_jobs_complete
        or not job_result_is_complete(final_val_skill_dir)
        or args.training_iterations > 0
        or args.run_verified_test
    )
    missing_env = missing_runtime_env(args, env, benchmark_runs=benchmark_runs)
    manifest: dict[str, Any] = {
        "schema_version": 1,
        "run_name": run_name,
        "created_at": utc_now(),
        "mode": "swegym_train_val_loop",
        "dry_run": args.dry_run,
        "smoke": args.smoke,
        "provider": args.provider,
        "swegym_dataset": str(args.swegym_dataset),
        "verified_dataset": args.verified_dataset,
        "split": repo_stats,
        "skill_version_id": version_id,
        "skill_archive_root": str(skill_archive_root),
        "hierarchical_skill_update": {
            "training_iterations": args.training_iterations,
            "repo_update_batch_size": args.repo_update_batch_size,
            "failure_mode_update_every_repo_updates": args.failure_mode_update_every_repo_updates,
            "failure_mode_min_repo_support": args.failure_mode_min_repo_support,
            "policy_update_every_failure_mode_triggers": args.policy_update_every_failure_mode_triggers,
            "max_task_evidence_edits_per_task": args.max_task_evidence_edits_per_task,
            "max_repo_skill_updates_per_batch": args.max_repo_skill_updates_per_batch,
            "max_failure_mode_updates_per_trigger": args.max_failure_mode_updates_per_trigger,
            "policy_max_bullet_updates": args.policy_max_bullet_updates,
            "validation_every_batches": args.validation_every_batches,
            "validation_min_effective_trials": args.validation_min_effective_trials,
            "validation_max_diagnostic_rate": args.validation_max_diagnostic_rate,
            "validation_regression_tolerance": args.validation_regression_tolerance,
            "skip_validation_gate": args.skip_validation_gate,
        },
        "missing_runtime_env": missing_env,
    }
    write_json(run_dir / "manifest.json", manifest)

    wandb = WandbLogger(
        enabled=not args.no_wandb and not args.dry_run,
        project=args.wandb_project,
        entity=args.wandb_entity,
        run_name=run_name,
        api_key=args.wandb_api_key,
        config={
            key: value
            for key, value in manifest.items()
            if key not in {"split"}
        }
        | {
            "validation_repos": sorted(validation_repos),
            "n_swegym_tasks": len(tasks),
            "n_train_tasks": split_info["n_train"],
            "n_validation_tasks": split_info["n_validation"],
        },
    )

    if missing_env:
        wandb.log(
            {
                "preflight/missing_env_count": len(missing_env),
                "preflight/blocked": 1,
            },
            step=0,
        )
        wandb.artifact(run_dir / "manifest.json", f"{run_name}-manifest", "manifest")
        wandb.finish()
        raise SystemExit(
            "Missing required runtime environment variables: "
            + ", ".join(missing_env)
            + ". Provide them through the shell or .env, then rerun."
        )

    try:
        train_memory_path = train_dir / "skill_harness_memory.json"
        train_task_evidence_dir = train_dir / "task_evidence_cards"

        if train_job_complete:
            log(f"using existing complete no-skills baseline job: {train_job_dir}")
        else:
            run_command(
                benchmark_command(
                    args,
                    dataset=args.swegym_dataset,
                    benchmark_name="swe-gym",
                    job_name=train_job_name,
                    use_skills=False,
                    task_names_file=split_dir / "train_tasks.txt",
                ),
                env=env,
                dry_run=args.dry_run,
                timeout_sec=args.benchmark_timeout_sec,
            )
            train_job_complete = not args.dry_run and job_result_is_complete(train_job_dir)

        if not args.dry_run and train_job_complete:
            metrics = job_metrics(train_job_dir)
            write_json(train_dir / "baseline_metrics.json", metrics)
            wandb.log(
                {
                    "training/n_trials": metrics["n_trials"],
                    "training/n_errors": metrics["n_errors"],
                    "training/resolved": metrics["resolved"],
                    "training/mean_reward": metrics["mean_reward"],
                },
                step=1,
            )

        if not train_job_complete and not args.dry_run:
            raise SystemExit(f"No-skills baseline job did not complete successfully: {train_job_dir}")

        training_batches = write_repo_training_batches(
            train_tasks_file=split_dir / "train_tasks.txt",
            tasks=tasks,
            batch_size=args.repo_update_batch_size,
            out_dir=split_dir / "training_batches",
        )
        training_iteration_jobs: list[dict[str, Any]] = []
        validation_gate_history: list[dict[str, Any]] = []
        active_version = version_id
        accepted_version = version_id
        accepted_validation_metrics: dict[str, Any] | None = None
        validation_baseline_metrics: dict[str, Any] | None = None
        val_base_complete = not args.dry_run and job_result_is_complete(val_base_dir)
        if not val_base_complete:
            run_command(
                benchmark_command(
                    args,
                    dataset=args.swegym_dataset,
                    benchmark_name="swe-gym",
                    job_name=val_base_name,
                    use_skills=False,
                    task_names_file=split_dir / "validation_tasks.txt",
                ),
                env=env,
                dry_run=args.dry_run,
                timeout_sec=args.benchmark_timeout_sec,
            )
            val_base_complete = not args.dry_run and job_result_is_complete(val_base_dir)
        if not args.dry_run and val_base_complete:
            validation_baseline_metrics = effective_job_metrics(val_base_dir)
            accepted_validation_metrics = validation_baseline_metrics
            write_json(validation_dir / "baseline_effective_metrics.json", validation_baseline_metrics)
            wandb.log(
                {
                    "validation_baseline/effective_trials": validation_baseline_metrics["effective_trials"],
                    "validation_baseline/diagnostic_trials": validation_baseline_metrics["diagnostic_trials"],
                    "validation_baseline/effective_resolved_rate": validation_baseline_metrics["effective_resolved_rate"],
                    "validation_baseline/effective_mean_reward": validation_baseline_metrics["effective_mean_reward"],
                },
                step=1,
            )
        if not val_base_complete and not args.dry_run:
            raise SystemExit(f"Validation no-skills baseline job did not complete: {val_base_dir}")

        if args.skip_training_update:
            create_seed_memory(train_memory_path, version_id, run_name)
            seed_skill_root = args.seed_skill_root.expanduser().resolve() if args.seed_skill_root else train_dir / "seed_skills"
            skill_pack_root = materialize_general_skill_seeds(
                skill_root=seed_skill_root,
                version_id=version_id,
                run_name=run_name,
            )
            policy_state = write_hierarchical_training_artifacts(
                args=args,
                train_dir=train_dir,
                memory_path=train_memory_path,
                skill_archive_root=seed_skill_root,
                version_id=version_id,
                run_name=run_name,
            )
            active_version = version_id
            accepted_version = version_id
        else:
            if not args.dry_run and not (train_job_dir / "result.json").exists():
                raise SystemExit(f"Missing training baseline result: {train_job_dir / 'result.json'}")
            if not args.dry_run and train_memory_path.exists():
                log(f"resuming existing training memory: {train_memory_path}")
                active_version = read_active_version(train_memory_path)
            else:
                run_command(
                    update_memory_command(
                        args,
                        baseline_job_dir=train_job_dir,
                        memory_path=train_memory_path,
                        task_evidence_dir=train_task_evidence_dir,
                        generated_skill_dir=skill_archive_root,
                        version_id=version_id,
                        append=False,
                    ),
                    env=env,
                    dry_run=args.dry_run,
                )
                if not args.dry_run and train_memory_path.exists():
                    active_version = read_active_version(train_memory_path)
            skill_pack_root = skill_archive_root / active_version
            if not args.dry_run:
                materialize_general_skill_seeds(
                    skill_root=skill_archive_root,
                    version_id=active_version,
                    run_name=run_name,
                )
                policy_state = write_hierarchical_training_artifacts(
                    args=args,
                    train_dir=train_dir,
                    memory_path=train_memory_path,
                    skill_archive_root=skill_archive_root,
                    version_id=active_version,
                    run_name=run_name,
                )
            else:
                policy_state = {
                    "clocks": {},
                    "promotions": {},
                }
            batch_base_version = memory_base_version(train_memory_path, version_id)
            accepted_version = active_version
            accepted_skill_pack_root = skill_pack_root

            for iteration in range(1, max(0, args.training_iterations) + 1):
                env["PI_SKILL_HARNESS_MEMORY_PATH"] = str(train_memory_path)
                env["PI_SKILL_PACK_ROOT"] = str(accepted_skill_pack_root)
                env["PI_USE_SKILL_HARNESS_MEMORY"] = "true"
                env["PI_SKILL_RETRIEVAL_SCOPE"] = "all"
                for batch in training_batches:
                    batch_index = int(batch["batch_index"])
                    batch_file = Path(str(batch["task_names_file"]))
                    skill_job_name = f"{run_name}_train_iter{iteration:02d}_batch{batch_index:04d}_skills"
                    skill_job_dir = ROOT / "jobs" / skill_job_name
                    if not job_result_is_complete(skill_job_dir):
                        run_command(
                            benchmark_command(
                                args,
                                dataset=args.swegym_dataset,
                                benchmark_name="swe-gym",
                                job_name=skill_job_name,
                                use_skills=True,
                                task_names_file=batch_file,
                            ),
                            env=env,
                            dry_run=args.dry_run,
                            timeout_sec=args.benchmark_timeout_sec,
                        )
                    if not args.dry_run and not job_result_is_complete(skill_job_dir):
                        raise SystemExit(f"Skill-assisted training batch did not complete: {skill_job_dir}")

                    batch_version_id = version_id_with_offset(
                        batch_base_version,
                        ((iteration - 1) * len(training_batches)) + batch_index,
                    )
                    if not args.dry_run and skill_job_name in memory_source_jobs(train_memory_path):
                        log(f"memory already contains training batch job; skipping update: {skill_job_name}")
                    else:
                        run_command(
                            update_memory_command(
                                args,
                                baseline_job_dir=skill_job_dir,
                                previous_job_dir=(
                                    train_job_dir
                                    if iteration == 1
                                    else ROOT / "jobs" / f"{run_name}_train_iter{iteration - 1:02d}_batch{batch_index:04d}_skills"
                                ),
                                task_names_file=batch_file,
                                memory_path=train_memory_path,
                                task_evidence_dir=train_task_evidence_dir,
                                generated_skill_dir=skill_archive_root,
                                version_id=batch_version_id,
                                append=True,
                            ),
                            env=env,
                            dry_run=args.dry_run,
                        )
                    if not args.dry_run and train_memory_path.exists():
                        active_version = read_active_version(train_memory_path)
                    else:
                        active_version = batch_version_id
                    skill_pack_root = skill_archive_root / active_version
                    if not args.dry_run:
                        materialize_general_skill_seeds(
                            skill_root=skill_archive_root,
                            version_id=active_version,
                            run_name=run_name,
                        )
                        policy_state = write_hierarchical_training_artifacts(
                            args=args,
                            train_dir=train_dir,
                            memory_path=train_memory_path,
                            skill_archive_root=skill_archive_root,
                            version_id=active_version,
                            run_name=run_name,
                        )
                    candidate_version = active_version
                    candidate_skill_pack_root = skill_pack_root
                    env["PI_SKILL_HARNESS_MEMORY_PATH"] = str(train_memory_path)
                    env["PI_SKILL_PACK_ROOT"] = str(candidate_skill_pack_root)
                    env["PI_USE_SKILL_HARNESS_MEMORY"] = "true"
                    env["PI_SKILL_RETRIEVAL_SCOPE"] = "all"
                    training_iteration_jobs.append(
                        {
                            "iteration": iteration,
                            "batch_index": batch_index,
                            "repo_slug": batch["repo_slug"],
                            "task_count": batch["task_count"],
                            "job_dir": str(skill_job_dir),
                            "task_names_file": str(batch_file),
                            "skill_version_id": candidate_version,
                        }
                    )
                    should_validate = (
                        args.validation_every_batches > 0
                        and batch_index % args.validation_every_batches == 0
                    ) or batch_index == len(training_batches)
                    if should_validate:
                        gate_index = len(validation_gate_history) + 1
                        gate_job_name = (
                            f"{run_name}_validation_iter{iteration:02d}_gate{gate_index:04d}_skills"
                        )
                        gate_job_dir = ROOT / "jobs" / gate_job_name
                        env["PI_SKILL_HARNESS_MEMORY_PATH"] = str(train_memory_path)
                        env["PI_SKILL_PACK_ROOT"] = str(candidate_skill_pack_root)
                        env["PI_USE_SKILL_HARNESS_MEMORY"] = "false"
                        env["PI_SKILL_RETRIEVAL_SCOPE"] = "transfer"
                        if not job_result_is_complete(gate_job_dir):
                            run_command(
                                benchmark_command(
                                    args,
                                    dataset=args.swegym_dataset,
                                    benchmark_name="swe-gym",
                                    job_name=gate_job_name,
                                    use_skills=True,
                                    task_names_file=split_dir / "validation_tasks.txt",
                                ),
                                env=env,
                                dry_run=args.dry_run,
                                timeout_sec=args.benchmark_timeout_sec,
                            )
                        if not args.dry_run and not job_result_is_complete(gate_job_dir):
                            raise SystemExit(f"Validation gate job did not complete: {gate_job_dir}")
                        if args.dry_run:
                            gate_decision = {"gate": "dry_run", "reason": "dry_run"}
                            candidate_metrics = {}
                        else:
                            candidate_metrics = effective_job_metrics(gate_job_dir)
                            gate_decision = validation_gate_decision(
                                candidate_metrics=candidate_metrics,
                                reference_metrics=accepted_validation_metrics,
                                baseline_metrics=validation_baseline_metrics,
                                args=args,
                            )
                            if args.skip_validation_gate and gate_decision.get("gate") == "reject":
                                gate_decision = {
                                    **gate_decision,
                                    "gate": "accept",
                                    "reason": f"skip_validation_gate_overrode_{gate_decision.get('reason')}",
                                }

                        accepted_before = accepted_version
                        if gate_decision.get("gate") == "accept":
                            accepted_version = candidate_version
                            accepted_skill_pack_root = candidate_skill_pack_root
                            accepted_validation_metrics = candidate_metrics or accepted_validation_metrics
                        elif not args.dry_run:
                            set_memory_active_version(train_memory_path, accepted_version)
                            active_version = accepted_version
                            skill_pack_root = accepted_skill_pack_root

                        gate_record = {
                            "gate_index": gate_index,
                            "iteration": iteration,
                            "batch_index": batch_index,
                            "candidate_version": candidate_version,
                            "accepted_version_before": accepted_before,
                            "accepted_version_after": accepted_version,
                            "job_dir": str(gate_job_dir),
                            "metrics": candidate_metrics,
                            "decision": gate_decision,
                        }
                        validation_gate_history.append(gate_record)
                        write_json(
                            validation_dir / "validation_gate_history.json",
                            {"gates": validation_gate_history},
                        )
                        if not args.dry_run:
                            append_version_validation_decision(
                                train_memory_path,
                                candidate_version=candidate_version,
                                accepted_version_before=accepted_before,
                                accepted_version_after=accepted_version,
                                decision={
                                    "gate_index": gate_index,
                                    "iteration": iteration,
                                    "batch_index": batch_index,
                                    "job_dir": str(gate_job_dir),
                                    "metrics": candidate_metrics,
                                    **gate_decision,
                                },
                            )
                            wandb.log(
                                {
                                    "validation_gate/index": gate_index,
                                    "validation_gate/accepted": 1 if gate_decision.get("gate") == "accept" else 0,
                                    "validation_gate/effective_trials": candidate_metrics.get("effective_trials"),
                                    "validation_gate/diagnostic_rate": candidate_metrics.get("diagnostic_rate"),
                                    "validation_gate/effective_resolved_rate": candidate_metrics.get("effective_resolved_rate"),
                                    "validation_gate/candidate_version_numeric": int(candidate_version.lstrip("v")) if candidate_version.startswith("v") and candidate_version[1:].isdigit() else 0,
                                    "validation_gate/accepted_version_numeric": int(accepted_version.lstrip("v")) if accepted_version.startswith("v") and accepted_version[1:].isdigit() else 0,
                                },
                                step=gate_index,
                            )
                        env["PI_SKILL_HARNESS_MEMORY_PATH"] = str(train_memory_path)
                        env["PI_SKILL_PACK_ROOT"] = str(accepted_skill_pack_root)
                        env["PI_USE_SKILL_HARNESS_MEMORY"] = "true"
                        env["PI_SKILL_RETRIEVAL_SCOPE"] = "all"
                write_json(train_dir / "training_iteration_jobs.json", {"jobs": training_iteration_jobs})
            active_version = accepted_version
            skill_pack_root = accepted_skill_pack_root
            if not args.dry_run:
                set_memory_active_version(train_memory_path, accepted_version)

        env["PI_SKILL_HARNESS_MEMORY_PATH"] = str(train_memory_path)
        env["PI_SKILL_PACK_ROOT"] = str(skill_pack_root)
        env["PI_USE_SKILL_HARNESS_MEMORY"] = "false"
        env["PI_SKILL_RETRIEVAL_SCOPE"] = "transfer"
        wandb.log(
            {
                "stage/training_prepared": 1,
                "skill/version": version_id,
                "training/task_evidence_events": (policy_state.get("clocks") or {}).get("task_evidence_events"),
                "training/repo_level_updates": (policy_state.get("clocks") or {}).get("repo_level_updates"),
                "training/failure_mode_update_triggers": (policy_state.get("clocks") or {}).get("failure_mode_update_triggers"),
                "training/repo_skill_promotions": (policy_state.get("promotions") or {}).get("repo_skill_promotions"),
                "training/failure_mode_skill_promotions": (policy_state.get("promotions") or {}).get("failure_mode_skill_promotions"),
            },
            step=1,
        )

        env["PI_SKILL_HARNESS_MEMORY_PATH"] = str(train_memory_path)
        env["PI_SKILL_PACK_ROOT"] = str(skill_pack_root)
        env["PI_USE_SKILL_HARNESS_MEMORY"] = "false"
        env["PI_SKILL_RETRIEVAL_SCOPE"] = "transfer"
        if not job_result_is_complete(final_val_skill_dir):
            run_command(
                benchmark_command(
                    args,
                    dataset=args.swegym_dataset,
                    benchmark_name="swe-gym",
                    job_name=final_val_skill_name,
                    use_skills=True,
                    task_names_file=split_dir / "validation_tasks.txt",
                ),
                env=env,
                dry_run=args.dry_run,
                timeout_sec=args.benchmark_timeout_sec,
            )
        if not args.dry_run and job_result_is_complete(val_base_dir) and job_result_is_complete(final_val_skill_dir):
            report = write_phase_report(
                baseline_job_dir=val_base_dir,
                eval_job_dir=final_val_skill_dir,
                out_dir=validation_dir,
                extra={
                    "phase": "swegym_validation",
                    "split": "repo_isolated",
                    "accepted_skill_version": active_version,
                },
            )
            final_effective_metrics = effective_job_metrics(final_val_skill_dir)
            write_json(validation_dir / "accepted_skill_effective_metrics.json", final_effective_metrics)
            wandb.log(
                {
                    "validation/baseline_mean_reward": report["baseline"]["mean_reward"],
                    "validation/skill_mean_reward": report["evaluation"]["mean_reward"],
                    "validation/mean_delta": report["mean_delta"],
                    "validation/resolved_delta": report["resolved_delta"],
                    "validation/effective_resolved_rate": final_effective_metrics["effective_resolved_rate"],
                    "validation/effective_trials": final_effective_metrics["effective_trials"],
                },
                step=2,
            )

        inference_artifact_path = None
        if not args.dry_run and train_memory_path.exists():
            inference_artifact_path = export_inference_artifact(
                train_dir=train_dir,
                memory_path=train_memory_path,
                accepted_version=active_version,
                skill_pack_root=skill_pack_root,
                run_name=run_name,
            )

        verified_job_dir = None
        if args.run_verified_test:
            verified_job_name = f"{run_name}_swebench_verified_skills"
            verified_job_dir = ROOT / "jobs" / verified_job_name
            env["PI_SKILL_PACK_ROOT"] = str(skill_pack_root)
            env["PI_USE_SKILL_HARNESS_MEMORY"] = "false"
            env["PI_SKILL_RETRIEVAL_SCOPE"] = "general,failure"
            if not job_result_is_complete(verified_job_dir):
                run_command(
                    benchmark_command(
                        args,
                        dataset=args.verified_dataset,
                        benchmark_name="swe-bench",
                        job_name=verified_job_name,
                        use_skills=True,
                        task_names_file=(
                            args.smoke_verified_tasks_file
                            if args.smoke and args.smoke_verified_tasks_file
                            else args.verified_task_names_file
                        ),
                    ),
                    env=env,
                    dry_run=args.dry_run,
                    timeout_sec=args.benchmark_timeout_sec,
                )
            if not args.dry_run and job_result_is_complete(verified_job_dir):
                verified_metrics = effective_job_metrics(verified_job_dir)
                write_json(validation_dir / "swebench_verified_effective_metrics.json", verified_metrics)
                wandb.log(
                    {
                        "swebench_verified/effective_trials": verified_metrics["effective_trials"],
                        "swebench_verified/diagnostic_trials": verified_metrics["diagnostic_trials"],
                        "swebench_verified/effective_resolved_rate": verified_metrics["effective_resolved_rate"],
                        "swebench_verified/effective_mean_reward": verified_metrics["effective_mean_reward"],
                    },
                    step=3,
                )

        manifest.update(
            {
                "updated_at": utc_now(),
                "training_job_dir": str(train_job_dir),
                "training_memory_path": str(train_memory_path),
                "skill_pack_root": str(skill_pack_root),
                "active_skill_version_id": active_version,
                "accepted_skill_version_id": active_version,
                "validation_gate_history": str(validation_dir / "validation_gate_history.json"),
                "inference_artifact_path": str(inference_artifact_path) if inference_artifact_path else None,
                "verified_skill_job_dir": str(verified_job_dir) if verified_job_dir else None,
                "pi_skill_env": {
                    "PI_SKILL_HARNESS_MEMORY_PATH": str(train_memory_path),
                    "PI_SKILL_PACK_ROOT": str(skill_pack_root),
                    "PI_USE_SKILL_HARNESS_MEMORY": "false",
                    "PI_SKILL_RETRIEVAL_SCOPE": "transfer",
                },
                "training_pi_skill_env": {
                    "PI_SKILL_HARNESS_MEMORY_PATH": str(train_memory_path),
                    "PI_SKILL_PACK_ROOT": str(skill_pack_root),
                    "PI_USE_SKILL_HARNESS_MEMORY": "true",
                    "PI_SKILL_RETRIEVAL_SCOPE": "all",
                },
                "validation_pi_skill_env": {
                    "PI_SKILL_HARNESS_MEMORY_PATH": str(train_memory_path),
                    "PI_SKILL_PACK_ROOT": str(skill_pack_root),
                    "PI_USE_SKILL_HARNESS_MEMORY": "false",
                    "PI_SKILL_RETRIEVAL_SCOPE": "transfer",
                },
                "downstream_pi_skill_env": {
                    "PI_SKILL_PACK_ROOT": str(skill_pack_root),
                    "PI_USE_SKILL_HARNESS_MEMORY": "false",
                    "PI_SKILL_RETRIEVAL_SCOPE": "general,failure",
                },
            }
        )
        write_json(run_dir / "manifest.json", manifest)

        wandb.artifact(run_dir / "manifest.json", f"{run_name}-manifest", "manifest")
        wandb.artifact(split_dir, f"{run_name}-splits", "dataset-split")
        if not args.dry_run:
            wandb.artifact(train_dir, f"{run_name}-training", "training-artifacts")
            for report_path, name in (
                (validation_dir / "score_report.json", "validation-report"),
            ):
                wandb.artifact(report_path, f"{run_name}-{name}", "score-report")
    finally:
        wandb.finish()

    log(f"wrote manifest: {run_dir / 'manifest.json'}")
    log(f"wrote split summary: {split_dir / 'repo_split_summary.json'}")


if __name__ == "__main__":
    main()
