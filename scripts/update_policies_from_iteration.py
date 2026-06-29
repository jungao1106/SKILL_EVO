#!/usr/bin/env python
import argparse
import glob
import json
import re
import statistics
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_POLICY_ROOT = ROOT / "evolution" / "policies"
DEFAULT_SKILL_ROOT = ROOT / "skills" / "tasks"
HARNESS_POLICY_PATH = ROOT / "evolution" / "harness_policy.md"
REWARD_KEYS = ("reward", "resolved", "success", "pass")
STAGES = ("reproduce", "localize", "edit", "validate", "recover")
GENERIC_SKILL_PHRASES = (
    "inspect the repository",
    "high-signal paths",
    "behavior-owning module",
    "run the nearest focused test",
    "small targeted change",
    "stop the loop",
    "re-localize",
    "weak priors",
    "use as a retrieval hint",
)
PRIVATE_PATH_PATTERNS = (
    r"/testbed\b",
    r"/opt/miniconda",
    r"/tmp/(?!pi-skills\b)",
    r"/root/",
    r"/home/",
)
SOURCE_PATH_RE = re.compile(
    r"(?<![A-Za-z0-9_.-])(?:[A-Za-z0-9_.-]+/)+[A-Za-z0-9_.-]+"
    r"\.(?:py|pyi|js|ts|tsx|jsx|rst|txt|cfg|ini|toml|yml|yaml|json|md)"
)
TEST_COMMAND_RE = re.compile(
    r"(?:(?:python\s+-m\s+)?pytest|tox\b|unittest\b|runtests\.py|manage\.py\s+test|"
    r"python\s+tests?/[^\\n`]+)"
)
DEFAULT_POLICY_SAMPLE_LIMITS = {
    "positive": 8,
    "strong_negative": 8,
    "weak_negative": 8,
    "stable_positive": 4,
}
HARNESS_CASE_POLICY: dict[str, dict[str, Any]] = {
    "positive": {
        "definition": "previous reward 0 -> current reward 1",
        "polarity": "positive",
        "policy_weight": 1.0,
        "allowed_edit_degree": "preserve_distill_only",
        "max_stage_edits": 1,
        "allowed_curator_ops": ["preserve_stage_skill", "compress_stage_skill"],
        "reward_update_goal": "learn label-free evidence that predicted a true improvement",
        "curator_update_goal": "preserve the useful pattern without broadening it",
    },
    "strong_negative": {
        "definition": "previous reward 1 -> current reward 0",
        "polarity": "negative",
        "policy_weight": 3.0,
        "allowed_edit_degree": "major_delete_or_rewrite",
        "max_stage_edits": 5,
        "allowed_curator_ops": [
            "delete_stage_skill",
            "rewrite_stage_skill",
            "add_stop_condition",
            "disable_task_skill",
        ],
        "reward_update_goal": "reduce false accepts that turn solved tasks into failures",
        "curator_update_goal": "remove or sharply narrow the harmful skill behavior",
    },
    "weak_negative": {
        "definition": "previous reward 0 -> current reward 0",
        "polarity": "negative",
        "policy_weight": 0.75,
        "allowed_edit_degree": "bounded_targeted_rewrite",
        "max_stage_edits": 2,
        "allowed_curator_ops": [
            "rewrite_stage_skill",
            "add_stop_condition",
            "compress_stage_skill",
        ],
        "reward_update_goal": "learn why the skill failed to help without over-penalizing it as a regression",
        "curator_update_goal": "make a small targeted improvement from the failed behavior",
    },
    "unpaired_current_zero": {
        "definition": "current reward 0 but previous reward is unavailable or non-binary",
        "polarity": "diagnostic_negative",
        "policy_weight": 0.25,
        "allowed_edit_degree": "diagnostic_only",
        "max_stage_edits": 1,
        "allowed_curator_ops": ["add_stop_condition", "compress_stage_skill"],
        "reward_update_goal": "record current failure evidence without treating it as an oracle pair",
        "curator_update_goal": "only add conservative recover/validate safeguards",
    },
    "current_error_diagnostic": {
        "definition": "current round has an exception or missing verifier reward",
        "polarity": "diagnostic_negative",
        "policy_weight": 0.25,
        "allowed_edit_degree": "diagnostic_only",
        "max_stage_edits": 1,
        "allowed_curator_ops": ["add_stop_condition", "compress_stage_skill"],
        "reward_update_goal": "track runtime/setup risk separately from reward-paired labels",
        "curator_update_goal": "only update recover/validate safeguards",
    },
}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(errors="replace"))


def first_reward_value(rewards: Any) -> float | None:
    if not rewards:
        return None
    if isinstance(rewards, dict):
        for key in REWARD_KEYS:
            if key in rewards:
                try:
                    return float(rewards[key])
                except (TypeError, ValueError):
                    return None
        try:
            return float(next(iter(rewards.values())))
        except (StopIteration, TypeError, ValueError):
            return None
    if isinstance(rewards, list):
        if not rewards:
            return None
        try:
            return float(rewards[0])
        except (TypeError, ValueError):
            return None
    try:
        return float(rewards)
    except (TypeError, ValueError):
        return None


def reward_from_result(result: dict[str, Any]) -> float | None:
    verifier = result.get("verifier_result")
    if not isinstance(verifier, dict):
        return None
    return first_reward_value(verifier.get("rewards"))


def task_slug_from_name(task_name: str | None) -> str:
    text = task_name or ""
    patterns = (
        r"swe-bench/([A-Za-z0-9_.-]+__[A-Za-z0-9_.-]+-\d+)(?=__|$|[^A-Za-z0-9_.-])",
        r"(?<![A-Za-z0-9_.-])([A-Za-z0-9_.-]+__[A-Za-z0-9_.-]+-\d+)(?=__|$|[^A-Za-z0-9_.-])",
    )
    for pattern in patterns:
        match = re.search(pattern, text)
        if match:
            return match.group(1)
    return ""


def repo_from_task_slug(task_slug: str) -> str:
    if "__" not in task_slug:
        return "unknown"
    return task_slug.split("__", 1)[0]


def expand_paths(paths: list[str], globs: list[str]) -> list[Path]:
    expanded: list[Path] = []
    for value in paths:
        expanded.append(Path(value).expanduser())
    for pattern in globs:
        matches = glob.glob(pattern)
        expanded.extend(Path(match).expanduser() for match in matches)
    unique: list[Path] = []
    seen: set[Path] = set()
    for path in expanded:
        resolved = path.resolve()
        if resolved not in seen:
            unique.append(resolved)
            seen.add(resolved)
    return sorted(unique)


def trial_result_paths(job_dirs: list[Path]) -> list[Path]:
    paths: list[Path] = []
    for job_dir in job_dirs:
        if not job_dir.exists():
            raise FileNotFoundError(f"Missing job dir: {job_dir}")
        for path in sorted(job_dir.glob("*/result.json")):
            if path.parent == job_dir:
                continue
            paths.append(path)
    return paths


def trial_row(result_path: Path) -> dict[str, Any]:
    result = load_json(result_path)
    exception = result.get("exception_info") or {}
    task_name = result.get("task_name")
    task_slug = task_slug_from_name(task_name)
    row = {
        "task_name": task_name,
        "task_slug": task_slug,
        "repo": repo_from_task_slug(task_slug),
        "trial_name": result.get("trial_name") or result_path.parent.name,
        "job_dir": str(result_path.parent.parent),
        "result_path": str(result_path),
        "reward": reward_from_result(result),
        "exception_type": exception.get("exception_type") if exception else None,
        "exception_message": exception.get("exception_message") if exception else None,
    }
    metadata_path = result_path.parent / "agent" / "pi-metadata.json"
    if metadata_path.exists():
        try:
            metadata = load_json(metadata_path)
        except (OSError, ValueError):
            metadata = {}
        row["pi_metadata_path"] = str(metadata_path)
        row["skills_count"] = metadata.get("skills_count")
        row["skill_retrieval_filter"] = metadata.get("skill_retrieval_filter") or metadata.get("task_skill_filter")
        memory = metadata.get("skill_harness_memory") or {}
        if isinstance(memory, dict):
            row["memory_reason"] = memory.get("reason")
            row["memory_task_slug"] = memory.get("task_slug")
    return row


