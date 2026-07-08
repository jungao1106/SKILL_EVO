from __future__ import annotations

import json
import shutil
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from evolution.candidate_pack import (
    read_jsonl,
    safe_slug,
)


def _as_int(value: object, default: int = 0) -> int:
    try:
        return int(value or default)
    except (TypeError, ValueError):
        return default


def _unique_texts(values: list[Any] | None, *, limit: int = 8) -> list[str]:
    out: list[str] = []
    for value in values or []:
        text = str(value).strip()
        if text and text not in out:
            out.append(text)
        if len(out) >= limit:
            break
    return out


def _counter_from_dicts(rows: list[dict[str, Any]], key: str) -> Counter[str]:
    counter: Counter[str] = Counter()
    for row in rows:
        values = row.get(key) or []
        if isinstance(values, str):
            values = [values]
        if isinstance(values, list):
            for value in values:
                text = str(value).strip()
                if text:
                    counter[text] += 1
    return counter


def _dict_counter_from_rows(rows: list[dict[str, Any]], key: str) -> Counter[str]:
    counter: Counter[str] = Counter()
    for row in rows:
        values = row.get(key) or {}
        if isinstance(values, dict):
            for name, count in values.items():
                text = str(name).strip()
                if text:
                    counter[text] += _as_int(count, 1)
    return counter


def _yaml_scalar(value: object) -> str:
    return json.dumps(str(value), ensure_ascii=True)


def _support_bucket(support_count: int) -> str:
    if support_count >= 3:
        return "support_3"
    if support_count == 2:
        return "support_2"
    if support_count == 1:
        return "support_1"
    return "support_0"


def _repo_cluster(decision: dict[str, Any]) -> dict[str, Any]:
    candidate = decision.get("candidate") if isinstance(decision.get("candidate"), dict) else {}
    cluster = candidate.get("source_cluster") if isinstance(candidate.get("source_cluster"), dict) else {}
    return cluster


def _failure_cluster(decision: dict[str, Any]) -> dict[str, Any]:
    candidate = decision.get("candidate") if isinstance(decision.get("candidate"), dict) else {}
    cluster = candidate.get("source_cluster") if isinstance(candidate.get("source_cluster"), dict) else {}
    return cluster


def _failure_support_count(decision: dict[str, Any]) -> int:
    cluster = _failure_cluster(decision)
    raw = decision.get("repo_support_count")
    if raw is None:
        raw = cluster.get("repo_support_count")
    if raw is None:
        raw = len(decision.get("support_repos") or cluster.get("support_repos") or [])
    return _as_int(raw)


def _failure_event_count(decision: dict[str, Any]) -> int:
    cluster = _failure_cluster(decision)
    raw = decision.get("event_support_count")
    if raw is None:
        raw = cluster.get("event_support_count")
    return _as_int(raw)


def _failure_support_repos(decision: dict[str, Any]) -> list[str]:
    cluster = _failure_cluster(decision)
    return _unique_texts(decision.get("support_repos") or cluster.get("support_repos") or [], limit=24)


def _failure_signature(decision: dict[str, Any]) -> str:
    candidate = decision.get("candidate") if isinstance(decision.get("candidate"), dict) else {}
    return str(decision.get("failure_signature") or candidate.get("failure_signature") or "unknown").strip()


def _source_decision_index(decision: dict[str, Any]) -> int:
    return _as_int(decision.get("_source_decision_index"))


@dataclass
class SuccessPattern:
    name: str
    pattern_key: str
    support_repos: list[str]
    support_events: int
    positive_support: int
    accepted_events: int
    paths: list[str]
    edited_paths: list[str]
    tests: list[str]
    failure_signatures: list[str]
    source_decision_indices: list[int]
    trigger_index: int

    @property
    def support_count(self) -> int:
        return len(self.support_repos)


