import json
import os
import re
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MEMORY_PATH = ROOT / "run_logs" / "skill_harness_memory.json"

STOPWORDS = {
    "a",
    "an",
    "and",
    "are",
    "as",
    "be",
    "by",
    "for",
    "from",
    "in",
    "is",
    "it",
    "not",
    "of",
    "on",
    "or",
    "that",
    "the",
    "this",
    "to",
    "using",
    "when",
    "with",
}
GENERIC_FALLBACK_SKILL_NAMES = {
    "task-recover-from-drift",
    "task-reproduce-from-issue",
    "task-validate-targeted",
    "task-edit-minimal-owner",
    "task-localize-high-signal-paths",
}
BLOCKED_SKILL_RISKS = {
    "generic_fallback_skill",
    "private_or_sandbox_path",
    "unresolved_source_trace",
    "source_trace_exception",
}
MIN_MEMORY_SKILL_QUALITY = float(os.getenv("PI_MIN_MEMORY_SKILL_QUALITY", "0.58"))


def tokenize_text(text: str) -> set[str]:
    tokens = {
        token.lower()
        for token in re.findall(r"[A-Za-z_][A-Za-z0-9_]{2,}", text or "")
    }
    return {token for token in tokens if token not in STOPWORDS}


def memory_path_from_env() -> Path:
    value = os.getenv("PI_SKILL_HARNESS_MEMORY_PATH")
    if value:
        return Path(value).expanduser()
    return DEFAULT_MEMORY_PATH


def load_memory(path: Path | None = None) -> dict[str, Any]:
    memory_path = path or memory_path_from_env()
    if not memory_path.exists():
        return {}
    try:
        data = json.loads(memory_path.read_text())
    except (OSError, ValueError):
        return {}
    if not isinstance(data, dict):
        return {}
    return data


def active_version(memory: dict[str, Any]) -> dict[str, Any] | None:
    versions = memory.get("versions")
    if not isinstance(versions, dict):
        return None
    version_id = memory.get("active_version")
    if isinstance(version_id, str) and isinstance(versions.get(version_id), dict):
        version = dict(versions[version_id])
        version.setdefault("version_id", version_id)
        return version
    for fallback_id in sorted(versions):
        if isinstance(versions.get(fallback_id), dict):
            version = dict(versions[fallback_id])
            version.setdefault("version_id", fallback_id)
            return version
    return None


def task_slug_from_text(text: str) -> str:
    patterns = (
        r"swe-bench/([A-Za-z0-9_.-]+__[A-Za-z0-9_.-]+-\d+)(?=__|$|[^A-Za-z0-9_.-])",
        r"(?<![A-Za-z0-9_.-])([A-Za-z0-9_.-]+__[A-Za-z0-9_.-]+-\d+)(?=__|$|[^A-Za-z0-9_.-])",
    )
    for pattern in patterns:
        match = re.search(pattern, text or "")
        if match:
            return match.group(1)
    return ""


def _entry_task_slug(entry: dict[str, Any]) -> str:
    for key in ("task_name", "task_skill_id", "entry_id", "trial_dir"):
        value = entry.get(key)
        if value is None:
            continue
        slug = task_slug_from_text(str(value))
        if slug:
            return slug
    return ""


def _entry_text(entry: dict[str, Any]) -> str:
    parts: list[str] = []
    for key in (
        "task_name",
        "repo",
        "issue_title",
        "selected_skill",
        "selected_skills",
        "task_stage_skills",
        "selected_skill_resources",
        "summary",
    ):
        value = entry.get(key)
        if isinstance(value, dict):
            parts.extend(str(item) for item in value.values())
        elif value is not None:
            parts.append(str(value))
    for key in ("keywords", "touched_paths", "edited_paths", "test_commands"):
        value = entry.get(key)
        if isinstance(value, list):
            parts.extend(str(item) for item in value)
    return "\n".join(parts)


def _entry_tokens(entry: dict[str, Any]) -> set[str]:
    keywords = entry.get("keywords")
    if isinstance(keywords, list) and keywords:
        return {str(item).lower() for item in keywords}
    return tokenize_text(_entry_text(entry))


def _reward_value(entry: dict[str, Any]) -> float:
    value = entry.get("reward")
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def score_entry(query_tokens: set[str], entry: dict[str, Any]) -> float:
    entry_tokens = _entry_tokens(entry)
    if not query_tokens or not entry_tokens:
        return 0.0
    overlap = query_tokens & entry_tokens
    score = float(len(overlap))
    if entry.get("repo") and str(entry["repo"]).lower() in query_tokens:
        score += 3.0
    if _reward_value(entry) >= 1.0:
        score += 1.5
    if entry.get("task_stage_skills"):
        score += 0.75
    if entry.get("selected_skill"):
        score += 0.5
    return score


def _short_list(values: Any, limit: int = 4) -> str:
    if not isinstance(values, list) or not values:
        return "none"
    clean = [str(value) for value in values if value]
    return ", ".join(clean[:limit]) if clean else "none"


