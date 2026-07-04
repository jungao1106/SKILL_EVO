from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any


def safe_slug(value: object, *, fallback: str = "unknown", limit: int = 120) -> str:
    text = str(value or fallback)
    slug = "".join(ch if ch.isalnum() or ch in "._-" else "-" for ch in text)
    slug = "-".join(part for part in slug.split("-") if part)
    return (slug.strip("-") or fallback)[:limit]


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    if not path.exists():
        return rows
    with path.open() as handle:
        for line in handle:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def _support_count(decision: dict[str, Any]) -> int:
    candidate = decision.get("candidate") if isinstance(decision.get("candidate"), dict) else {}
    cluster = candidate.get("source_cluster") if isinstance(candidate.get("source_cluster"), dict) else {}
    raw = decision.get("repo_support_count")
    if raw is None:
        raw = cluster.get("repo_support_count")
    if raw is None:
        raw = len(decision.get("support_repos") or cluster.get("support_repos") or [])
    try:
        return int(raw or 0)
    except (TypeError, ValueError):
        return 0


def _event_support_count(decision: dict[str, Any]) -> int:
    candidate = decision.get("candidate") if isinstance(decision.get("candidate"), dict) else {}
    cluster = candidate.get("source_cluster") if isinstance(candidate.get("source_cluster"), dict) else {}
    raw = decision.get("event_support_count")
    if raw is None:
        raw = cluster.get("event_support_count")
    try:
        return int(raw or 0)
    except (TypeError, ValueError):
        return 0


def _support_repos(decision: dict[str, Any]) -> list[str]:
    candidate = decision.get("candidate") if isinstance(decision.get("candidate"), dict) else {}
    cluster = candidate.get("source_cluster") if isinstance(candidate.get("source_cluster"), dict) else {}
    repos = decision.get("support_repos") or cluster.get("support_repos") or []
    out: list[str] = []
    for repo in repos:
        text = str(repo).strip()
        if text and text not in out:
            out.append(text)
    return out


def latest_failure_mode_candidate_decisions(
    decisions: list[dict[str, Any]],
    *,
    include_promoted: bool = False,
    excluded_signatures: set[str] | None = None,
    min_repo_support: int = 2,
    max_candidates: int | None = None,
    up_to_trigger_index: int | None = None,
) -> list[dict[str, Any]]:
    """Select at most one staged failure-mode candidate per signature.

    These candidates are intended for candidate-augmented ablations, not for the
    main promoted-only transfer contract.
    """

    by_signature: dict[str, dict[str, Any]] = {}
    excluded_signatures = excluded_signatures or set()
    for index, decision in enumerate(decisions):
        if decision.get("level") != "failure_mode":
            continue
        if not isinstance(decision.get("candidate"), dict):
            continue
        if not include_promoted and decision.get("decision") == "promote":
            continue
        trigger_index = decision.get("trigger_index")
        if up_to_trigger_index is not None:
            try:
                if int(trigger_index or 0) > up_to_trigger_index:
                    continue
            except (TypeError, ValueError):
                continue
        support = _support_count(decision)
        if support < min_repo_support:
            continue
        signature = str(decision.get("failure_signature") or decision["candidate"].get("failure_signature") or "")
        if not signature:
            continue
        if signature in excluded_signatures:
            continue
        enriched = {
            **decision,
            "_source_decision_index": index + 1,
            "_support_count": support,
            "_event_support_count": _event_support_count(decision),
        }
        previous = by_signature.get(signature)
        if previous is None:
            by_signature[signature] = enriched
            continue
        previous_key = (
            int(previous.get("trigger_index") or 0),
            int(previous.get("_support_count") or 0),
            int(previous.get("_event_support_count") or 0),
            int(previous.get("_source_decision_index") or 0),
        )
        current_key = (
            int(enriched.get("trigger_index") or 0),
            int(enriched.get("_support_count") or 0),
            int(enriched.get("_event_support_count") or 0),
            int(enriched.get("_source_decision_index") or 0),
        )
        if current_key >= previous_key:
            by_signature[signature] = enriched

    selected = sorted(
        by_signature.values(),
        key=lambda item: (
            -int(item.get("_support_count") or 0),
            -int(item.get("_event_support_count") or 0),
            str(item.get("failure_signature") or ""),
        ),
    )
    if max_candidates is not None and max_candidates >= 0:
        selected = selected[:max_candidates]
    return selected