def _repo_pattern_key(cluster: dict[str, Any]) -> str:
    bits: list[str] = []
    if cluster.get("repeated_paths"):
        bits.append("localize")
    if cluster.get("repeated_edited_paths"):
        bits.append("edit")
    if cluster.get("repeated_tests"):
        bits.append("validate")
    if cluster.get("failure_signature_counts"):
        bits.append("recover")
    if not bits:
        bits.append("positive-procedure")
    return "+".join(bits)


def _pattern_title(pattern_key: str) -> str:
    mapping = {
        "localize": "localization",
        "edit": "narrow edit",
        "validate": "focused validation",
        "recover": "recovery",
        "positive-procedure": "positive repair procedure",
    }
    parts = [mapping.get(part, part.replace("-", " ")) for part in pattern_key.split("+")]
    if len(parts) == 1:
        return parts[0]
    return ", ".join(parts[:-1]) + f", and {parts[-1]}"


def latest_success_patterns(
    decisions: list[dict[str, Any]],
    *,
    min_repo_support: int = 2,
    min_positive_support: int = 2,
    max_patterns: int | None = None,
    require_accepted: bool = True,
) -> list[SuccessPattern]:
    groups: dict[str, list[tuple[int, dict[str, Any]]]] = defaultdict(list)
    for index, decision in enumerate(decisions, start=1):
        if decision.get("level") != "repo":
            continue
        candidate = decision.get("candidate") if isinstance(decision.get("candidate"), dict) else {}
        cluster = _repo_cluster(decision)
        if not cluster:
            continue
        if require_accepted and not (
            decision.get("candidate_decision") == "accept"
            or decision.get("decision") in {"promote", "refresh"}
        ):
            continue
        positive_support = _as_int(cluster.get("positive_support") or decision.get("positive_task_evidence_count"))
        if positive_support < min_positive_support:
            continue
        pattern_key = _repo_pattern_key(cluster)
        enriched = {
            **decision,
            "_source_decision_index": index,
            "_pattern_key": pattern_key,
            "_repo": str(candidate.get("repo") or decision.get("repo") or cluster.get("repo") or "unknown"),
            "_positive_support": positive_support,
        }
        groups[pattern_key].append((index, enriched))

    patterns: list[SuccessPattern] = []
    for pattern_key, rows_with_index in groups.items():
        rows = [row for _, row in rows_with_index]
        repos = sorted({str(row.get("_repo") or "unknown") for row in rows})
        if len(repos) < min_repo_support:
            continue
        clusters = [_repo_cluster(row) for row in rows]
        path_counter = _counter_from_dicts(clusters, "repeated_paths")
        edited_counter = _counter_from_dicts(clusters, "repeated_edited_paths")
        test_counter = _counter_from_dicts(clusters, "repeated_tests")
        failure_counter = _dict_counter_from_rows(clusters, "failure_signature_counts")
        accepted_events = sum(
            1
            for row in rows
            if row.get("candidate_decision") == "accept" or row.get("decision") in {"promote", "refresh"}
        )
        patterns.append(
            SuccessPattern(
                name=f"success-{safe_slug(pattern_key, limit=80)}",
                pattern_key=pattern_key,
                support_repos=repos,
                support_events=len(rows),
                positive_support=sum(_as_int(row.get("_positive_support")) for row in rows),
                accepted_events=accepted_events,
                paths=[value for value, _ in path_counter.most_common(8)],
                edited_paths=[value for value, _ in edited_counter.most_common(8)],
                tests=[value for value, _ in test_counter.most_common(4)],
                failure_signatures=[value for value, _ in failure_counter.most_common(5)],
                source_decision_indices=[_as_int(row.get("_source_decision_index")) for row in rows],
                trigger_index=max(_as_int(row.get("trigger_index") or row.get("update_index")) for row in rows),
            )
        )

    patterns.sort(
        key=lambda item: (
            -item.support_count,
            -item.accepted_events,
            -item.positive_support,
            item.pattern_key,
        )
    )
    if max_patterns is not None and max_patterns >= 0:
        patterns = patterns[:max_patterns]
    return patterns