def load_trials(job_dirs: list[Path]) -> tuple[dict[str, dict[str, Any]], list[str]]:
    by_task: dict[str, dict[str, Any]] = {}
    warnings: list[str] = []
    for path in trial_result_paths(job_dirs):
        row = trial_row(path)
        task_name = row.get("task_name")
        if not task_name:
            warnings.append(f"missing task_name: {path}")
            continue
        if task_name in by_task:
            warnings.append(
                f"duplicate task_name={task_name}: keeping {path}, replacing {by_task[task_name]['result_path']}"
            )
        by_task[task_name] = row
    return by_task, warnings


def outcome_signature(row: dict[str, Any] | None) -> str:
    if not row:
        return "missing"
    reward = row.get("reward")
    exception = row.get("exception_type")
    if reward is not None:
        return f"reward:{float(reward):.6g}" + (f":exception:{exception}" if exception else "")
    if exception:
        return f"exception:{exception}"
    return "no_reward"


def reward_zero(row: dict[str, Any] | None) -> bool:
    if not row:
        return False
    reward = row.get("reward")
    try:
        return reward is not None and float(reward) == 0.0
    except (TypeError, ValueError):
        return False


def current_error(row: dict[str, Any] | None) -> bool:
    if not row:
        return False
    return bool(row.get("exception_type")) or row.get("reward") is None


def binary_reward(row: dict[str, Any] | None) -> int | None:
    if not row:
        return None
    reward = row.get("reward")
    if reward is None:
        return None
    try:
        value = float(reward)
    except (TypeError, ValueError):
        return None
    if value >= 1.0:
        return 1
    if value == 0.0:
        return 0
    return None


def transition_case_type(previous: dict[str, Any] | None, current: dict[str, Any] | None) -> str:
    previous_reward = binary_reward(previous)
    current_reward = binary_reward(current)
    if previous_reward == 0 and current_reward == 1:
        return "positive"
    if previous_reward == 1 and current_reward == 0:
        return "strong_negative"
    if previous_reward == 0 and current_reward == 0:
        return "weak_negative"
    if previous_reward == 1 and current_reward == 1:
        return "stable_positive"
    if current_reward == 0:
        return "unpaired_current_zero"
    if current_reward == 1:
        return "unpaired_current_one"
    if current_error(current):
        return "current_error_diagnostic"
    if previous and not current:
        return "missing_current"
    if current and not previous:
        return "missing_previous"
    return "other"


def harness_case_type(
    previous: dict[str, Any] | None,
    current: dict[str, Any] | None,
    *,
    include_diagnostic_cases: bool,
) -> str | None:
    previous_reward = binary_reward(previous)
    current_reward = binary_reward(current)
    if previous_reward == 0 and current_reward == 1:
        return "positive"
    if previous_reward == 1 and current_reward == 0:
        return "strong_negative"
    if previous_reward == 0 and current_reward == 0:
        return "weak_negative"
    if include_diagnostic_cases and current_reward == 0:
        return "unpaired_current_zero"
    if include_diagnostic_cases and current_error(current):
        return "current_error_diagnostic"
    return None


def classify_transition(previous: dict[str, Any] | None, current: dict[str, Any] | None) -> str:
    prev_reward = previous.get("reward") if previous else None
    curr_reward = current.get("reward") if current else None
    if prev_reward is not None and curr_reward is not None:
        if float(curr_reward) > float(prev_reward):
            return "improved"
        if float(curr_reward) < float(prev_reward):
            return "regressed"
        if float(curr_reward) == 0.0:
            return "still_zero"
        return "same_reward"
    if previous and current and outcome_signature(previous) != outcome_signature(current):
        return "error_or_missing_changed"
    if current and current_error(current):
        return "current_error"
    return "unchanged"


def find_skill_files(skill_root: Path | None, task_name: str | None) -> list[str]:
    if skill_root is None or not skill_root.exists():
        return []
    task_slug = task_slug_from_name(task_name)
    if not task_slug:
        return []
    repo = repo_from_task_slug(task_slug)
    candidates = sorted((skill_root / repo).glob(f"{task_slug}__*"))
    if not candidates:
        candidates = sorted(skill_root.glob(f"*/{task_slug}__*"))
    files: list[Path] = []
    for candidate in candidates:
        files.extend(sorted(candidate.glob("*/**/SKILL.md")))
    return [str(path) for path in files]


def selected_case(
    task_name: str,
    base: dict[str, Any] | None,
    previous: dict[str, Any] | None,
    current: dict[str, Any] | None,
    *,
    skill_root: Path | None,
    include_diagnostic_cases: bool,
) -> dict[str, Any] | None:
    case_type = harness_case_type(
        previous,
        current,
        include_diagnostic_cases=include_diagnostic_cases,
    )
    if not case_type:
        return None
    harness_policy = HARNESS_CASE_POLICY[case_type]

    transition = classify_transition(previous, current)
    row = {
        "task_name": task_name,
        "task_slug": task_slug_from_name(task_name),
        "repo": repo_from_task_slug(task_slug_from_name(task_name)),
        "case_type": case_type,
        "case_definition": harness_policy["definition"],
        "polarity": harness_policy["polarity"],
        "policy_weight": harness_policy["policy_weight"],
        "allowed_edit_degree": harness_policy["allowed_edit_degree"],
        "max_stage_edits": harness_policy["max_stage_edits"],
        "allowed_curator_ops": harness_policy["allowed_curator_ops"],
        "reward_update_goal": harness_policy["reward_update_goal"],
        "curator_update_goal": harness_policy["curator_update_goal"],
        "transition": transition,
        "base_transition_type": transition_case_type(base, current),
        "base": {
            "reward": base.get("reward") if base else None,
            "exception_type": base.get("exception_type") if base else None,
            "trial_name": base.get("trial_name") if base else None,
            "result_path": base.get("result_path") if base else None,
        },
        "previous": {
            "reward": previous.get("reward") if previous else None,
            "exception_type": previous.get("exception_type") if previous else None,
            "trial_name": previous.get("trial_name") if previous else None,
            "result_path": previous.get("result_path") if previous else None,
        },
        "current": {
            "reward": current.get("reward") if current else None,
            "exception_type": current.get("exception_type") if current else None,
            "trial_name": current.get("trial_name") if current else None,
            "result_path": current.get("result_path") if current else None,
            "skills_count": current.get("skills_count") if current else None,
            "memory_reason": current.get("memory_reason") if current else None,
        },
        "skill_files": find_skill_files(skill_root, task_name),
    }
    return row


def build_cases(
    base_trials: dict[str, dict[str, Any]],
    previous_trials: dict[str, dict[str, Any]],
    current_trials: dict[str, dict[str, Any]],
    *,
    skill_root: Path | None,
    include_diagnostic_cases: bool,
) -> list[dict[str, Any]]:
    task_names = sorted(set(base_trials) | set(previous_trials) | set(current_trials))
    cases: list[dict[str, Any]] = []
    for task_name in task_names:
        case = selected_case(
            task_name,
            base_trials.get(task_name),
            previous_trials.get(task_name),
            current_trials.get(task_name),
            skill_root=skill_root,
            include_diagnostic_cases=include_diagnostic_cases,
        )
        if case:
            cases.append(case)
    return cases