def _yaml_scalar(value: object) -> str:
    return json.dumps(str(value), ensure_ascii=True)


def render_failure_mode_candidate_skill(
    *,
    decision: dict[str, Any],
    run_name: str,
) -> str:
    candidate = decision.get("candidate") if isinstance(decision.get("candidate"), dict) else {}
    evaluator = decision.get("evaluator_decision") if isinstance(decision.get("evaluator_decision"), dict) else {}
    signature = str(decision.get("failure_signature") or candidate.get("failure_signature") or "unknown")
    name = str(candidate.get("name") or f"recover-from-{signature}")
    support = _support_count(decision)
    events = _event_support_count(decision)
    repos = _support_repos(decision)
    quality = min(0.89, 0.50 + 0.08 * min(support, 4) + 0.01 * min(events, 10))
    actions = [str(action) for action in (candidate.get("actions") or []) if str(action).strip()]
    if not actions:
        actions = [
            "Stop the current failing action pattern before expanding the edit.",
            "Reconstruct the smallest current-task symptom from public evidence.",
            "Re-localize or add the narrowest validation check before editing further.",
        ]

    lines = [
        "---",
        f"name: {name}",
        f"description: Candidate failure-mode recovery skill for {signature}; use only as negative evidence.",
        "active: true",
        f"quality_score: {quality:.2f}",
        "quality_tier: failure_mode_candidate",
        "risk_flags: []",
        "use_policy: negative-evidence-gated",
        "level: failure_mode",
        "status: candidate",
        f"failure_signature: {_yaml_scalar(signature)}",
        f"support_count: {support}",
        f"event_support_count: {events}",
        f"trigger_index: {int(decision.get('trigger_index') or 0)}",
        "---",
        "",
        f"# {name}",
        "",
        "## Status",
        "",
        f"- Run: `{run_name}`",
        "- Status: `candidate`",
        f"- Evaluator decision: `{evaluator.get('decision') or decision.get('candidate_decision')}`",
        f"- Promotion decision: `{decision.get('decision')}`",
        f"- Reason: {decision.get('reason') or evaluator.get('reason') or 'candidate selected for ablation'}",
        f"- Support: `{support}` repos; `{events}` events",
        f"- Support repos: {', '.join(repos) if repos else 'none'}",
        "",
        "## Trigger",
        "",
        str(candidate.get("trigger") or "Use only when the current public trace matches this failure signature."),
        "",
        "## Negative Evidence",
        "",
        (
            "This is a staged failure-mode candidate, not a promoted patch recipe. "
            "Use it to avoid a historically failing action pattern; do not copy source-task edits."
        ),
        "",
        "## Do Not",
        "",
        "1. Do not continue a diff that no longer connects to the failing symbol, traceback, issue text, or focused test.",
        "2. Do not accept a broad edit when validation is weak, missing, or unrelated to the current symptom.",
        "3. Do not transfer historical patch shape from the support repos without fresh current-task evidence.",
        "",
        "## Recovery Actions",
        "",
        *[f"{index}. {action}" for index, action in enumerate(actions, start=1)],
        "",
        "## Stop Condition",
        "",
        str(
            candidate.get("stop_condition")
            or candidate.get("abort_condition")
            or "Stop using this candidate when the current trace no longer matches the failure signature."
        ),
        "",
        "## Support Summary",
        "",
        str(candidate.get("support_summary") or f"{support} repos; {events} events; repos={', '.join(repos)}"),
        "",
    ]
    return "\n".join(lines)