def latest_failure_skills(
    decisions: list[dict[str, Any]],
    *,
    min_repo_support: int = 2,
    include_support_1: bool = False,
    max_skills: int | None = None,
) -> list[dict[str, Any]]:
    by_signature: dict[str, dict[str, Any]] = {}
    promoted_signatures: set[str] = set()
    for index, decision in enumerate(decisions, start=1):
        if decision.get("level") != "failure_mode":
            continue
        if not isinstance(decision.get("candidate"), dict):
            continue
        support = _failure_support_count(decision)
        if include_support_1:
            if support < 1:
                continue
        elif support < min_repo_support:
            continue
        signature = _failure_signature(decision)
        if not signature:
            continue
        if decision.get("decision") == "promote":
            promoted_signatures.add(signature)
        enriched = {
            **decision,
            "_source_decision_index": index,
            "_support_count": support,
            "_event_support_count": _failure_event_count(decision),
        }
        previous = by_signature.get(signature)
        if previous is None:
            by_signature[signature] = enriched
            continue
        previous_key = (
            int(previous.get("_support_count") or 0),
            int(previous.get("_event_support_count") or 0),
            int(previous.get("trigger_index") or 0),
            int(previous.get("_source_decision_index") or 0),
        )
        current_key = (
            int(enriched.get("_support_count") or 0),
            int(enriched.get("_event_support_count") or 0),
            int(enriched.get("trigger_index") or 0),
            int(enriched.get("_source_decision_index") or 0),
        )
        if current_key >= previous_key:
            by_signature[signature] = enriched

    for signature, decision in by_signature.items():
        decision["_promoted"] = signature in promoted_signatures or decision.get("decision") == "promote"

    selected = sorted(
        by_signature.values(),
        key=lambda item: (
            not bool(item.get("_promoted")),
            -int(item.get("_support_count") or 0),
            -int(item.get("_event_support_count") or 0),
            str(item.get("failure_signature") or ""),
        ),
    )
    if max_skills is not None and max_skills >= 0:
        selected = selected[:max_skills]
    return selected