def transition_row(
    task_name: str,
    previous: dict[str, Any] | None,
    current: dict[str, Any] | None,
    *,
    previous_label: str,
    current_label: str,
) -> dict[str, Any]:
    previous_reward = binary_reward(previous)
    current_reward = binary_reward(current)
    return {
        "task_name": task_name,
        "task_slug": task_slug_from_name(task_name),
        "repo": repo_from_task_slug(task_slug_from_name(task_name)),
        "previous_label": previous_label,
        "current_label": current_label,
        "previous_reward": previous_reward,
        "current_reward": current_reward,
        "transition_type": transition_case_type(previous, current),
        "transition": classify_transition(previous, current),
        "previous_exception": previous.get("exception_type") if previous else None,
        "current_exception": current.get("exception_type") if current else None,
        "previous_result_path": previous.get("result_path") if previous else None,
        "current_result_path": current.get("result_path") if current else None,
        "previous_trial_name": previous.get("trial_name") if previous else None,
        "current_trial_name": current.get("trial_name") if current else None,
        "current_skills_count": current.get("skills_count") if current else None,
        "current_memory_reason": current.get("memory_reason") if current else None,
    }


def build_transition_table(
    base_trials: dict[str, dict[str, Any]],
    previous_trials: dict[str, dict[str, Any]],
    current_trials: dict[str, dict[str, Any]],
    *,
    base_label: str,
    previous_label: str,
    current_label: str,
) -> list[dict[str, Any]]:
    task_names = sorted(set(base_trials) | set(previous_trials) | set(current_trials))
    rows: list[dict[str, Any]] = []
    for task_name in task_names:
        base = base_trials.get(task_name)
        previous = previous_trials.get(task_name)
        current = current_trials.get(task_name)
        row = transition_row(
            task_name,
            previous,
            current,
            previous_label=previous_label,
            current_label=current_label,
        )
        row.update(
            {
                "base_label": base_label,
                "base_reward": binary_reward(base),
                "base_exception": base.get("exception_type") if base else None,
                "base_result_path": base.get("result_path") if base else None,
                "base_trial_name": base.get("trial_name") if base else None,
                "base_to_previous_type": transition_case_type(base, previous),
                "base_to_current_type": transition_case_type(base, current),
                "base_to_previous_transition": classify_transition(base, previous),
                "base_to_current_transition": classify_transition(base, current),
            }
        )
        rows.append(row)
    return rows


def stable_positive_case(
    transition: dict[str, Any],
    *,
    skill_root: Path | None,
) -> dict[str, Any]:
    task_name = str(transition.get("task_name") or "")
    harness_policy = {
        "definition": "base, previous, and current rewards are all 1",
        "polarity": "positive",
        "policy_weight": 0.5,
        "allowed_edit_degree": "preserve_only",
        "max_stage_edits": 0,
        "allowed_curator_ops": ["preserve_stage_skill"],
        "reward_update_goal": "identify stable useful principles without editing accepted skills",
        "curator_update_goal": "avoid unnecessary edits to already stable accepted skills",
    }
    return {
        "task_name": task_name,
        "task_slug": transition.get("task_slug"),
        "repo": transition.get("repo"),
        "case_type": "stable_positive",
        "case_definition": harness_policy["definition"],
        "polarity": harness_policy["polarity"],
        "policy_weight": harness_policy["policy_weight"],
        "allowed_edit_degree": harness_policy["allowed_edit_degree"],
        "max_stage_edits": harness_policy["max_stage_edits"],
        "allowed_curator_ops": harness_policy["allowed_curator_ops"],
        "reward_update_goal": harness_policy["reward_update_goal"],
        "curator_update_goal": harness_policy["curator_update_goal"],
        "transition": "stable_positive",
        "base_transition_type": transition.get("base_to_current_type"),
        "base": {
            "reward": transition.get("base_reward"),
            "exception_type": transition.get("base_exception"),
            "trial_name": transition.get("base_trial_name"),
            "result_path": transition.get("base_result_path"),
        },
        "previous": {
            "reward": transition.get("previous_reward"),
            "exception_type": transition.get("previous_exception"),
            "trial_name": transition.get("previous_trial_name"),
            "result_path": transition.get("previous_result_path"),
        },
        "current": {
            "reward": transition.get("current_reward"),
            "exception_type": transition.get("current_exception"),
            "trial_name": transition.get("current_trial_name"),
            "result_path": transition.get("current_result_path"),
            "skills_count": transition.get("current_skills_count"),
            "memory_reason": transition.get("current_memory_reason"),
        },
        "skill_files": find_skill_files(skill_root, task_name),
        "policy_only": True,
    }


def balanced_sample_cases(
    cases: list[dict[str, Any]],
    *,
    limits: dict[str, int],
) -> list[dict[str, Any]]:
    selected: list[dict[str, Any]] = []
    for case_type in ("positive", "strong_negative", "weak_negative", "stable_positive"):
        limit = max(0, int(limits.get(case_type, 0)))
        if limit == 0:
            continue
        bucket = [case for case in cases if case.get("case_type") == case_type]
        repos = sorted({case.get("repo") or "unknown" for case in bucket})
        by_repo = {
            repo: sorted(
                [case for case in bucket if (case.get("repo") or "unknown") == repo],
                key=lambda case: case.get("task_name") or "",
            )
            for repo in repos
        }
        while len([case for case in selected if case.get("case_type") == case_type]) < limit:
            progressed = False
            for repo in repos:
                repo_cases = by_repo[repo]
                if not repo_cases:
                    continue
                selected.append(repo_cases.pop(0))
                progressed = True
                if len([case for case in selected if case.get("case_type") == case_type]) >= limit:
                    break
            if not progressed:
                break
    return sorted(
        selected,
        key=lambda case: (
            str(case.get("case_type") or ""),
            str(case.get("repo") or ""),
            str(case.get("task_name") or ""),
        ),
    )


def safe_filename(text: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9_.-]+", "_", text).strip("_")
    return slug[:180] or "case"


def read_text_prefix(path: Path, max_chars: int) -> str:
    try:
        text = path.read_text(errors="replace")
    except OSError:
        return ""
    return text[:max_chars].rstrip()


def read_text_tail(path: Path, max_chars: int) -> str:
    try:
        text = path.read_text(errors="replace")
    except OSError:
        return ""
    return text[-max_chars:].strip()


def clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def read_skill_bundle(skill_files: list[str], *, max_files: int, max_chars_per_file: int) -> list[dict[str, Any]]:
    bundle: list[dict[str, Any]] = []
    for raw_path in skill_files[:max_files]:
        path = Path(str(raw_path))
        text = read_text_prefix(path, max_chars_per_file)
        if not text:
            continue
        stage = ""
        parts = path.parts
        for item in STAGES:
            if item in parts:
                stage = item
                break
        bundle.append(
            {
                "path": str(path),
                "stage": stage,
                "text": text,
                "chars": len(text),
            }
        )
    return bundle


def text_feature_summary(text: str) -> dict[str, Any]:
    lowered = text.lower()
    source_paths = sorted(set(SOURCE_PATH_RE.findall(text)))
    test_commands = sorted(
        set(match.group(0).strip() for match in TEST_COMMAND_RE.finditer(text))
    )
    generic_hits = [
        phrase for phrase in GENERIC_SKILL_PHRASES if phrase in lowered
    ]
    private_path_hits = []
    for pattern in PRIVATE_PATH_PATTERNS:
        if re.search(pattern, text):
            private_path_hits.append(pattern)
    stage_mentions = [stage for stage in STAGES if stage in lowered]
    return {
        "source_paths": source_paths[:20],
        "test_commands": test_commands[:12],
        "generic_hits": generic_hits,
        "private_path_hits": private_path_hits,
        "stage_mentions": stage_mentions,
        "n_source_paths": len(source_paths),
        "n_test_commands": len(test_commands),
        "n_generic_hits": len(generic_hits),
        "n_private_path_hits": len(private_path_hits),
    }


