from __future__ import annotations

from datetime import datetime, timezone
from typing import Any


HARD_RISK_PATTERNS = (
    "hidden verifier",
    "oracle patch",
    "exact patch",
    "task id",
    "/root/",
    "/home/",
    "/opt/miniconda",
)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _text_blob(candidate: dict[str, Any]) -> str:
    parts: list[str] = []
    for key in ("name", "trigger", "evidence_gate", "validation_hint", "abort_condition", "stop_condition", "support_summary"):
        parts.append(str(candidate.get(key) or ""))
    parts.extend(str(item) for item in (candidate.get("actions") or []))
    return "\n".join(parts).lower()


def _candidate_risks(candidate: dict[str, Any]) -> list[str]:
    blob = _text_blob(candidate)
    risks: list[str] = []
    if any(pattern in blob for pattern in HARD_RISK_PATTERNS):
        risks.append("hard_safety")
    actions = candidate.get("actions") or []
    if not actions:
        risks.append("missing_actions")
    if not str(candidate.get("trigger") or "").strip():
        risks.append("missing_trigger")
    if not str(candidate.get("support_summary") or "").strip():
        risks.append("missing_support_summary")
    if candidate.get("level") == "failure_mode":
        cluster = candidate.get("source_cluster") if isinstance(candidate.get("source_cluster"), dict) else {}
        support_repos = cluster.get("support_repos") or []
        if len(support_repos) < 2:
            risks.append("single_repo_failure_mode")
    return sorted(set(risks))


def evaluate_candidate(
    *,
    candidate: dict[str, Any],
    evidence: dict[str, Any],
    verifier_context: dict[str, Any] | None = None,
    evaluator_policy: dict[str, Any] | list[str] | None = None,
    min_repo_positive_support: int = 2,
    min_failure_repo_support: int = 3,
) -> dict[str, Any]:
    """Deterministic evaluator shell.

    This is deliberately small: it creates the explicit evaluator boundary now,
    and can be replaced by an agent call without changing the evo loop contract.
    """

    level = str(candidate.get("level") or "candidate")
    risks = _candidate_risks(candidate)
    case_counts = evidence.get("case_counts") if isinstance(evidence.get("case_counts"), dict) else {}
    diagnostic_counts = evidence.get("diagnostic_signature_counts") if isinstance(evidence.get("diagnostic_signature_counts"), dict) else {}
    verifier_context = verifier_context or {}
    if isinstance(evaluator_policy, dict):
        policy_rules = [str(rule) for rule in (evaluator_policy.get("rules") or []) if str(rule).strip()]
        policy_update_count = int(evaluator_policy.get("update_count") or 0)
    elif isinstance(evaluator_policy, list):
        policy_rules = [str(rule) for rule in evaluator_policy if str(rule).strip()]
        policy_update_count = 0
    else:
        policy_rules = []
        policy_update_count = 0

    positive_support = int(evidence.get("positive_support") or 0)
    support_tasks = int(evidence.get("support_tasks") or 0)
    repo_support = int(evidence.get("repo_support_count") or len(evidence.get("support_repos") or []) or 0)
    event_support = int(evidence.get("event_support_count") or 0)
    diagnostics = sum(int(value) for value in diagnostic_counts.values()) if diagnostic_counts else int(case_counts.get("diagnostic") or 0)
    strong_negative = int(case_counts.get("strong_negative") or 0)
    repeated_repo_signal = bool(
        evidence.get("repeated_paths")
        or evidence.get("repeated_tests")
        or evidence.get("repeated_edited_paths")
    )
    policy_text = "\n".join(policy_rules).lower()
    policy_requires_repeated_repo_signal = (
        level == "repo"
        and (
            "repeated path" in policy_text
            or "validation evidence" in policy_text
            or "repeated repo" in policy_text
        )
    )
    policy_penalizes_false_accepts = "false accepts" in policy_text or "false accept" in policy_text

    if level == "repo":
        if positive_support < min_repo_positive_support:
            risks.append("insufficient_positive_repo_support")
        if not repeated_repo_signal:
            risks.append("missing_repeated_repo_signal")
    if policy_requires_repeated_repo_signal and not repeated_repo_signal:
        risks.append("policy_missing_repeated_repo_signal")
    if policy_penalizes_false_accepts and strong_negative:
        risks.append("policy_false_accept_risk")
    risks = sorted(set(risks))

    score = 0.35
    if level == "repo":
        score += min(positive_support, 5) * 0.08
        score += min(support_tasks, 5) * 0.03
        if repeated_repo_signal:
            score += 0.18
        if positive_support >= min_repo_positive_support:
            score += 0.12
    elif level == "failure_mode":
        score += min(repo_support, 5) * 0.10
        score += min(event_support, 10) * 0.01
        if repo_support >= min_failure_repo_support:
            score += 0.16
    if strong_negative:
        score -= 0.20
    if diagnostics:
        score -= min(diagnostics, 5) * 0.04
    if "hard_safety" in risks:
        score = 0.0
    else:
        score -= 0.08 * len([risk for risk in risks if risk != "hard_safety"])
    score = max(0.0, min(1.0, score))

    if "hard_safety" in risks:
        decision = "reject"
        reason = "hard safety risk detected"
    elif level == "repo" and positive_support < min_repo_positive_support:
        decision = "memory_only"
        reason = "insufficient verifier-positive repo support"
    elif level == "repo" and not repeated_repo_signal:
        decision = "memory_only"
        reason = "missing repeated repo path, edit, or validation evidence"
    elif policy_penalizes_false_accepts and strong_negative:
        decision = "reject"
        reason = "evaluator policy rejected candidate because false-accept risk is present"
    elif level == "failure_mode" and repo_support < min_failure_repo_support:
        decision = "memory_only"
        reason = "insufficient cross-repo support for failure-mode candidate"
    elif score >= 0.62 and not strong_negative:
        decision = "accept"
        reason = "candidate passed deterministic verifier-proxy gate"
    elif score >= 0.45:
        decision = "revise"
        reason = "candidate has partial support but needs stronger evidence or structure"
    else:
        decision = "reject"
        reason = "candidate support is too weak"

    return {
        "created_at": utc_now(),
        "schema_version": 1,
        "candidate_level": level,
        "candidate_name": candidate.get("name"),
        "decision": decision,
        "proxy_reward": round(score, 3),
        "confidence": round(min(0.95, 0.45 + abs(score - 0.5)), 3),
        "risk_flags": risks,
        "reason": reason,
        "verifier_context": verifier_context,
        "evidence_summary": {
            "support_tasks": support_tasks,
            "positive_support": positive_support,
            "repo_support_count": repo_support,
            "event_support_count": event_support,
            "case_counts": case_counts,
            "diagnostic_count": diagnostics,
            "strong_negative": strong_negative,
            "repeated_repo_signal": repeated_repo_signal,
        },
        "evaluator_policy_snapshot": {
            "update_count": policy_update_count,
            "rules": policy_rules,
        },
    }


def calibration_event(
    *,
    candidate: dict[str, Any],
    decision: dict[str, Any],
    verifier_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    verifier_context = verifier_context or {}
    return {
        "created_at": utc_now(),
        "candidate_level": candidate.get("level"),
        "candidate_name": candidate.get("name"),
        "evaluator_decision": decision.get("decision"),
        "proxy_reward": decision.get("proxy_reward"),
        "risk_flags": decision.get("risk_flags") or [],
        "verifier_context": verifier_context,
    }