def render_success_pattern_skill(*, pattern: SuccessPattern, run_name: str) -> str:
    support = pattern.support_count
    quality = min(0.90, 0.58 + 0.03 * min(support, 8) + 0.01 * min(pattern.accepted_events, 8))
    title = _pattern_title(pattern.pattern_key)
    actions = [
        "Start from the smallest public symptom: issue text, traceback, failing assertion, or focused test.",
        "Choose the narrowest owner path that is supported by current evidence before editing.",
        "Keep edits minimal and re-check the focused behavior before broad validation.",
    ]
    if "localize" in pattern.pattern_key:
        actions.insert(1, "Use repeated owner paths only as localization priors; confirm with current repository evidence.")
    if "validate" in pattern.pattern_key:
        actions.append("Prefer a focused validation command that directly exercises the changed behavior.")
    if "recover" in pattern.pattern_key:
        actions.append("If the current diff drifts away from the symptom, stop and re-localize before expanding scope.")

    lines = [
        "---",
        f"name: {pattern.name}",
        f"description: Training-distilled success pattern for {title}.",
        "active: true",
        f"quality_score: {quality:.2f}",
        "quality_tier: success_pattern",
        "risk_flags: []",
        "use_policy: evidence-gated",
        "level: success_pattern",
        "status: frozen",
        f"skill_type: {_yaml_scalar('success_pattern')}",
        f"support_count: {support}",
        f"event_support_count: {pattern.support_events}",
        f"positive_support_count: {pattern.positive_support}",
        f"support_bucket: {_yaml_scalar(_support_bucket(support))}",
        f"pattern_key: {_yaml_scalar(pattern.pattern_key)}",
        f"trigger_index: {pattern.trigger_index}",
        "---",
        "",
        f"# {pattern.name}",
        "",
        "## Status",
        "",
        f"- Run: `{run_name}`",
        "- Status: `frozen`",
        "- Source: training-only repo-level success evidence",
        f"- Support: `{support}` repos; `{pattern.support_events}` repo events; `{pattern.positive_support}` verifier-positive task events",
        f"- Support repos: {', '.join(pattern.support_repos)}",
        "",
        "## Applicability",
        "",
        (
            f"Use this skill when current task evidence calls for {title}. "
            "It is a procedural prior, not a repo-specific patch recipe."
        ),
        "",
        "## Evidence Gate",
        "",
        "Use only after inspecting the current repository evidence. Ignore it if the issue, traceback, owner path, or test signal does not match the current task.",
        "",
        "## Actions",
        "",
        *[f"{index}. {action}" for index, action in enumerate(actions, start=1)],
        "",
        "## Do Not",
        "",
        "1. Do not copy historical paths, tests, or patch shape without fresh current-task evidence.",
        "2. Do not broaden the edit solely because this skill was retrieved.",
        "3. Do not treat weak validation as success; tie validation to the changed behavior.",
        "",
        "## Support Summary",
        "",
        f"- Pattern key: `{pattern.pattern_key}`",
        f"- Common paths: {', '.join(pattern.paths) if pattern.paths else 'none'}",
        f"- Common edited paths: {', '.join(pattern.edited_paths) if pattern.edited_paths else 'none'}",
        f"- Common focused tests: {' | '.join(pattern.tests) if pattern.tests else 'none'}",
        f"- Associated failure signatures: {', '.join(pattern.failure_signatures) if pattern.failure_signatures else 'none'}",
        "",
    ]
    return "\n".join(lines)


def render_failure_skill(*, decision: dict[str, Any], run_name: str) -> str:
    candidate = decision.get("candidate") if isinstance(decision.get("candidate"), dict) else {}
    evaluator = decision.get("evaluator_decision") if isinstance(decision.get("evaluator_decision"), dict) else {}
    signature = _failure_signature(decision)
    name = str(candidate.get("name") or f"recover-from-{signature}")
    support = _failure_support_count(decision)
    events = _failure_event_count(decision)
    status = "promoted" if decision.get("_promoted") or decision.get("decision") == "promote" else "support_qualified"
    quality = min(0.91, 0.57 + 0.06 * min(support, 5) + 0.01 * min(events, 12))
    actions = [str(action) for action in (candidate.get("actions") or []) if str(action).strip()]
    if not actions:
        actions = [
            "Stop the current failing action pattern before expanding the edit.",
            "Reconstruct the smallest current-task symptom from public evidence.",
            "Re-localize or add the narrowest validation check before editing further.",
        ]
    repos = _failure_support_repos(decision)

    lines = [
        "---",
        f"name: {name}",
        f"description: Frozen training-distilled failure-mode recovery skill for {signature}.",
        "active: true",
        f"quality_score: {quality:.2f}",
        "quality_tier: failure_mode",
        "risk_flags: []",
        "use_policy: negative-evidence-gated",
        "level: failure_mode",
        f"status: {status}",
        f"skill_type: {_yaml_scalar('failure_mode')}",
        f"failure_signature: {_yaml_scalar(signature)}",
        f"support_count: {support}",
        f"event_support_count: {events}",
        f"support_bucket: {_yaml_scalar(_support_bucket(support))}",
        f"trigger_index: {_as_int(decision.get('trigger_index'))}",
        "---",
        "",
        f"# {name}",
        "",
        "## Status",
        "",
        f"- Run: `{run_name}`",
        f"- Status: `{status}`",
        "- Source: training-only failure-mode evidence",
        f"- Evaluator decision: `{evaluator.get('decision') or decision.get('candidate_decision')}`",
        f"- Promotion decision: `{decision.get('decision')}`",
        f"- Reason: {decision.get('reason') or evaluator.get('reason') or 'support-qualified by training evidence'}",
        f"- Support: `{support}` repos; `{events}` events",
        f"- Support repos: {', '.join(repos) if repos else 'none'}",
        "",
        "## Trigger",
        "",
        str(candidate.get("trigger") or "Use only when the current public trace matches this failure signature."),
        "",
        "## Negative Evidence Gate",
        "",
        (
            "Use this skill to avoid or recover from a historically recurring failure mode. "
            "Do not copy source-task edits; require fresh current-task evidence."
        ),
        "",
        "## Recovery Actions",
        "",
        *[f"{index}. {action}" for index, action in enumerate(actions, start=1)],
        "",
        "## Do Not",
        "",
        "1. Do not continue a diff that no longer connects to the failing symbol, traceback, issue text, or focused test.",
        "2. Do not accept a broad edit when validation is weak, missing, or unrelated to the current symptom.",
        "3. Do not treat this as proof that the historical patch shape transfers.",
        "",
        "## Stop Condition",
        "",
        str(
            candidate.get("stop_condition")
            or candidate.get("abort_condition")
            or "Stop using this skill when the current trace no longer matches the failure signature."
        ),
        "",
        "## Support Summary",
        "",
        str(candidate.get("support_summary") or f"{support} repos; {events} events; repos={', '.join(repos)}"),
        "",
    ]
    return "\n".join(lines)