def skill_evidence_features(case: dict[str, Any]) -> dict[str, Any]:
    skill_bundle = read_skill_bundle(
        [str(path) for path in (case.get("skill_files") or [])],
        max_files=12,
        max_chars_per_file=2400,
    )
    combined = "\n\n".join(item["text"] for item in skill_bundle)
    features = text_feature_summary(combined)
    current_result = ((case.get("current") or {}).get("result_path"))
    problem_text = ""
    trial_tail = ""
    stderr_tail = ""
    if current_result:
        trial_dir = Path(str(current_result)).parent
        problem_text = read_text_prefix(trial_dir / "agent" / "problem_statement.md", 2200)
        trial_tail = read_text_tail(trial_dir / "trial.log", 2600)
        stderr_tail = read_text_tail(trial_dir / "agent" / "pi-stderr.txt", 1800)
    trace_text = "\n".join([problem_text, trial_tail, stderr_tail])
    trace_features = text_feature_summary(trace_text)
    skill_stages = sorted({item["stage"] for item in skill_bundle if item.get("stage")})
    return {
        "skill_files_seen": len(skill_bundle),
        "skill_stages": skill_stages,
        "skill_text_features": features,
        "trace_features": trace_features,
        "problem_excerpt": problem_text[:900],
        "trial_tail_excerpt": trial_tail[:900],
        "stderr_tail_excerpt": stderr_tail[:600],
    }


def label_free_reward_judgment(case: dict[str, Any], features: dict[str, Any]) -> dict[str, Any]:
    skill_text = features.get("skill_text_features") or {}
    trace_features = features.get("trace_features") or {}
    skill_files_seen = int(features.get("skill_files_seen") or 0)
    n_source_paths = int(skill_text.get("n_source_paths") or 0)
    n_test_commands = int(skill_text.get("n_test_commands") or 0)
    n_generic_hits = int(skill_text.get("n_generic_hits") or 0)
    n_private_path_hits = int(skill_text.get("n_private_path_hits") or 0)
    trace_source_paths = int(trace_features.get("n_source_paths") or 0)
    trace_test_commands = int(trace_features.get("n_test_commands") or 0)

    evidence_score = 0.0
    evidence_score += min(n_source_paths, 4) * 0.12
    evidence_score += min(n_test_commands, 2) * 0.16
    evidence_score += min(skill_files_seen, 5) * 0.04
    if trace_source_paths and n_source_paths:
        evidence_score += 0.12
    if trace_test_commands and n_test_commands:
        evidence_score += 0.12

    risk_score = 0.0
    risk_score += min(n_generic_hits, 5) * 0.12
    risk_score += min(n_private_path_hits, 3) * 0.18
    if skill_files_seen == 0:
        risk_score += 0.35
    if n_source_paths == 0:
        risk_score += 0.22
    if n_test_commands == 0:
        risk_score += 0.12

    predicted_delta = clamp(evidence_score - risk_score, -1.0, 1.0)
    confidence = clamp(0.42 + abs(predicted_delta) * 0.38 + min(skill_files_seen, 5) * 0.025, 0.05, 0.95)
    if predicted_delta >= 0.28 and confidence >= 0.62:
        decision = "accept"
    elif predicted_delta <= -0.22 and confidence >= 0.55:
        decision = "reject"
    elif abs(predicted_delta) <= 0.18:
        decision = "abstain"
    else:
        decision = "revise"

    stage_scores = {
        "reproduce": clamp(0.15 * min(n_test_commands, 2) - 0.08 * n_generic_hits, -1.0, 1.0),
        "localize": clamp(0.12 * min(n_source_paths, 4) - 0.10 * n_generic_hits - 0.12 * n_private_path_hits, -1.0, 1.0),
        "edit": clamp(0.08 * min(n_source_paths, 3) - 0.12 * (1 if n_source_paths == 0 else 0), -1.0, 1.0),
        "validate": clamp(0.18 * min(n_test_commands, 2) - 0.10 * (1 if n_test_commands == 0 else 0), -1.0, 1.0),
        "recover": clamp(0.10 * (1 if n_generic_hits else 0) - 0.08 * n_private_path_hits, -1.0, 1.0),
    }
    risks: list[str] = []
    if n_generic_hits:
        risks.append("generic_skill_language")
    if n_private_path_hits:
        risks.append("private_or_sandbox_path_leakage")
    if n_source_paths == 0:
        risks.append("missing_concrete_owner_paths")
    if n_test_commands == 0:
        risks.append("missing_focused_validation")
    if skill_files_seen == 0:
        risks.append("missing_skill_files")
    useful: list[str] = []
    if n_source_paths:
        useful.append("names_concrete_paths")
    if n_test_commands:
        useful.append("names_validation_commands")
    if trace_source_paths:
        useful.append("trace_contains_path_evidence")
    return {
        "mode": "label_free",
        "decision": decision,
        "predicted_delta": round(predicted_delta, 3),
        "confidence": round(confidence, 3),
        "stage_scores": {key: round(value, 3) for key, value in stage_scores.items()},
        "risk_signals": risks,
        "useful_signals": useful,
        "risk": "; ".join(risks) if risks else "no_major_static_risk_detected",
        "curator_feedback": label_free_curator_feedback(risks, useful),
    }


def label_free_curator_feedback(risks: list[str], useful: list[str]) -> str:
    if "missing_concrete_owner_paths" in risks:
        return "Require concrete owner files/functions before accepting localize or edit guidance."
    if "private_or_sandbox_path_leakage" in risks:
        return "Remove sandbox/private absolute paths and replace them with repository-relative evidence."
    if "missing_focused_validation" in risks:
        return "Add one focused reproduction or validation command before preserving the skill."
    if useful:
        return "The skill has concrete evidence hooks; accept only if the candidate edit preserves them without broadening behavior."
    return "Abstain unless the skill edit narrows search, edit ownership, or validation."


def label_known_reward_calibration(
    case: dict[str, Any],
    label_free: dict[str, Any],
    features: dict[str, Any],
) -> dict[str, Any]:
    case_type = str(case.get("case_type") or "")
    decision = str(label_free.get("decision") or "abstain")
    previous_reward = binary_reward(case.get("previous"))
    current_reward = binary_reward(case.get("current"))
    if case_type == "positive":
        actual_delta = 1
        actual_outcome = "improved"
    elif case_type == "strong_negative":
        actual_delta = -1
        actual_outcome = "regressed"
    elif case_type == "weak_negative":
        actual_delta = 0
        actual_outcome = "still_zero"
    elif case_type == "stable_positive":
        actual_delta = 0
        actual_outcome = "stable_positive"
    else:
        actual_delta = None
        actual_outcome = "diagnostic"

    if case_type == "strong_negative" and decision in {"accept", "revise"}:
        error_type = "false_accept"
    elif case_type == "positive" and decision in {"reject", "abstain"}:
        error_type = "false_reject"
    elif case_type == "weak_negative" and decision == "accept":
        error_type = "weak_signal_accept"
    elif case_type == "stable_positive" and decision == "reject":
        error_type = "over_reject_stable_positive"
    elif decision == "abstain" and case_type in {"positive", "strong_negative"}:
        error_type = "overconfident_abstain" if float(label_free.get("confidence") or 0) >= 0.65 else "under_informed_abstain"
    else:
        error_type = "calibrated"

    skill_text = features.get("skill_text_features") or {}
    risk_signals = list(label_free.get("risk_signals") or [])
    bad_signal = "none"
    if "generic_skill_language" in risk_signals:
        bad_signal = "generic workflow wording looked actionable but did not provide enough task ownership evidence"
    elif "missing_concrete_owner_paths" in risk_signals:
        bad_signal = "skill lacked concrete owner paths or functions"
    elif "missing_focused_validation" in risk_signals:
        bad_signal = "skill lacked a focused validation command"
    elif "private_or_sandbox_path_leakage" in risk_signals:
        bad_signal = "skill included sandbox/private paths that can distract future rollouts"

    new_rule = reward_policy_lesson_for_case(case_type, error_type, risk_signals)
    curator_feedback = curator_feedback_for_case(
        case,
        error_type=error_type,
        risk_signals=risk_signals,
        skill_features=skill_text,
    )
    return {
        "mode": "label_known",
        "actual_transition": case_type,
        "actual_outcome": actual_outcome,
        "actual_delta": actual_delta,
        "reward_before": previous_reward,
        "reward_after": current_reward,
        "reward_agent_error": error_type,
        "bad_signal": bad_signal,
        "new_rule": new_rule,
        "curator_feedback": curator_feedback,
    }


