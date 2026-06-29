from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _short_list(values: list[Any] | None, *, limit: int = 6) -> list[str]:
    out: list[str] = []
    for value in values or []:
        text = str(value).strip()
        if text and text not in out:
            out.append(text)
        if len(out) >= limit:
            break
    return out


def repeated_values(
    entries: list[dict[str, Any]],
    key: str,
    *,
    limit: int = 8,
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


def build_repo_cluster(
    *,
    repo: str,
    entries: list[dict[str, Any]],
    update_index: int,
    case_counts: dict[str, int],
    failure_signature_counts: dict[str, int],
    diagnostic_signature_counts: dict[str, int],
    positive_entries: list[dict[str, Any]],
) -> dict[str, Any]:
    return {
        "created_at": utc_now(),
        "level": "repo_cluster",
        "repo": repo,
        "update_index": update_index,
        "support_tasks": len(entries),
        "positive_support": len(positive_entries),
        "case_counts": case_counts,
        "failure_signature_counts": failure_signature_counts,
        "diagnostic_signature_counts": diagnostic_signature_counts,
        "repeated_paths": repeated_values(positive_entries or entries, "touched_paths"),
        "repeated_edited_paths": repeated_values(positive_entries or entries, "edited_paths"),
        "repeated_tests": repeated_values(positive_entries or entries, "test_commands", limit=4),
        "source_entry_ids": [str(entry.get("entry_id") or "") for entry in entries],
        "source_tasks": [str(entry.get("task_name") or "") for entry in entries],
    }


def write_repo_candidate(cluster: dict[str, Any]) -> dict[str, Any]:
    repo = str(cluster.get("repo") or "unknown")
    repeated_paths = _short_list(cluster.get("repeated_paths"), limit=6)
    repeated_tests = _short_list(cluster.get("repeated_tests"), limit=3)
    repeated_failures = [
        key
        for key, _ in Counter(cluster.get("failure_signature_counts") or {}).most_common(3)
    ]
    name_parts = [repo.replace("__", "-").replace("_", "-"), "repo"]
    if repeated_paths:
        name_parts.append("localize")
    if repeated_tests:
        name_parts.append("validate")
    if repeated_failures:
        name_parts.append("recover")
    name = "-".join(name_parts)
    trigger_bits = [f"current task is in repo {repo}"]
    if repeated_paths:
        trigger_bits.append("public trace matches repeated owner paths or adjacent modules")
    if repeated_tests:
        trigger_bits.append("focused validation resembles repeated test commands")
    if repeated_failures:
        trigger_bits.append("failure signature matches repeated repo failures")
    actions: list[str] = []
    if repeated_paths:
        actions.append("Start localization from the repeated owner paths only after current evidence matches them.")
    if repeated_tests:
        actions.append("Prefer the repeated focused validation command when it matches the current issue.")
    if repeated_failures:
        actions.append("If the repeated failure signature appears, recover before broadening the edit.")
    if not actions:
        actions.append("Use the repo evidence only as weak background and collect a fresh current-task signal first.")
    return {
        "created_at": utc_now(),
        "level": "repo",
        "repo": repo,
        "name": name[:120],
        "trigger": "; ".join(trigger_bits),
        "evidence_gate": "Use only when current public evidence independently matches the repo cluster; do not copy a source-task patch.",
        "actions": actions,
        "validation_hint": "; ".join(repeated_tests) if repeated_tests else "derive the narrowest public check from the current issue",
        "abort_condition": "Stop using this repo candidate if the current traceback, symbol, path, or focused test does not match the cluster evidence.",
        "support_summary": (
            f"{cluster.get('support_tasks', 0)} task events; "
            f"{cluster.get('positive_support', 0)} verifier-positive events; "
            f"paths={', '.join(repeated_paths) or 'none'}; "
            f"tests={', '.join(repeated_tests) or 'none'}; "
            f"failures={', '.join(repeated_failures) or 'none'}"
        ),
        "source_cluster": cluster,
    }


def build_failure_cluster(
    *,
    signature: str,
    repo_clusters: list[dict[str, Any]],
    trigger_index: int,
) -> dict[str, Any]:
    support_repos = sorted({str(cluster.get("repo") or "unknown") for cluster in repo_clusters})
    return {
        "created_at": utc_now(),
        "level": "failure_cluster",
        "failure_signature": signature,
        "trigger_index": trigger_index,
        "support_repos": support_repos,
        "repo_support_count": len(support_repos),
        "event_support_count": sum(int((cluster.get("failure_signature_counts") or {}).get(signature) or 0) for cluster in repo_clusters),
        "repo_clusters": repo_clusters,
    }


def write_failure_mode_candidate(cluster: dict[str, Any]) -> dict[str, Any]:
    signature = str(cluster.get("failure_signature") or "unknown-failure-mode")
    support_repos = _short_list(cluster.get("support_repos"), limit=12)
    return {
        "created_at": utc_now(),
        "level": "failure_mode",
        "failure_signature": signature,
        "name": f"recover-from-{signature}"[:120],
        "trigger": "Use when the current public trace shows this failure signature; do not use solely because the skill exists.",
        "actions": [
            "Reconstruct the smallest current-task symptom before editing again.",
            "Check whether the current diff still connects to the failing symbol, traceback, or focused test.",
            "If localization drifted, discard unrelated paths and re-localize from public evidence.",
            "If validation is weak or missing, derive the narrowest public check before broad testing.",
        ],
        "stop_condition": "Stop when the current trace no longer matches the failure signature or a narrower repo/current-task signal overrides it.",
        "support_summary": (
            f"{cluster.get('repo_support_count', 0)} repos; "
            f"{cluster.get('event_support_count', 0)} events; "
            f"repos={', '.join(support_repos)}"
        ),
        "source_cluster": cluster,
    }


def candidate_to_skill_markdown(
    *,
    candidate: dict[str, Any],
    decision: dict[str, Any],
    run_name: str,
) -> str:
    level = str(candidate.get("level") or "candidate")
    name = str(candidate.get("name") or f"{level}-skill")
    quality = float(decision.get("proxy_reward") or 0.0)
    lines = [
        "---",
        f"name: {name}",
        f"description: {level} skill accepted from verifier-calibrated evaluator.",
        "active: true",
        f"quality_score: {quality:.2f}",
        f"quality_tier: {level}",
        "risk_flags: []",
        "use_policy: evidence-gated",
        f"level: {level}",
        "---",
        "",
        f"# {name}",
        "",
        f"- Run: `{run_name}`",
        f"- Evaluator decision: `{decision.get('decision')}`",
        f"- Proxy reward: `{decision.get('proxy_reward')}`",
        f"- Confidence: `{decision.get('confidence')}`",
        "",
        "## Trigger",
        "",
        str(candidate.get("trigger") or ""),
        "",
        "## Evidence Gate",
        "",
        str(candidate.get("evidence_gate") or "Use only when current public evidence matches the support summary."),
        "",
        "## Actions",
        "",
        *[f"{index}. {action}" for index, action in enumerate(candidate.get("actions") or [], start=1)],
        "",
        "## Validation Hint",
        "",
        str(candidate.get("validation_hint") or ""),
        "",
        "## Stop Condition",
        "",
        str(candidate.get("stop_condition") or candidate.get("abort_condition") or ""),
        "",
        "## Support Summary",
        "",
        str(candidate.get("support_summary") or ""),
        "",
    ]
    return "\n".join(lines)


def write_candidate_skill(
    *,
    version_root: Path,
    candidate: dict[str, Any],
    decision: dict[str, Any],
    run_name: str,
) -> Path:
    level = str(candidate.get("level") or "candidate")
    name = str(candidate.get("name") or f"{level}-skill")
    safe_name = "".join(ch if ch.isalnum() or ch in "._-" else "-" for ch in name).strip("-") or level
    if level == "repo":
        repo = str(candidate.get("repo") or "unknown")
        safe_repo = "".join(ch if ch.isalnum() or ch in "._-" else "-" for ch in repo).strip("-") or "unknown"
        skill_dir = version_root / "_repos" / safe_repo / "candidate" / safe_name
    elif level == "failure_mode":
        signature = str(candidate.get("failure_signature") or "unknown")
        safe_signature = "".join(ch if ch.isalnum() or ch in "._-" else "-" for ch in signature).strip("-") or "unknown"
        skill_dir = version_root / "_failure_modes" / safe_signature / "recover" / safe_name
    else:
        skill_dir = version_root / "_candidates" / safe_name
    skill_dir.mkdir(parents=True, exist_ok=True)
    skill_path = skill_dir / "SKILL.md"
    skill_path.write_text(candidate_to_skill_markdown(candidate=candidate, decision=decision, run_name=run_name))
    return skill_path