def _stage_skill_active(stage_skill: dict[str, Any]) -> bool:
    name = str(stage_skill.get("name") or "")
    if name in GENERIC_FALLBACK_SKILL_NAMES:
        return False
    if stage_skill.get("active") is False:
        return False
    quality = stage_skill.get("quality_score")
    if isinstance(quality, (int, float)) and quality < MIN_MEMORY_SKILL_QUALITY:
        return False
    risks = {str(item) for item in (stage_skill.get("risk_flags") or [])}
    if risks & BLOCKED_SKILL_RISKS:
        return False
    use_policy = str(stage_skill.get("use_policy") or "").strip().lower()
    if use_policy in {"memory-only", "disabled", "inactive"}:
        return False
    return True


def format_memory_prompt(
    version: dict[str, Any],
    selected_entries: list[dict[str, Any]],
    *,
    max_chars: int,
) -> str:
    if not selected_entries:
        return ""

    version_id = str(version.get("version_id") or "")
    parent_id = version.get("parent_version")
    lines = [
        "",
        "Trace-derived SWE task/stage skill memory:",
        f"- Active memory version: {version_id}"
        + (f" (parent: {parent_id})" if parent_id else ""),
        "- There are no global skills here. Each item below is one previous SWE task, organized by stage-specific skills: reproduce, localize, edit, validate, recover.",
        "- Treat these as weak priors and control points. Still inspect the current repository before editing.",
        "- Stage-skill details below are filtered to active, evidence-gated skills; if no concrete evidence matches, ignore them and continue normally.",
    ]
    for item in selected_entries:
        entry = item["entry"]
        selected_skill = entry.get("selected_skill") or {}
        selected_skills = entry.get("selected_skills") or []
        if not selected_skills and selected_skill:
            selected_skills = [selected_skill]
        skill_names = []
        for skill in selected_skills:
            if isinstance(skill, dict):
                name = skill.get("name")
            else:
                name = str(skill or "")
            if name:
                skill_names.append(str(name))
        reward = entry.get("reward")
        status = "resolved" if _reward_value(entry) >= 1.0 else "unresolved"
        lines.extend(
            [
                f"- Similar trace {entry.get('entry_id', '<unknown>')} ({status}, reward={reward}, score={item['score']:.1f}):",
                f"  task={entry.get('task_name') or 'unknown'}",
                f"  trace_selected_skills={', '.join(skill_names) if skill_names else 'none'}",
                f"  high_signal_paths={_short_list(entry.get('touched_paths'))}",
                f"  verification={_short_list(entry.get('test_commands'), limit=2)}",
                f"  hint={entry.get('summary') or 'Use as a retrieval hint only.'}",
            ]
        )
        for skill_summary in (entry.get("skill_summaries") or [])[:3]:
            if not isinstance(skill_summary, dict):
                continue
            name = skill_summary.get("name") or "unknown"
            role = skill_summary.get("role") or "related skill"
            hints = _short_list(skill_summary.get("hints"), limit=2)
            lines.append(f"  skill_summary[{name}]={role}; hints={hints}")
        for generated_skill in (entry.get("generated_skills") or [])[:3]:
            if not isinstance(generated_skill, dict):
                continue
            name = generated_skill.get("name") or "unknown"
            trigger = generated_skill.get("trigger") or ""
            actions = _short_list(generated_skill.get("actions"), limit=2)
            stop = generated_skill.get("stop_condition") or ""
            lines.append(
                f"  generated_skill[{name}]=trigger={trigger}; actions={actions}; stop={stop}"
            )
        for control_point in (entry.get("control_points") or [])[:4]:
            if not isinstance(control_point, dict):
                continue
            phase = control_point.get("phase") or "phase"
            trigger = control_point.get("trigger") or ""
            action = control_point.get("action") or ""
            stop = control_point.get("stop_condition") or ""
            lines.append(
                f"  control_point[{phase}]=when {trigger} -> {action}; stop={stop}"
            )
        for stage_item in (entry.get("task_stage_skills") or [])[:5]:
            if not isinstance(stage_item, dict):
                continue
            stage = stage_item.get("stage") or "stage"
            active_stage_skills = [
                skill
                for skill in (stage_item.get("skills") or [])
                if isinstance(skill, dict) and _stage_skill_active(skill)
            ]
            for stage_skill in active_stage_skills[:3]:
                if not isinstance(stage_skill, dict):
                    continue
                name = stage_skill.get("name") or "unknown"
                trigger = stage_skill.get("trigger") or ""
                actions = _short_list(stage_skill.get("actions"), limit=2)
                evidence = stage_skill.get("evidence_to_collect") or ""
                stop = stage_skill.get("stop_condition") or ""
                quality = stage_skill.get("quality_score")
                quality_text = f"; quality={quality:.2f}" if isinstance(quality, (int, float)) else ""
                lines.append(
                    f"  stage_skill[{stage}/{name}]=trigger={trigger}; actions={actions}; evidence={evidence}; stop={stop}{quality_text}"
                )
                scripts = stage_skill.get("script_resources") or []
                if scripts:
                    script_bits = []
                    for script in scripts[:2]:
                        if isinstance(script, dict):
                            script_bits.append(
                                f"{script.get('source_path') or 'script'}:{script.get('purpose') or ''}"
                            )
                    if script_bits:
                        lines.append(f"    scripts={'; '.join(script_bits)}")
                distilled_scripts = stage_skill.get("distilled_scripts") or []
                if distilled_scripts:
                    distilled_bits = []
                    for script in distilled_scripts[:2]:
                        if isinstance(script, dict):
                            filename = script.get("filename") or "script"
                            purpose = script.get("purpose") or ""
                            distilled_bits.append(f"{filename}:{purpose}")
                    if distilled_bits:
                        lines.append(
                            f"    distilled_scripts={'; '.join(distilled_bits)}"
                        )
            for point in (stage_item.get("control_points") or [])[:2]:
                if not isinstance(point, dict):
                    continue
                trigger = point.get("trigger") or ""
                action = point.get("action") or ""
                stop = point.get("stop_condition") or ""
                lines.append(
                    f"  control_point[{stage}]=when {trigger} -> {action}; stop={stop}"
                )
        skill_hints = _short_list(entry.get("skill_hints"), limit=2)
        harness_hints = _short_list(entry.get("harness_hints"), limit=2)
        avoid = _short_list(entry.get("avoid"), limit=2)
        if skill_hints != "none":
            lines.append(f"  skill_hints={skill_hints}")
        if harness_hints != "none":
            lines.append(f"  harness_hints={harness_hints}")
        if avoid != "none":
            lines.append(f"  avoid={avoid}")

    prompt = "\n".join(lines).rstrip() + "\n"
    if len(prompt) > max_chars:
        prompt = prompt[: max_chars - 30].rstrip() + "\n... [memory truncated]\n"
    return prompt