def reward_policy_lesson_for_case(
    case_type: str,
    error_type: str,
    risk_signals: list[str],
) -> str:
    if error_type == "false_accept":
        if "missing_concrete_owner_paths" in risk_signals:
            return "Reject or revise localize/edit skill edits that do not name concrete repository-relative owner paths or functions."
        if "generic_skill_language" in risk_signals:
            return "Do not accept generic workflow improvements as useful skill evidence; require task-specific reproduction, ownership, or validation details."
        return "For 1->0 regressions, make reward judgment conservative unless the candidate clearly preserves the previous successful path."
    if error_type == "false_reject":
        return "When a 0->1 case contains concrete files, tests, or patch-shape evidence, preserve that evidence even if surrounding wording is imperfect."
    if error_type == "weak_signal_accept":
        return "For 0->0 cases, prefer revise or abstain over accept; require a narrow new evidence hook before allowing curation."
    if error_type == "over_reject_stable_positive":
        return "Stable 1->1 cases should act as preservation anchors unless the skill contains clear leakage or broad harmful instructions."
    if error_type in {"overconfident_abstain", "under_informed_abstain"}:
        return "Abstention should include the missing evidence that would change the decision, especially owner path, reproduction, or validation."
    return "Keep the existing calibration for cases where static risk and verifier transition agree."


def curator_feedback_for_case(
    case: dict[str, Any],
    *,
    error_type: str,
    risk_signals: list[str],
    skill_features: dict[str, Any],
) -> dict[str, Any]:
    case_type = str(case.get("case_type") or "")
    source_paths = list(skill_features.get("source_paths") or [])[:8]
    test_commands = list(skill_features.get("test_commands") or [])[:4]
    max_stage_edits = int(case.get("max_stage_edits") or 0)
    if case_type == "positive":
        target_stages = ["validate"] if test_commands else ["localize"]
        rewrite_goal = "preserve and compress the concrete success path without adding broad behavior"
        skill_action = "promote_evidence_hooks"
    elif case_type == "strong_negative":
        target_stages = ["localize", "edit", "recover"][: max(1, min(max_stage_edits, 3))]
        rewrite_goal = "disable or sharply narrow guidance that can distract a previously solved task"
        skill_action = "demote_or_disable_harmful_guidance"
    elif case_type == "weak_negative":
        target_stages = ["localize", "validate"][: max(1, min(max_stage_edits, 2))]
        rewrite_goal = "add one concrete evidence hook and an early stop condition instead of broad search"
        skill_action = "keep_memory_only_until_evidence_added"
    elif case_type == "stable_positive":
        target_stages = []
        rewrite_goal = "preserve as a policy anchor; do not edit accepted skills"
        skill_action = "preserve"
    else:
        target_stages = ["recover"]
        rewrite_goal = "record diagnostic safeguards only"
        skill_action = "diagnostic_memory_only"

    must_remove: list[str] = []
    if "generic_skill_language" in risk_signals:
        must_remove.append("generic workflow wording that does not change task behavior")
    if "private_or_sandbox_path_leakage" in risk_signals:
        must_remove.append("sandbox/private absolute paths")
    if "missing_concrete_owner_paths" in risk_signals:
        must_remove.append("claims of localization without repository-relative owner evidence")
    must_preserve: list[str] = []
    if source_paths:
        must_preserve.append("repository-relative paths: " + ", ".join(source_paths[:4]))
    if test_commands:
        must_preserve.append("focused validation commands: " + "; ".join(test_commands[:2]))
    return {
        "allowed_edit_degree": case.get("allowed_edit_degree"),
        "max_stage_edits": max_stage_edits,
        "target_stages": target_stages,
        "must_preserve": must_preserve,
        "must_remove": must_remove,
        "rewrite_goal": rewrite_goal,
        "reward_agent_error": error_type,
        "skill_action": skill_action,
        "activation_policy": (
            "active_evidence_gated"
            if case_type in {"positive", "stable_positive"}
            else "inactive_memory_only"
            if case_type in {"strong_negative", "weak_negative"}
            else "diagnostic_memory_only"
        ),
    }


def build_reward_case(case: dict[str, Any]) -> dict[str, Any]:
    features = skill_evidence_features(case)
    label_free = label_free_reward_judgment(case, features)
    label_known = label_known_reward_calibration(case, label_free, features)
    return {
        "schema_version": 1,
        "task_name": case.get("task_name"),
        "task_slug": case.get("task_slug"),
        "repo": case.get("repo"),
        "case_type": case.get("case_type"),
        "transition": case.get("transition"),
        "allowed_edit_degree": case.get("allowed_edit_degree"),
        "max_stage_edits": case.get("max_stage_edits"),
        "skill_files": case.get("skill_files") or [],
        "trace_digest_path": case.get("trace_digest_path"),
        "label_free_judgment": label_free,
        "label_known_critique": label_known,
        "curator_feedback": label_known.get("curator_feedback"),
        "policy_lesson": label_known.get("new_rule"),
        "evidence_features": {
            key: value
            for key, value in features.items()
            if key not in {"problem_excerpt", "trial_tail_excerpt", "stderr_tail_excerpt"}
        },
        "evidence_excerpt": {
            "problem": features.get("problem_excerpt"),
            "trial_tail": features.get("trial_tail_excerpt"),
            "stderr_tail": features.get("stderr_tail_excerpt"),
        },
    }