def copy_transfer_seed_skills(*, source_root: Path, output_root: Path) -> list[str]:
    copied: list[str] = []
    for relative in ("_general", "_failure_modes"):
        source = source_root / relative
        if not source.exists():
            continue
        target = output_root / relative
        if target.exists():
            shutil.rmtree(target)
        shutil.copytree(source, target)
        copied.append(relative)
    return copied


def promoted_failure_signatures(decisions: list[dict[str, Any]]) -> set[str]:
    return {
        str(decision.get("failure_signature") or "")
        for decision in decisions
        if decision.get("level") == "failure_mode"
        and decision.get("decision") == "promote"
        and str(decision.get("failure_signature") or "").strip()
    }


def materialize_failure_candidate_augmented_pack(
    *,
    source_skill_root: Path,
    output_root: Path,
    promotion_decisions: list[dict[str, Any]],
    run_name: str,
    min_repo_support: int = 2,
    max_candidates: int | None = None,
    include_promoted_candidates: bool = False,
    include_existing_signatures: bool = False,
    up_to_trigger_index: int | None = None,
    clean: bool = True,
) -> dict[str, Any]:
    if clean and output_root.exists():
        shutil.rmtree(output_root)
    output_root.mkdir(parents=True, exist_ok=True)
    copied = copy_transfer_seed_skills(source_root=source_skill_root, output_root=output_root)
    excluded_signatures = set()
    if not include_existing_signatures:
        excluded_signatures = promoted_failure_signatures(promotion_decisions)
    selected = latest_failure_mode_candidate_decisions(
        promotion_decisions,
        include_promoted=include_promoted_candidates,
        excluded_signatures=excluded_signatures,
        min_repo_support=min_repo_support,
        max_candidates=max_candidates,
        up_to_trigger_index=up_to_trigger_index,
    )
    manifest_rows: list[dict[str, Any]] = []
    for decision in selected:
        candidate = decision.get("candidate") if isinstance(decision.get("candidate"), dict) else {}
        signature = str(decision.get("failure_signature") or candidate.get("failure_signature") or "unknown")
        name = str(candidate.get("name") or f"recover-from-{signature}")
        skill_dir = (
            output_root
            / "_failure_modes"
            / safe_slug(signature)
            / "candidate"
            / safe_slug(name)
        )
        skill_dir.mkdir(parents=True, exist_ok=True)
        skill_path = skill_dir / "SKILL.md"
        skill_path.write_text(render_failure_mode_candidate_skill(decision=decision, run_name=run_name))
        manifest_rows.append(
            {
                "failure_signature": signature,
                "name": name,
                "decision": decision.get("decision"),
                "candidate_decision": decision.get("candidate_decision"),
                "reason": decision.get("reason"),
                "support_count": _support_count(decision),
                "event_support_count": _event_support_count(decision),
                "support_repos": _support_repos(decision),
                "trigger_index": decision.get("trigger_index"),
                "source_decision_index": decision.get("_source_decision_index"),
                "skill_path": str(skill_path.relative_to(output_root)),
            }
        )

    manifest = {
        "schema_version": 1,
        "kind": "failure_mode_candidate_augmented_skill_pack",
        "run_name": run_name,
        "source_skill_root": str(source_skill_root),
        "output_root": str(output_root),
        "copied_transfer_dirs": copied,
        "selection": {
            "min_repo_support": min_repo_support,
            "max_candidates": max_candidates,
            "include_promoted_candidates": include_promoted_candidates,
            "include_existing_signatures": include_existing_signatures,
            "up_to_trigger_index": up_to_trigger_index,
            "dedupe": "latest_per_failure_signature",
        },
        "candidate_count": len(manifest_rows),
        "candidates": manifest_rows,
    }
    (output_root / "candidate_augmented_manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    )
    return manifest