def retrieve_task_memory(
    task_text: str,
    *,
    path: Path | None = None,
    max_entries: int | None = None,
    max_prompt_chars: int | None = None,
) -> dict[str, Any]:
    memory_path = path or memory_path_from_env()
    memory = load_memory(memory_path)
    version = active_version(memory)
    if version is None:
        return {
            "enabled": False,
            "memory_path": str(memory_path),
            "reason": "missing_or_empty_memory",
            "prompt": "",
            "selected_entries": [],
        }

    entries = version.get("entries") or []
    if not isinstance(entries, list):
        entries = []
    task_slug = task_slug_from_text(task_text)
    if not task_slug:
        return {
            "enabled": False,
            "memory_path": str(memory_path),
            "version_id": version.get("version_id"),
            "retrieval_scope": "task_exact",
            "reason": "missing_task_slug_for_task_specific_memory",
            "prompt": "",
            "selected_entries": [],
        }
    query_tokens = tokenize_text(task_text)
    limit = max_entries
    if limit is None:
        limit = int(os.getenv("PI_SKILL_HARNESS_MEMORY_MAX_ENTRIES", "3"))
    char_limit = max_prompt_chars
    if char_limit is None:
        char_limit = int(os.getenv("PI_SKILL_HARNESS_MEMORY_MAX_CHARS", "2200"))

    ranked: list[dict[str, Any]] = []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        if _entry_task_slug(entry) != task_slug:
            continue
        score = score_entry(query_tokens, entry)
        if score <= 0:
            continue
        ranked.append({"score": score, "entry": entry})
    ranked.sort(
        key=lambda item: (
            item["score"],
            _reward_value(item["entry"]),
            str(item["entry"].get("entry_id") or ""),
        ),
        reverse=True,
    )
    selected = ranked[: max(0, limit)]
    prompt = format_memory_prompt(version, selected, max_chars=char_limit)
    return {
        "enabled": True,
        "memory_path": str(memory_path),
        "version_id": version.get("version_id"),
        "parent_version": version.get("parent_version"),
        "created_at": version.get("created_at"),
        "source_jobs": version.get("source_jobs") or [],
        "retrieval_scope": "task_exact",
        "task_slug": task_slug,
        "reason": "selected_exact_task_memory" if selected else "no_exact_task_memory",
        "prompt": prompt,
        "selected_entries": [
            {
                "entry_id": item["entry"].get("entry_id"),
                "task_name": item["entry"].get("task_name"),
                "repo": item["entry"].get("repo"),
                "reward": item["entry"].get("reward"),
                "score": item["score"],
                "selected_skill": item["entry"].get("selected_skill"),
                "selected_skills": item["entry"].get("selected_skills"),
                "task_stage_skills": item["entry"].get("task_stage_skills"),
            }
            for item in selected
        ],
    }