def build_reward_cases(policy_cases: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [build_reward_case(case) for case in policy_cases]


def aggregate_reward_cases(reward_cases: list[dict[str, Any]]) -> dict[str, Any]:
    decisions = Counter(
        ((case.get("label_free_judgment") or {}).get("decision") or "unknown")
        for case in reward_cases
    )
    errors = Counter(
        ((case.get("label_known_critique") or {}).get("reward_agent_error") or "unknown")
        for case in reward_cases
    )
    case_types = Counter(case.get("case_type") or "unknown" for case in reward_cases)
    risk_signals: Counter[str] = Counter()
    target_stages: Counter[str] = Counter()
    policy_lessons: Counter[str] = Counter()
    for case in reward_cases:
        judgment = case.get("label_free_judgment") or {}
        for signal in judgment.get("risk_signals") or []:
            risk_signals[str(signal)] += 1
        feedback = case.get("curator_feedback") or {}
        for stage in feedback.get("target_stages") or []:
            target_stages[str(stage)] += 1
        lesson = str(case.get("policy_lesson") or "").strip()
        if lesson:
            policy_lessons[lesson] += 1
    return {
        "n_reward_cases": len(reward_cases),
        "label_free_decisions": dict(decisions),
        "label_known_errors": dict(errors),
        "case_types": dict(case_types),
        "risk_signals": dict(risk_signals),
        "target_stages": dict(target_stages),
        "top_policy_lessons": [
            {"lesson": lesson, "count": count}
            for lesson, count in policy_lessons.most_common(8)
        ],
    }


def selected_skill_summary(result_path: Path) -> list[str]:
    index_path = result_path.parent / "agent" / "pi-skills-index.json"
    if not index_path.exists():
        return []
    try:
        data = json.loads(index_path.read_text(errors="replace"))
    except (OSError, ValueError):
        return []
    if isinstance(data, dict):
        skills = data.get("selected_skills") or data.get("skills") or data.get("items") or []
    elif isinstance(data, list):
        skills = data
    else:
        skills = []
    rows: list[str] = []
    for item in skills[:12]:
        if isinstance(item, dict):
            name = item.get("name") or item.get("skill_name") or item.get("path") or "skill"
            stage = item.get("stage") or item.get("category") or ""
            rows.append(f"- {stage}: {name}".strip())
        else:
            rows.append(f"- {item}")
    return rows


def write_trace_digest(case: dict[str, Any], out_dir: Path) -> str | None:
    current_result = ((case.get("current") or {}).get("result_path"))
    if not current_result:
        return None
    result_path = Path(str(current_result))
    trial_dir = result_path.parent
    previous = case.get("previous") or {}
    current = case.get("current") or {}
    digest_dir = out_dir / "trace_digests"
    digest_dir.mkdir(parents=True, exist_ok=True)
    digest_path = digest_dir / (
        safe_filename(f"{case.get('case_type')}_{case.get('task_slug') or case.get('task_name')}")
        + ".md"
    )
    problem = read_text_prefix(trial_dir / "agent" / "problem_statement.md", 2500)
    trial_tail = read_text_tail(trial_dir / "trial.log", 3500)
    stderr_tail = read_text_tail(trial_dir / "agent" / "pi-stderr.txt", 2000)
    skill_rows = selected_skill_summary(result_path)
    lines = [
        f"# Trace Digest: {case.get('task_name')}",
        "",
        f"- Case type: `{case.get('case_type')}`",
        f"- Transition: `{case.get('transition')}`",
        f"- Edit degree: `{case.get('allowed_edit_degree')}`",
        f"- Max stage edits: `{case.get('max_stage_edits')}`",
        f"- Previous reward: `{previous.get('reward')}`",
        f"- Current reward: `{current.get('reward')}`",
        f"- Previous exception: `{previous.get('exception_type')}`",
        f"- Current exception: `{current.get('exception_type')}`",
        f"- Previous result: `{previous.get('result_path')}`",
        f"- Current result: `{current.get('result_path')}`",
        "",
        "## Problem Statement",
        "",
        problem or "_missing_",
        "",
        "## Selected Skills",
        "",
        "\n".join(skill_rows) if skill_rows else "_missing_",
        "",
        "## Trial Log Tail",
        "",
        "```text",
        trial_tail or "_missing_",
        "```",
        "",
        "## Agent Stderr Tail",
        "",
        "```text",
        stderr_tail or "_missing_",
        "```",
        "",
        "## Reward-Blind Critique Prompt",
        "",
        "Assess whether the skill guidance was evidence-grounded, narrow, and verifiable without using the verifier label.",
        "",
        "## Reward-Known Critique Prompt",
        "",
        "Use the verifier transition to decide whether to preserve, narrow, rewrite, or disable the relevant stage guidance.",
        "",
    ]
    digest_path.write_text("\n".join(lines))
    return str(digest_path)


def aggregate_cases(cases: list[dict[str, Any]]) -> dict[str, Any]:
    transitions = Counter(case["transition"] for case in cases)
    case_types = Counter(case.get("case_type") or "unknown" for case in cases)
    allowed_edit_degrees = Counter(
        case.get("allowed_edit_degree") or "unknown" for case in cases
    )
    repos = Counter(case.get("repo") or "unknown" for case in cases)
    current_exceptions = Counter(
        (case.get("current") or {}).get("exception_type") or "none" for case in cases
    )
    previous_exceptions = Counter(
        (case.get("previous") or {}).get("exception_type") or "none" for case in cases
    )
    skill_counts = [
        int((case.get("current") or {}).get("skills_count") or 0)
        for case in cases
        if (case.get("current") or {}).get("skills_count") is not None
    ]
    deltas: list[float] = []
    for case in cases:
        prev_reward = (case.get("previous") or {}).get("reward")
        curr_reward = (case.get("current") or {}).get("reward")
        if prev_reward is not None and curr_reward is not None:
            deltas.append(float(curr_reward) - float(prev_reward))
    return {
        "n_selected_cases": len(cases),
        "transitions": dict(transitions),
        "case_types": dict(case_types),
        "allowed_edit_degrees": dict(allowed_edit_degrees),
        "fixed_harness_case_policy": HARNESS_CASE_POLICY,
        "top_repos": dict(repos.most_common(20)),
        "current_exceptions": dict(current_exceptions),
        "previous_exceptions": dict(previous_exceptions),
        "mean_selected_delta": statistics.fmean(deltas) if deltas else None,
        "skills_count_distribution": dict(Counter(skill_counts)),
    }


def aggregate_transition_table(rows: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "n_transition_rows": len(rows),
        "previous_to_current_types": dict(Counter(row.get("transition_type") or "unknown" for row in rows)),
        "base_to_previous_types": dict(Counter(row.get("base_to_previous_type") or "unknown" for row in rows)),
        "base_to_current_types": dict(Counter(row.get("base_to_current_type") or "unknown" for row in rows)),
        "previous_rewards": dict(Counter(str(row.get("previous_reward")) for row in rows)),
        "current_rewards": dict(Counter(str(row.get("current_reward")) for row in rows)),
        "base_rewards": dict(Counter(str(row.get("base_reward")) for row in rows)),
        "current_exceptions_all": dict(Counter(row.get("current_exception") or "none" for row in rows)),
    }


def policy_update_blocks(
    *,
    iteration_id: str,
    previous_label: str,
    current_label: str,
    aggregate: dict[str, Any],
) -> tuple[str, str]:
    transitions = aggregate.get("transitions") or {}
    case_types = aggregate.get("case_types") or {}
    allowed_edit_degrees = aggregate.get("allowed_edit_degrees") or {}
    transition_stats = aggregate.get("transition_table") or {}
    current_exceptions = aggregate.get("current_exceptions") or {}
    reward_agent = aggregate.get("reward_agent") or {}
    reward_decisions = reward_agent.get("label_free_decisions") or {}
    reward_errors = reward_agent.get("label_known_errors") or {}
    reward_risks = reward_agent.get("risk_signals") or {}
    reward_target_stages = reward_agent.get("target_stages") or {}
    reward_lessons = reward_agent.get("top_policy_lessons") or []
    reward_lesson_lines = "\n".join(
        f"- {item.get('lesson')} (`{item.get('count')}` cases)"
        for item in reward_lessons[:6]
        if isinstance(item, dict) and item.get("lesson")
    ) or "- No reward-agent lesson was generated."
    strong_negative = int(case_types.get("strong_negative", 0))
    weak_negative = int(case_types.get("weak_negative", 0))
    positive = int(case_types.get("positive", 0))
    stable_positive = int(case_types.get("stable_positive", 0))
    reward_block = f"""## TextGrad Update {iteration_id}: Fixed-Harness Reward Calibration

Source: compare `{current_label}` against `{previous_label}` using the fixed top-level harness labels. These labels are not policy parameters and must not drift across iterations:

- positive: previous reward `0` -> current reward `1`; preserve/distill only.
- strong_negative: previous reward `1` -> current reward `0`; major delete or rewrite is allowed.
- weak_negative: previous reward `0` -> current reward `0`; only bounded targeted rewrite is allowed.

Observed selected cases:

- selected_cases: `{aggregate.get("n_selected_cases")}`
- case_types: `{json.dumps(case_types, ensure_ascii=False, sort_keys=True)}`
- allowed_edit_degrees: `{json.dumps(allowed_edit_degrees, ensure_ascii=False, sort_keys=True)}`
- transitions: `{json.dumps(transitions, ensure_ascii=False, sort_keys=True)}`
- base_to_current_types: `{json.dumps(transition_stats.get("base_to_current_types") or {}, ensure_ascii=False, sort_keys=True)}`
- previous_to_current_types: `{json.dumps(transition_stats.get("previous_to_current_types") or {}, ensure_ascii=False, sort_keys=True)}`
- current_exceptions: `{json.dumps(current_exceptions, ensure_ascii=False, sort_keys=True)}`
- mean_selected_delta: `{aggregate.get("mean_selected_delta")}`
- reward_agent_cases: `{reward_agent.get("n_reward_cases")}`
- reward_agent_label_free_decisions: `{json.dumps(reward_decisions, ensure_ascii=False, sort_keys=True)}`
- reward_agent_label_known_errors: `{json.dumps(reward_errors, ensure_ascii=False, sort_keys=True)}`
- reward_agent_risk_signals: `{json.dumps(reward_risks, ensure_ascii=False, sort_keys=True)}`

Policy update:

- Strong negatives (`{strong_negative}` cases) are false accepts unless the current failure is clearly infrastructure-only. Reward should become more conservative on the label-free signals that looked good but produced 1->0.
- Weak negatives (`{weak_negative}` cases) are failed attempts, not catastrophic regressions. Reward should prefer `revise`/`abstain` and ask for small targeted evidence, not broad deletion.
- Positives (`{positive}` cases) are true useful skills. Reward should learn which label-free evidence predicted 0->1 and keep those signals without turning them into generic acceptance rules.
- Stable positives (`{stable_positive}` policy-only cases) are anchors for preserving already-good behavior. Use them to avoid policy drift, not to create new task edits.
- Runtime/setup errors are diagnostic risk signals. Penalize skills that increase timeout or setup fragility, but do not let infrastructure noise override the fixed positive/negative labels.
- Always compare current behavior against both the previous skill version and the no-skill/base reference. A current improvement over previous is not necessarily a good policy signal if it is still worse than base.
- The reward agent may recommend accept/revise/reject, but the allowed edit degree is fixed by the harness label above.
- `textgrad_feedback_for_curator` must name the case type, risky or useful stage, and the fixed edit budget to apply.

Reward-agent calibration lessons:

{reward_lesson_lines}
"""
    curator_block = f"""## TextGrad Update {iteration_id}: Fixed-Harness Curation Update

Source: compare `{current_label}` against `{previous_label}` and rewrite only according to the fixed top-level harness labels:

- positive: previous reward `0` -> current reward `1`; preserve or distill with at most one stage edit.
- strong_negative: previous reward `1` -> current reward `0`; delete, disable, or major rewrite up to five stage edits.
- weak_negative: previous reward `0` -> current reward `0`; bounded targeted rewrite up to two stage edits.

Observed selected cases:

- selected_cases: `{aggregate.get("n_selected_cases")}`
- case_types: `{json.dumps(case_types, ensure_ascii=False, sort_keys=True)}`
- allowed_edit_degrees: `{json.dumps(allowed_edit_degrees, ensure_ascii=False, sort_keys=True)}`
- transitions: `{json.dumps(transitions, ensure_ascii=False, sort_keys=True)}`
- base_to_current_types: `{json.dumps(transition_stats.get("base_to_current_types") or {}, ensure_ascii=False, sort_keys=True)}`
- previous_to_current_types: `{json.dumps(transition_stats.get("previous_to_current_types") or {}, ensure_ascii=False, sort_keys=True)}`
- top_repos: `{json.dumps(aggregate.get("top_repos") or {}, ensure_ascii=False, sort_keys=True)}`
- current_exceptions: `{json.dumps(current_exceptions, ensure_ascii=False, sort_keys=True)}`
- reward_agent_target_stages: `{json.dumps(reward_target_stages, ensure_ascii=False, sort_keys=True)}`
- reward_agent_risk_signals: `{json.dumps(reward_risks, ensure_ascii=False, sort_keys=True)}`

Policy update:

- For positive cases, preserve the helpful stage behavior and distill it shorter. Do not generalize it into unrelated tasks or add new broad actions.
- For strong negatives, assume the skill harmed a previously solved task. Delete, disable, or rewrite the likely harmful stage first; generic localize/edit advice is the first suspect.
- For weak negatives, make a small targeted rewrite from the failed behavior. Prefer recover/validate stop conditions and narrower reproduction over broad new search plans.
- Stable positives are policy-only anchors. They should mostly prevent unnecessary edits.
- Before editing an accepted skill, inspect whether current is better than both previous and base. If current is worse than base, prefer narrowing or disabling over preserving.
- Editing strength is fixed by the harness case type and cannot be changed by curator policy text.
- Timeout-sensitive edits must reduce search breadth and add early stop conditions.
- Environment/setup failures should only affect recover/validate safeguards unless source-code evidence says otherwise.
- Use each reward case's `curator_feedback` as the per-task rewrite contract: obey `target_stages`, preserve concrete paths/tests, and remove the listed risky signals before adding new prose.
"""
    return reward_block.rstrip() + "\n", curator_block.rstrip() + "\n"


def replace_or_append_block(text: str, *, block_id: str, block: str) -> str:
    start = f"<!-- skill-evo-policy-update:{block_id}:start -->"
    end = f"<!-- skill-evo-policy-update:{block_id}:end -->"
    wrapped = f"{start}\n{block.rstrip()}\n{end}\n"
    pattern = re.compile(
        re.escape(start) + r".*?" + re.escape(end) + r"\n?",
        flags=re.DOTALL,
    )
    if pattern.search(text):
        return pattern.sub(wrapped, text)
    return text.rstrip() + "\n\n" + wrapped


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")


def write_markdown_report(
    path: Path,
    *,
    iteration_id: str,
    previous_label: str,
    current_label: str,
    aggregate: dict[str, Any],
    cases: list[dict[str, Any]],
    policy_cases: list[dict[str, Any]],
    reward_cases: list[dict[str, Any]],
    reward_block: str,
    curator_block: str,
) -> None:
    lines = [
        f"# Policy Update Report {iteration_id}",
        "",
        f"- Created: `{utc_now()}`",
        f"- Previous round: `{previous_label}`",
        f"- Current round: `{current_label}`",
        f"- Fixed harness policy: `{HARNESS_POLICY_PATH}`",
        f"- Selected cases: `{aggregate.get('n_selected_cases')}`",
        f"- Policy sample cases: `{len(policy_cases)}`",
        f"- Mean selected delta: `{aggregate.get('mean_selected_delta')}`",
        "",
        "## Aggregate",
        "",
        "```json",
        json.dumps(aggregate, ensure_ascii=False, indent=2, sort_keys=True),
        "```",
        "",
        "## Policy Case Preview",
        "",
        "| Task | Case Type | Base | Previous | Current | Edit Degree | Digest |",
        "| --- | --- | ---: | ---: | ---: | --- | --- |",
    ]
    for case in policy_cases[:80]:
        base = case.get("base") or {}
        previous = case.get("previous") or {}
        current = case.get("current") or {}
        lines.append(
            "| {task} | {transition} | {base} | {prev} | {curr} | {degree} | {digest} |".format(
                task=case.get("task_name") or "",
                transition=case.get("case_type") or "",
                base=base.get("reward"),
                prev=previous.get("reward"),
                curr=current.get("reward"),
                degree=case.get("allowed_edit_degree") or "",
                digest=case.get("trace_digest_path") or "",
            )
        )
    lines.extend(
        [
            "",
            "## Reward Agent Case Memory",
            "",
            "| Task | Case Type | Label-Free Decision | Error | Target Stages | Lesson |",
            "| --- | --- | --- | --- | --- | --- |",
        ]
    )
    for reward_case in reward_cases[:80]:
        judgment = reward_case.get("label_free_judgment") or {}
        critique = reward_case.get("label_known_critique") or {}
        feedback = reward_case.get("curator_feedback") or {}
        target_stages = ", ".join(str(stage) for stage in (feedback.get("target_stages") or []))
        lesson = str(reward_case.get("policy_lesson") or "").replace("|", "\\|")
        lines.append(
            "| {task} | {case_type} | {decision} ({confidence}) | {error} | {stages} | {lesson} |".format(
                task=reward_case.get("task_name") or "",
                case_type=reward_case.get("case_type") or "",
                decision=judgment.get("decision") or "",
                confidence=judgment.get("confidence"),
                error=critique.get("reward_agent_error") or "",
                stages=target_stages,
                lesson=lesson,
            )
        )
    lines.extend(
        [
            "",
            "## Reward Policy TextGrad Block",
            "",
            reward_block.rstrip(),
            "",
            "## Curator Policy TextGrad Block",
            "",
            curator_block.rstrip(),
            "",
        ]
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Update curator/reward text policies from fixed harness labels: 0->1 positive, 1->0 strong negative, 0->0 weak negative."
        )
    )
    parser.add_argument("--iteration-id", required=True)
    parser.add_argument("--base-label", default="base")
    parser.add_argument("--previous-label", default="previous")
    parser.add_argument("--current-label", default="current")
    parser.add_argument("--base-job-dir", action="append", default=[])
    parser.add_argument("--previous-job-dir", action="append", default=[])
    parser.add_argument("--current-job-dir", action="append", default=[])
    parser.add_argument("--base-job-glob", action="append", default=[])
    parser.add_argument("--previous-job-glob", action="append", default=[])
    parser.add_argument("--current-job-glob", action="append", default=[])
    parser.add_argument("--current-skill-root", type=Path, default=None)
    parser.add_argument("--current-skill-version", default=None)
    parser.add_argument("--policy-root", type=Path, default=DEFAULT_POLICY_ROOT)
    parser.add_argument("--out-dir", type=Path, default=None)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--policy-positive-limit", type=int, default=DEFAULT_POLICY_SAMPLE_LIMITS["positive"])
    parser.add_argument("--policy-strong-negative-limit", type=int, default=DEFAULT_POLICY_SAMPLE_LIMITS["strong_negative"])
    parser.add_argument("--policy-weak-negative-limit", type=int, default=DEFAULT_POLICY_SAMPLE_LIMITS["weak_negative"])
    parser.add_argument("--policy-stable-positive-limit", type=int, default=DEFAULT_POLICY_SAMPLE_LIMITS["stable_positive"])
    parser.add_argument("--expected-current-trials", type=int, default=0)
    parser.add_argument("--require-complete-current", action="store_true")
    parser.add_argument(
        "--include-diagnostic-cases",
        action="store_true",
        help="Also include unpaired current reward-zero and current error/missing-reward diagnostics. Core policy updates should usually omit this.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    base_job_dirs = expand_paths(args.base_job_dir, args.base_job_glob)
    previous_job_dirs = expand_paths(args.previous_job_dir, args.previous_job_glob)
    current_job_dirs = expand_paths(args.current_job_dir, args.current_job_glob)
    if not previous_job_dirs:
        raise SystemExit("No previous jobs provided.")
    if not current_job_dirs:
        raise SystemExit("No current jobs provided.")
    if not base_job_dirs:
        base_job_dirs = previous_job_dirs

    skill_root = args.current_skill_root
    if skill_root is None and args.current_skill_version:
        skill_root = DEFAULT_SKILL_ROOT / args.current_skill_version
    if skill_root is not None:
        skill_root = skill_root.expanduser().resolve()

    out_dir = args.out_dir
    if out_dir is None:
        safe_id = re.sub(r"[^A-Za-z0-9_.-]+", "_", args.iteration_id).strip("_")
        out_dir = ROOT / "run_logs" / "policy_updates" / safe_id
    out_dir = out_dir.expanduser().resolve()

    base_trials, base_warnings = load_trials(base_job_dirs)
    previous_trials, previous_warnings = load_trials(previous_job_dirs)
    current_trials, current_warnings = load_trials(current_job_dirs)
    if args.expected_current_trials and len(current_trials) < args.expected_current_trials:
        message = (
            f"Current jobs have {len(current_trials)} trial results, expected {args.expected_current_trials}."
        )
        if args.require_complete_current:
            raise SystemExit(message)
        current_warnings.append(message)
    cases = build_cases(
        base_trials,
        previous_trials,
        current_trials,
        skill_root=skill_root,
        include_diagnostic_cases=args.include_diagnostic_cases,
    )
    transition_table = build_transition_table(
        base_trials,
        previous_trials,
        current_trials,
        base_label=args.base_label,
        previous_label=args.previous_label,
        current_label=args.current_label,
    )
    stable_positive_cases = [
        stable_positive_case(row, skill_root=skill_root)
        for row in transition_table
        if row.get("base_reward") == 1
        and row.get("previous_reward") == 1
        and row.get("current_reward") == 1
    ]
    policy_cases = balanced_sample_cases(
        cases + stable_positive_cases,
        limits={
            "positive": args.policy_positive_limit,
            "strong_negative": args.policy_strong_negative_limit,
            "weak_negative": args.policy_weak_negative_limit,
            "stable_positive": args.policy_stable_positive_limit,
        },
    )
    for case in policy_cases:
        digest_path = write_trace_digest(case, out_dir)
        if digest_path:
            case["trace_digest_path"] = digest_path
    reward_cases = build_reward_cases(cases)
    reward_agent_summary = aggregate_reward_cases(reward_cases)

    aggregate = aggregate_cases(cases)
    aggregate.update(
        {
            "iteration_id": args.iteration_id,
            "created_at": utc_now(),
            "harness_policy_path": str(HARNESS_POLICY_PATH),
            "base_label": args.base_label,
            "previous_label": args.previous_label,
            "current_label": args.current_label,
            "base_jobs": [str(path) for path in base_job_dirs],
            "previous_jobs": [str(path) for path in previous_job_dirs],
            "current_jobs": [str(path) for path in current_job_dirs],
            "current_skill_root": str(skill_root) if skill_root else None,
            "base_trial_count": len(base_trials),
            "previous_trial_count": len(previous_trials),
            "current_trial_count": len(current_trials),
            "policy_sample_cases": len(policy_cases),
            "policy_sample_case_types": dict(Counter(case.get("case_type") or "unknown" for case in policy_cases)),
            "reward_agent": reward_agent_summary,
            "transition_table": aggregate_transition_table(transition_table),
            "warnings": base_warnings + previous_warnings + current_warnings,
        }
    )

    reward_block, curator_block = policy_update_blocks(
        iteration_id=args.iteration_id,
        previous_label=args.previous_label,
        current_label=args.current_label,
        aggregate=aggregate,
    )

    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "summary.json").write_text(
        json.dumps(aggregate, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    )
    write_jsonl(out_dir / "transition_table.jsonl", transition_table)
    write_jsonl(out_dir / "selected_cases.jsonl", cases)
    write_jsonl(out_dir / "policy_cases.jsonl", policy_cases)
    write_jsonl(out_dir / "reward_cases.jsonl", reward_cases)
    (out_dir / "reward_policy_update.md").write_text(reward_block)
    (out_dir / "curator_policy_update.md").write_text(curator_block)
    write_markdown_report(
        out_dir / "policy_update_report.md",
        iteration_id=args.iteration_id,
        previous_label=args.previous_label,
        current_label=args.current_label,
        aggregate=aggregate,
        cases=cases,
        policy_cases=policy_cases,
        reward_cases=reward_cases,
        reward_block=reward_block,
        curator_block=curator_block,
    )

    policy_out_dir = out_dir / "policies"
    policy_out_dir.mkdir(parents=True, exist_ok=True)
    block_id = args.iteration_id
    policy_root = args.policy_root.expanduser().resolve()
    policy_pairs = (
        ("reward_policy.md", reward_block),
        ("curator_policy.md", curator_block),
    )
    for filename, block in policy_pairs:
        source = policy_root / filename
        if not source.exists():
            raise FileNotFoundError(f"Missing policy: {source}")
        updated = replace_or_append_block(
            source.read_text(errors="replace"),
            block_id=block_id,
            block=block,
        )
        (policy_out_dir / filename).write_text(updated)
        if args.apply:
            source.write_text(updated)

    print(f"Wrote {out_dir / 'summary.json'}")
    print(f"Wrote {out_dir / 'transition_table.jsonl'}")
    print(f"Wrote {out_dir / 'selected_cases.jsonl'}")
    print(f"Wrote {out_dir / 'policy_cases.jsonl'}")
    print(f"Wrote {out_dir / 'reward_cases.jsonl'}")
    print(f"Wrote {out_dir / 'policy_update_report.md'}")
    print(f"Wrote updated policy previews under {policy_out_dir}")
    if args.apply:
        print(f"Updated policies under {policy_root}")
    else:
        print("Policies were not modified; pass --apply to update policy-root.")


if __name__ == "__main__":
    main()
