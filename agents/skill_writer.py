from __future__ import annotations

import re
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


_BOOLEAN_WRITER_DIRECTIVES = (
    "public_evidence_only",
    "require_same_repo_repetition",
    "recover_validate_only",
    "gate_surviving_signals_only",
)


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


def normalize_writer_policy(writer_policy: dict[str, Any] | None) -> dict[str, Any]:
    """Compile the learned writer policy into directives consumed by the writer.

    New policy states persist explicit directives.  The text fallback keeps archived
    policy states operational instead of silently ignoring their learned rules.
    """

    raw = writer_policy if isinstance(writer_policy, dict) else {}
    rules = [str(rule).strip() for rule in (raw.get("rules") or []) if str(rule).strip()]
    explicit = raw.get("directives") if isinstance(raw.get("directives"), dict) else {}
    rule_text = " ".join(rules).lower()
    directives: dict[str, Any] = {
        "public_evidence_only": "trace-visible" in rule_text and "public" in rule_text,
        "require_same_repo_repetition": "same-repo" in rule_text and "repeated support" in rule_text,
        "recover_validate_only": "recover/validate" in rule_text and "semantic edit" in rule_text,
        "gate_surviving_signals_only": "survived the lower-level gate" in rule_text,
    }
    for key in _BOOLEAN_WRITER_DIRECTIVES:
        if key in explicit:
            directives[key] = bool(explicit[key])

    max_actions = explicit.get("max_actions")
    if max_actions is None:
        match = re.search(r"at most\s+(\d+)\s+(?:recover/validate\s+)?actions", rule_text)
        max_actions = match.group(1) if match else None
    try:
        directives["max_actions"] = max(1, min(8, int(max_actions))) if max_actions is not None else None
    except (TypeError, ValueError):
        directives["max_actions"] = None

    return {
        "rules": rules,
        "directives": directives,
        "update_count": int(raw.get("update_count") or 0),
        **({"path": raw.get("path")} if raw.get("path") else {}),
    }


def _append_gate(current: str, addition: str) -> str:
    current = str(current or "").strip()
    addition = str(addition or "").strip()
    if not current:
        return addition
    if not addition or addition in current:
        return current
    return f"{current} {addition}"


def _policy_application(
    policy: dict[str, Any],
    effects: list[str],
) -> dict[str, Any]:
    directives = policy.get("directives") if isinstance(policy.get("directives"), dict) else {}
    return {
        "supplied": bool(policy.get("rules") or any(value for value in directives.values())),
        "applied": bool(effects),
        "update_count": int(policy.get("update_count") or 0),
        "rule_count": len(policy.get("rules") or []),
        "effects": effects,
    }


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


def write_repo_candidate(
    cluster: dict[str, Any],
    *,
    writer_policy: dict[str, Any] | None = None,
) -> dict[str, Any]:
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
    candidate = {
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
            f"{cluster.get('positive_support', 0)} positive events; "
            f"paths={', '.join(repeated_paths) or 'none'}; "
            f"tests={', '.join(repeated_tests) or 'none'}; "
            f"failures={', '.join(repeated_failures) or 'none'}"
        ),
        "source_cluster": cluster,
    }
    policy = normalize_writer_policy(writer_policy)
    directives = policy["directives"]
    effects: list[str] = []
    if directives["public_evidence_only"]:
        candidate["evidence_gate"] = _append_gate(
            candidate["evidence_gate"],
            "Match using trace-visible public paths, commands, outputs, or diagnostics only.",
        )
        effects.append("public_evidence_only")
    if directives["require_same_repo_repetition"]:
        candidate["trigger"] = (
            f"All required: current task is in repo {repo}; current public evidence independently "
            "matches at least one repeated same-repo path, validation command, or failure signature."
        )
        candidate["evidence_gate"] = _append_gate(
            candidate["evidence_gate"],
            "A repo name alone is insufficient; require a repeated same-repo signal.",
        )
        effects.append("require_same_repo_repetition")
        if not (repeated_paths or repeated_tests or repeated_failures):
            candidate["writer_abstained"] = True
            candidate["actions"] = [
                "Collect a repeated same-repo public signal before activating this candidate."
            ]
            effects.append("abstain_without_repeated_signal")
    if directives["recover_validate_only"] and int(cluster.get("positive_support") or 0) == 0:
        allowed_terms = ("validation", "test", "failure", "recover", "traceback", "symptom")
        candidate["actions"] = [
            action
            for action in candidate["actions"]
            if any(term in action.lower() for term in allowed_terms)
        ] or [
            "Reconstruct the current symptom and derive the narrowest public validation before editing."
        ]
        effects.append("recover_validate_only")
    if directives["gate_surviving_signals_only"]:
        candidate["evidence_gate"] = _append_gate(
            candidate["evidence_gate"],
            "Do not introduce trigger details or actions that are absent from the repeated cluster fields.",
        )
        effects.append("gate_surviving_signals_only")
    max_actions = directives.get("max_actions")
    if max_actions is not None and len(candidate["actions"]) > max_actions:
        candidate["actions"] = candidate["actions"][:max_actions]
        effects.append(f"max_actions={max_actions}")
    candidate["writer_policy_application"] = _policy_application(policy, effects)
    return candidate


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


def write_failure_mode_candidate(
    cluster: dict[str, Any],
    *,
    writer_policy: dict[str, Any] | None = None,
) -> dict[str, Any]:
    signature = str(cluster.get("failure_signature") or "unknown-failure-mode")
    support_repos = _short_list(cluster.get("support_repos"), limit=12)
    candidate = {
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
    policy = normalize_writer_policy(writer_policy)
    directives = policy["directives"]
    effects: list[str] = []
    if directives["public_evidence_only"]:
        candidate["evidence_gate"] = (
            "Require the current trace-visible public diagnostic to match this failure signature; "
            "task identity or hidden verifier information is never evidence."
        )
        effects.append("public_evidence_only")
    if directives["recover_validate_only"]:
        allowed_terms = (
            "symptom",
            "diff",
            "failing symbol",
            "traceback",
            "test",
            "localization drifted",
            "validation",
        )
        candidate["actions"] = [
            action
            for action in candidate["actions"]
            if any(term in action.lower() for term in allowed_terms)
        ]
        effects.append("recover_validate_only")
    if directives["gate_surviving_signals_only"]:
        candidate["evidence_gate"] = _append_gate(
            candidate.get("evidence_gate") or "",
            "Use only the supported failure signature and cross-repo support retained by the lower-level gate.",
        )
        effects.append("gate_surviving_signals_only")
    max_actions = directives.get("max_actions")
    if max_actions is not None and len(candidate["actions"]) > max_actions:
        candidate["actions"] = candidate["actions"][:max_actions]
        effects.append(f"max_actions={max_actions}")
    candidate["writer_policy_application"] = _policy_application(policy, effects)
    return candidate


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
        f"description: {level} skill accepted by the evaluator.",
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