def copy_general_seed_skills(*, source_root: Path, output_root: Path) -> list[str]:
    copied: list[str] = []
    source = source_root / "_general"
    if not source.exists():
        return copied
    target = output_root / "_general"
    if target.exists():
        shutil.rmtree(target)
    shutil.copytree(source, target)
    copied.append("_general")
    return copied


def materialize_frozen_skill_library(
    *,
    source_skill_root: Path,
    output_root: Path,
    promotion_decisions: list[dict[str, Any]],
    run_name: str,
    min_success_repo_support: int = 2,
    min_success_positive_support: int = 2,
    max_success_skills: int | None = 8,
    require_accepted_success: bool = True,
    min_failure_repo_support: int = 2,
    max_failure_skills: int | None = 8,
    include_support_1_failures: bool = False,
    include_general: bool = True,
    clean: bool = True,
) -> dict[str, Any]:
    if clean and output_root.exists():
        shutil.rmtree(output_root)
    output_root.mkdir(parents=True, exist_ok=True)
    copied = copy_general_seed_skills(source_root=source_skill_root, output_root=output_root) if include_general else []

    success_patterns = latest_success_patterns(
        promotion_decisions,
        min_repo_support=min_success_repo_support,
        min_positive_support=min_success_positive_support,
        max_patterns=max_success_skills,
        require_accepted=require_accepted_success,
    )
    failure_skills = latest_failure_skills(
        promotion_decisions,
        min_repo_support=min_failure_repo_support,
        include_support_1=include_support_1_failures,
        max_skills=max_failure_skills,
    )

    success_rows: list[dict[str, Any]] = []
    for pattern in success_patterns:
        skill_dir = output_root / "_success_patterns" / safe_slug(pattern.pattern_key) / safe_slug(pattern.name)
        skill_dir.mkdir(parents=True, exist_ok=True)
        skill_path = skill_dir / "SKILL.md"
        skill_path.write_text(render_success_pattern_skill(pattern=pattern, run_name=run_name))
        success_rows.append(
            {
                "name": pattern.name,
                "pattern_key": pattern.pattern_key,
                "support_count": pattern.support_count,
                "event_support_count": pattern.support_events,
                "positive_support_count": pattern.positive_support,
                "accepted_events": pattern.accepted_events,
                "support_repos": pattern.support_repos,
                "support_bucket": _support_bucket(pattern.support_count),
                "trigger_index": pattern.trigger_index,
                "source_decision_indices": pattern.source_decision_indices,
                "skill_path": str(skill_path.relative_to(output_root)),
            }
        )

    failure_rows: list[dict[str, Any]] = []
    for decision in failure_skills:
        candidate = decision.get("candidate") if isinstance(decision.get("candidate"), dict) else {}
        signature = _failure_signature(decision)
        name = str(candidate.get("name") or f"recover-from-{signature}")
        status = "promoted" if decision.get("_promoted") or decision.get("decision") == "promote" else "support_qualified"
        skill_dir = (
            output_root
            / "_failure_modes"
            / safe_slug(signature)
            / status
            / safe_slug(name)
        )
        skill_dir.mkdir(parents=True, exist_ok=True)
        skill_path = skill_dir / "SKILL.md"
        skill_path.write_text(render_failure_skill(decision=decision, run_name=run_name))
        failure_rows.append(
            {
                "name": name,
                "failure_signature": signature,
                "status": status,
                "decision": decision.get("decision"),
                "candidate_decision": decision.get("candidate_decision"),
                "reason": decision.get("reason"),
                "support_count": _failure_support_count(decision),
                "event_support_count": _failure_event_count(decision),
                "support_repos": _failure_support_repos(decision),
                "support_bucket": _support_bucket(_failure_support_count(decision)),
                "trigger_index": decision.get("trigger_index"),
                "source_decision_index": decision.get("_source_decision_index"),
                "skill_path": str(skill_path.relative_to(output_root)),
            }
        )

    manifest = {
        "schema_version": 1,
        "kind": "frozen_training_distilled_skill_library",
        "run_name": run_name,
        "source_skill_root": str(source_skill_root),
        "output_root": str(output_root),
        "copied_transfer_dirs": copied,
        "selection": {
            "min_success_repo_support": min_success_repo_support,
            "min_success_positive_support": min_success_positive_support,
            "max_success_skills": max_success_skills,
            "require_accepted_success": require_accepted_success,
            "min_failure_repo_support": min_failure_repo_support,
            "max_failure_skills": max_failure_skills,
            "include_support_1_failures": include_support_1_failures,
            "include_general": include_general,
            "feedback_scope": "training_only",
            "downstream_updates": "disabled",
        },
        "skill_counts": {
            "general": len(list((output_root / "_general").rglob("SKILL.md"))) if include_general else 0,
            "success_patterns": len(success_rows),
            "failure_modes": len(failure_rows),
            "total": (
                (len(list((output_root / "_general").rglob("SKILL.md"))) if include_general else 0)
                + len(success_rows)
                + len(failure_rows)
            ),
        },
        "success_patterns": success_rows,
        "failure_modes": failure_rows,
    }
    (output_root / "frozen_library_manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    )
    return manifest


def materialize_frozen_skill_library_from_files(
    *,
    source_skill_root: Path,
    output_root: Path,
    promotion_decisions_path: Path,
    run_name: str,
    min_success_repo_support: int = 2,
    min_success_positive_support: int = 2,
    max_success_skills: int | None = 8,
    require_accepted_success: bool = True,
    min_failure_repo_support: int = 2,
    max_failure_skills: int | None = 8,
    include_support_1_failures: bool = False,
    include_general: bool = True,
    clean: bool = True,
) -> dict[str, Any]:
    return materialize_frozen_skill_library(
        source_skill_root=source_skill_root,
        output_root=output_root,
        promotion_decisions=read_jsonl(promotion_decisions_path),
        run_name=run_name,
        min_success_repo_support=min_success_repo_support,
        min_success_positive_support=min_success_positive_support,
        max_success_skills=max_success_skills,
        require_accepted_success=require_accepted_success,
        min_failure_repo_support=min_failure_repo_support,
        max_failure_skills=max_failure_skills,
        include_support_1_failures=include_support_1_failures,
        include_general=include_general,
        clean=clean,
    )
