from __future__ import annotations

import json
import math
import re
import shutil
import tomllib
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from agents.skill_evaluator import calibration_event, evaluate_candidate
from agents.skill_writer import (
    build_failure_cluster,
    build_repo_cluster,
    write_failure_mode_candidate,
    write_repo_candidate,
)
from evolution.score import first_reward_value


REWARD_CONDITION_OPERATORS = ("eq", "ne", "lt", "le", "gt", "ge")
_REWARD_OPERATOR_SYMBOLS = {
    "eq": "==",
    "ne": "!=",
    "lt": "<",
    "le": "<=",
    "gt": ">",
    "ge": ">=",
}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def safe_slug(value: object, *, fallback: str = "unknown", limit: int = 120) -> str:
    text = str(value or fallback)
    slug = "".join(ch if ch.isalnum() or ch in "._-" else "-" for ch in text)
    slug = "-".join(part for part in slug.split("-") if part)
    return (slug.strip("-") or fallback)[:limit]


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(errors="replace"))


def write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True) + "\n")


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")


def task_slug(task_name: str) -> str:
    leaf = str(task_name or "").split("/")[-1]
    return leaf or "unknown-task"


def _repo_slug_from_url(value: str) -> str | None:
    text = str(value or "").strip()
    if not text:
        return None
    parsed = urlparse(text)
    path = parsed.path.strip("/")
    if path.endswith(".git"):
        path = path[:-4]
    parts = [part for part in path.split("/") if part]
    if len(parts) >= 2:
        return f"{safe_slug(parts[-2], limit=80)}__{safe_slug(parts[-1], limit=80)}"
    return None


def _deepswe_task_roots() -> list[Path]:
    roots = [
        Path("/vePFS-Mindverse/user/intern/jungao/Marcronv1-Coding/deep-swe/tasks"),
        Path(__file__).resolve().parents[1] / "deep-swe" / "tasks",
    ]
    return [root for root in roots if root.exists()]


def _deepswe_repo_slug(task_name: str) -> str | None:
    slug = task_slug(task_name)
    for root in _deepswe_task_roots():
        task_toml = root / slug / "task.toml"
        if not task_toml.exists():
            continue
        try:
            data = tomllib.loads(task_toml.read_text(encoding="utf-8"))
        except Exception:
            continue
        metadata = data.get("metadata") if isinstance(data.get("metadata"), dict) else {}
        repo = _repo_slug_from_url(str(metadata.get("repository_url") or ""))
        if repo:
            return repo
    return None


def repo_slug_from_task(task_name: str) -> str:
    leaf = task_slug(task_name)
    if "__" in leaf:
        owner, rest = leaf.split("__", 1)
        repo = rest.rsplit("-", 1)[0]
        return f"{owner}__{repo}"
    return _deepswe_repo_slug(task_name) or "unknown"


def result_reward(result: dict[str, Any]) -> float | None:
    rewards = (
        result.get("verifier_result", {}).get("rewards")
        if isinstance(result.get("verifier_result"), dict)
        else None
    )
    return first_reward_value(rewards)


def normalize_reward_condition(
    condition: dict[str, Any] | None = None,
) -> dict[str, Any]:
    raw = condition if isinstance(condition, dict) else {}
    operator = str(raw.get("operator") or "eq").strip().lower()
    if operator not in REWARD_CONDITION_OPERATORS:
        raise ValueError(
            f"Unsupported reward condition operator {operator!r}; "
            f"expected one of {', '.join(REWARD_CONDITION_OPERATORS)}"
        )
    try:
        value = float(raw.get("value", 0.0))
    except (TypeError, ValueError) as exc:
        raise ValueError(f"Invalid reward condition value: {raw.get('value')!r}") from exc
    if not math.isfinite(value):
        raise ValueError(f"Reward condition value must be finite: {value!r}")
    include_missing = bool(raw.get("include_missing", False))
    value_label = f"{value:g}"
    expression = f"reward {_REWARD_OPERATOR_SYMBOLS[operator]} {value_label}"
    if include_missing:
        expression = f"({expression}) or reward is missing/invalid"
    return {
        "operator": operator,
        "value": value,
        "include_missing": include_missing,
        "expression": expression,
    }


def reward_matches_condition(
    reward: object,
    condition: dict[str, Any] | None = None,
) -> bool:
    normalized = normalize_reward_condition(condition)
    if reward is None or isinstance(reward, bool):
        return bool(normalized["include_missing"])
    try:
        actual = float(reward)
    except (TypeError, ValueError):
        return bool(normalized["include_missing"])
    if not math.isfinite(actual):
        return bool(normalized["include_missing"])
    expected = float(normalized["value"])
    operator = normalized["operator"]
    return {
        "eq": actual == expected,
        "ne": actual != expected,
        "lt": actual < expected,
        "le": actual <= expected,
        "gt": actual > expected,
        "ge": actual >= expected,
    }[operator]


def _load_json_if_exists(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        return read_json(path)
    except json.JSONDecodeError:
        return {}


def _iter_event_lines(path: Path, *, limit: int = 2000) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    with path.open(errors="replace") as handle:
        for index, line in enumerate(handle):
            if index >= limit:
                break
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(row, dict):
                rows.append(row)
    return rows


def _extract_tool_calls_from_trajectory(path: Path) -> tuple[list[str], list[str], list[str]]:
    data = _load_json_if_exists(path)
    steps = data.get("steps") if isinstance(data.get("steps"), list) else []
    tool_names: list[str] = []
    commands: list[str] = []
    touched_paths: list[str] = []
    for step in steps:
        if not isinstance(step, dict):
            continue
        for call in step.get("tool_calls") or []:
            if not isinstance(call, dict):
                continue
            name = str(call.get("function_name") or "").strip()
            if name:
                tool_names.append(name)
            args = call.get("arguments") if isinstance(call.get("arguments"), dict) else {}
            command = str(args.get("command") or "").strip()
            if command:
                commands.append(command)
            path = str(args.get("path") or "").strip()
            if path:
                touched_paths.append(_normalize_trace_path(path))
            for edit in args.get("edits") or []:
                if isinstance(edit, dict) and path:
                    touched_paths.append(_normalize_trace_path(path))
    return _dedupe(tool_names), _dedupe(commands), _dedupe([p for p in touched_paths if p])


def _normalize_trace_path(path: str) -> str:
    text = str(path or "").strip()
    if text in {"/testbed", "/workspace", ".", "./", "/"}:
        return ""
    if text.startswith("/tmp/"):
        return ""
    for prefix in ("/testbed/", "/workspace/"):
        if text.startswith(prefix):
            text = text[len(prefix) :]
            break
    if text in {"", ".", "./", "/"} or text.startswith("tmp/"):
        return ""
    return text


def _dedupe(values: list[str], *, limit: int | None = None) -> list[str]:
    out: list[str] = []
    for value in values:
        text = str(value or "").strip()
        if text and text not in out:
            out.append(text)
            if limit is not None and len(out) >= limit:
                break
    return out


def _classify_test_commands(commands: list[str]) -> list[str]:
    test_words = ("pytest", "tox", "unittest", "manage.py test", "django test", "runtests", "npm test")
    return [
        command.replace("/testbed/", "").replace("/testbed", ".")
        for command in commands
        if any(word in command.lower() for word in test_words)
    ][:8]


def _edited_paths_from_commands(commands: list[str], touched_paths: list[str]) -> list[str]:
    edited = list(touched_paths)
    edit_markers = ("python - <<", "cat >", "apply_patch", "sed -i", "perl -pi")
    for command in commands:
        if not any(marker in command for marker in edit_markers):
            continue
        for match in re.findall(r"(?:(?:^|[\s'\"`])(?:/testbed/)?)([A-Za-z0-9_./-]+\.(?:py|js|ts|tsx|jsx|java|go|rs|c|cc|h|md|rst|txt))", command):
            edited.append(_normalize_trace_path(match))
    return _dedupe(edited, limit=12)


def _selected_skills(metadata: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    skills = metadata.get("skills")
    if not isinstance(skills, list):
        transferable = metadata.get("transferable_skills")
        skills = (
            transferable.get("selected")
            if isinstance(transferable, dict)
            else []
        )
    for skill in skills or []:
        if not isinstance(skill, dict):
            continue
        rows.append(
            {
                "name": skill.get("name"),
                "relative_path": skill.get("relative_path"),
                "quality_tier": skill.get("quality_tier"),
                "use_policy": skill.get("use_policy"),
            }
        )
    return rows


def _exception_type(result: dict[str, Any]) -> str | None:
    info = result.get("exception_info") if isinstance(result.get("exception_info"), dict) else {}
    value = info.get("exception_type") if info else None
    return str(value) if value else None


def case_label_for_evidence(entry: dict[str, Any]) -> str:
    reward = entry.get("reward")
    if entry.get("exception") or reward is None:
        return "diagnostic"
    try:
        value = float(reward)
    except (TypeError, ValueError):
        return "diagnostic"
    return "weak_positive" if value >= 1.0 else "weak_negative"


def failure_signature_for_evidence(entry: dict[str, Any]) -> str:
    exception = str(entry.get("exception") or "").strip()
    if exception:
        return f"runtime-{safe_slug(exception.lower(), fallback='diagnostic', limit=48)}"
    reward = entry.get("reward")
    if reward is None:
        return "runtime-missing-reward"
    try:
        if float(reward) >= 1.0:
            return "resolved"
    except (TypeError, ValueError):
        return "runtime-missing-reward"
    if not entry.get("edited_paths"):
        return "no-diff-recovery"
    if not entry.get("test_commands"):
        return "weak-validation"
    return "localization-drift"


def collect_failed_trace_evidence(
    *,
    aggregate_report_path: Path,
    reward_condition: dict[str, Any] | None = None,
    reward_threshold: float | None = None,
    max_evidence: int | None = None,
    benchmark_name: str = "swebench_verified",
) -> list[dict[str, Any]]:
    if reward_threshold is not None:
        if reward_condition is not None:
            raise ValueError("Use reward_condition or legacy reward_threshold, not both")
        reward_condition = {
            "operator": "lt",
            "value": reward_threshold,
            "include_missing": True,
        }
    selection_condition = normalize_reward_condition(reward_condition)
    report = read_json(aggregate_report_path)
    evidence_rows: list[dict[str, Any]] = []
    for index, row in enumerate(report.get("tasks") or [], start=1):
        reward = row.get("reward")
        if not reward_matches_condition(reward, selection_condition):
            continue
        result_path = Path(row["result_path"]).expanduser().resolve()
        result = _load_json_if_exists(result_path)
        trial_dir = result_path.parent
        agent_dir = trial_dir / "agent"
        metadata = _load_json_if_exists(agent_dir / "pi-metadata.json")
        metadata_kind = "pi"
        if not metadata:
            metadata = _load_json_if_exists(
                agent_dir / "claude-agent-metadata.json"
            )
            metadata_kind = "claude"
        tool_names, commands, touched_paths = _extract_tool_calls_from_trajectory(agent_dir / "trajectory.json")
        test_commands = _classify_test_commands(commands)
        edited_paths = _edited_paths_from_commands(commands, touched_paths)
        events_path = agent_dir / "pi-events.jsonl"
        if metadata_kind == "claude":
            events_path = agent_dir / "claude-agent-sdk.filtered.jsonl"
            if not events_path.is_file():
                events_path = agent_dir / "claude-agent-sdk.jsonl"
        events = _iter_event_lines(events_path, limit=200)
        transferable = metadata.get("transferable_skills")
        transferable = transferable if isinstance(transferable, dict) else {}
        task_name = str(row.get("task_name") or result.get("task_name") or "")
        entry = {
            "created_at": utc_now(),
            "schema_version": 1,
            "level": "task_evidence",
            "entry_id": f"tts-{index:04d}-{safe_slug(task_slug(task_name), limit=80)}",
            "source": f"{benchmark_name}_direct_skill_run",
            "benchmark": benchmark_name,
            "task_name": task_name,
            "trial_name": row.get("trial_name") or result.get("trial_name") or trial_dir.name,
            "repo": repo_slug_from_task(task_name),
            "task_slug": task_slug(task_name),
            "source_job": row.get("source_job"),
            "result_path": str(result_path),
            "trial_dir": str(trial_dir),
            "reward": reward,
            "selection_verifier_reward": reward,
            "selection_reward_condition": selection_condition,
            "selection_note": (
                "reward is used only for task selection under "
                f"{selection_condition['expression']}"
            ),
            "exception": row.get("exception_type") or _exception_type(result),
            "case_label": None,
            "failure_signature": None,
            "tools": tool_names[:20],
            "tool_call_count": len(tool_names),
            "test_commands": test_commands,
            "touched_paths": touched_paths[:12],
            "edited_paths": edited_paths,
            "selected_skills": _selected_skills(metadata),
            "skills_count": metadata.get("skills_count")
            or transferable.get("all_discovered_count"),
            "skills_source_root": metadata.get("skills_source_root")
            or transferable.get("source_root_filter"),
            "model": metadata.get("provider_model") or metadata.get("model"),
            "thinking": metadata.get("thinking"),
            "public_signal": {
                "has_diff_signal": bool(edited_paths),
                "has_test_signal": bool(test_commands),
                "has_trace_events": bool(events),
            },
            "verifier_scope": {
                "used_for_evolution": False,
                "used_for_selection": True,
                "used_for_report": True,
            },
        }
        entry["case_label"] = case_label_for_evidence(entry)
        entry["failure_signature"] = failure_signature_for_evidence(entry)
        evidence_rows.append(entry)
        if max_evidence is not None and len(evidence_rows) >= max_evidence:
            break
    return evidence_rows


def _accepted_task_entry(entry: dict[str, Any]) -> bool:
    reward = entry.get("reward")
    try:
        return (
            reward is not None
            and float(reward) >= 1.0
            and not entry.get("exception")
            and bool(entry.get("edited_paths") or entry.get("test_commands") or entry.get("touched_paths"))
        )
    except (TypeError, ValueError):
        return False


def _repo_groups(evidence_rows: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in evidence_rows:
        groups[str(row.get("repo") or "unknown")].append(row)
    return dict(groups)


def generate_test_time_decisions(
    *,
    evidence_rows: list[dict[str, Any]],
    run_name: str,
    benchmark_name: str = "swebench_verified",
    writer_policy: dict[str, Any] | None = None,
    evaluator_policy: dict[str, Any] | None = None,
    repo_update_batch_size: int = 5,
    repo_min_support: int = 2,
    repo_min_positive_support: int = 2,
    failure_mode_min_repo_support: int = 2,
    max_repo_skills_per_gate: int = 12,
    max_failure_skills_per_gate: int = 12,
) -> dict[str, Any]:
    repo_clusters: list[dict[str, Any]] = []
    failure_clusters: list[dict[str, Any]] = []
    repo_candidates: list[dict[str, Any]] = []
    failure_candidates: list[dict[str, Any]] = []
    evaluator_decisions: list[dict[str, Any]] = []
    calibration_events: list[dict[str, Any]] = []
    promotion_decisions: list[dict[str, Any]] = []
    failure_mode_pool: dict[str, dict[str, list[dict[str, Any]]]] = defaultdict(lambda: defaultdict(list))
    repo_update_count = 0

    for repo, entries in sorted(_repo_groups(evidence_rows).items()):
        if len(entries) < repo_min_support:
            continue
        for start in range(0, len(entries), repo_update_batch_size):
            batch = entries[start : start + repo_update_batch_size]
            if len(batch) < repo_min_support:
                continue
            repo_update_count += 1
            case_counts = Counter(str(entry.get("case_label") or case_label_for_evidence(entry)) for entry in batch)
            accepted_entries = [entry for entry in batch if _accepted_task_entry(entry)]
            failure_counts = Counter(
                str(entry.get("failure_signature") or failure_signature_for_evidence(entry))
                for entry in batch
                if str(entry.get("failure_signature") or failure_signature_for_evidence(entry)) != "resolved"
                and str(entry.get("case_label") or "") != "diagnostic"
            )
            diagnostic_counts = Counter(
                str(entry.get("failure_signature") or failure_signature_for_evidence(entry))
                for entry in batch
                if str(entry.get("case_label") or "") == "diagnostic"
            )
            cluster = build_repo_cluster(
                repo=repo,
                entries=batch,
                update_index=repo_update_count,
                case_counts=dict(case_counts),
                failure_signature_counts=dict(failure_counts),
                diagnostic_signature_counts=dict(diagnostic_counts),
                positive_entries=accepted_entries,
            )
            cluster["source"] = "test_time_benchmark_failed_traces"
            cluster["verifier_access"] = False
            repo_clusters.append(cluster)
            candidate = write_repo_candidate(cluster, writer_policy=writer_policy)
            candidate["source"] = "test_time_writer"
            candidate["benchmark"] = benchmark_name
            candidate["skill_polarity"] = "mixed"
            repo_candidates.append(candidate)
            evaluator_decision = evaluate_candidate(
                candidate=candidate,
                evidence=cluster,
                evaluator_context={
                    "source": "test_time_evaluator",
                    "verifier_access": False,
                    "promotion_source": "evaluator_only",
                    "case_counts": dict(case_counts),
                    "positive_support": len(accepted_entries),
                },
                evaluator_policy=evaluator_policy,
                min_repo_positive_support=repo_min_positive_support,
                min_failure_repo_support=failure_mode_min_repo_support,
            )
            evaluator_decisions.append(evaluator_decision)
            calibration_events.append(
                calibration_event(
                    candidate=candidate,
                    decision=evaluator_decision,
                    evaluator_context={
                        "source": "test_time_evaluator",
                        "verifier_access": False,
                    },
                )
            )
            accepted = evaluator_decision.get("decision") == "accept"
            promotion_decisions.append(
                {
                    "created_at": utc_now(),
                    "schema_version": 1,
                    "level": "repo",
                    "source": "test_time_benchmark_failed_traces",
                    "run_name": run_name,
                    "repo": repo,
                    "update_index": repo_update_count,
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
                    "reason": evaluator_decision.get("reason"),
                    "verifier_access": False,
                    "promotion_source": "evaluator_only",
                }
            )
            for signature, count in failure_counts.items():
                if not signature:
                    continue
                cluster_copy = dict(cluster)
                cluster_copy["failure_signature_event_count"] = count
                failure_mode_pool[str(signature)][repo].append(cluster_copy)

    failure_trigger_index = 1
    for signature, clusters_by_repo in sorted(failure_mode_pool.items()):
        repo_clusters_for_signature = [
            cluster
            for clusters in clusters_by_repo.values()
            for cluster in clusters
        ]
        failure_cluster = build_failure_cluster(
            signature=signature,
            repo_clusters=repo_clusters_for_signature,
            trigger_index=failure_trigger_index,
        )
        failure_cluster["source"] = "test_time_benchmark_failed_traces"
        failure_cluster["verifier_access"] = False
        failure_clusters.append(failure_cluster)
        candidate = write_failure_mode_candidate(
            failure_cluster,
            writer_policy=writer_policy,
        )
        candidate["source"] = "test_time_writer"
        candidate["benchmark"] = benchmark_name
        candidate["skill_polarity"] = "negative"
        failure_candidates.append(candidate)
        evaluator_decision = evaluate_candidate(
            candidate=candidate,
            evidence=failure_cluster,
            evaluator_context={
                "source": "test_time_evaluator",
                "verifier_access": False,
                "promotion_source": "evaluator_only",
                "repo_support_count": failure_cluster.get("repo_support_count"),
            },
            evaluator_policy=evaluator_policy,
            min_repo_positive_support=repo_min_positive_support,
            min_failure_repo_support=failure_mode_min_repo_support,
        )
        evaluator_decisions.append(evaluator_decision)
        calibration_events.append(
            calibration_event(
                candidate=candidate,
                decision=evaluator_decision,
                evaluator_context={
                    "source": "test_time_evaluator",
                    "verifier_access": False,
                },
            )
        )
        accepted = evaluator_decision.get("decision") == "accept"
        promotion_decisions.append(
            {
                "created_at": utc_now(),
                "schema_version": 1,
                "level": "failure_mode",
                "source": "test_time_benchmark_failed_traces",
                "run_name": run_name,
                "trigger_index": failure_trigger_index,
                "failure_signature": signature,
                "repo_support_count": failure_cluster.get("repo_support_count"),
                "event_support_count": failure_cluster.get("event_support_count"),
                "support_repos": failure_cluster.get("support_repos") or [],
                "failure_cluster": failure_cluster,
                "candidate": candidate,
                "evaluator_decision": evaluator_decision,
                "decision": "promote" if accepted else "stage",
                "candidate_decision": evaluator_decision.get("decision"),
                "reason": evaluator_decision.get("reason"),
                "verifier_access": False,
                "promotion_source": "evaluator_only",
            }
        )

    apply_gate_promotion_policy(
        promotion_decisions,
        max_repo_skills=max_repo_skills_per_gate,
        max_failure_skills=max_failure_skills_per_gate,
    )

    return {
        "repo_clusters": repo_clusters,
        "failure_clusters": failure_clusters,
        "repo_candidates": repo_candidates,
        "failure_candidates": failure_candidates,
        "evaluator_decisions": evaluator_decisions,
        "evaluator_calibration": calibration_events,
        "promotion_decisions": promotion_decisions,
    }


def _promotion_key(decision: dict[str, Any]) -> tuple[str, str, str]:
    candidate = decision.get("candidate") if isinstance(decision.get("candidate"), dict) else {}
    level = str(decision.get("level") or candidate.get("level") or "candidate")
    name = str(candidate.get("name") or "")
    if level == "repo":
        return (level, str(decision.get("repo") or candidate.get("repo") or "unknown"), name)
    if level == "failure_mode":
        return (
            level,
            str(decision.get("failure_signature") or candidate.get("failure_signature") or "unknown"),
            name,
        )
    return (level, name, "")


def _decision_score(decision: dict[str, Any]) -> tuple[float, int, int, str]:
    evaluator = decision.get("evaluator_decision") if isinstance(decision.get("evaluator_decision"), dict) else {}
    try:
        proxy = float(evaluator.get("proxy_reward") or 0.0)
    except (TypeError, ValueError):
        proxy = 0.0
    support = _decision_support_count(decision)
    try:
        index = int(decision.get("update_index") or decision.get("trigger_index") or 0)
    except (TypeError, ValueError):
        index = 0
    return (proxy, support, index, str(decision.get("created_at") or ""))


def apply_gate_promotion_policy(
    promotion_decisions: list[dict[str, Any]],
    *,
    max_repo_skills: int,
    max_failure_skills: int,
) -> None:
    """Promote at most one strongest accepted candidate per reusable skill key."""

    accepted_by_key: dict[tuple[str, str, str], dict[str, Any]] = {}
    for decision in promotion_decisions:
        if decision.get("candidate_decision") != "accept":
            if decision.get("decision") == "promote":
                decision["decision"] = "stage"
            continue
        key = _promotion_key(decision)
        previous = accepted_by_key.get(key)
        if previous is None or _decision_score(decision) >= _decision_score(previous):
            accepted_by_key[key] = decision

    repo_selected = sorted(
        [
            decision
            for key, decision in accepted_by_key.items()
            if key[0] == "repo"
        ],
        key=_decision_score,
        reverse=True,
    )[: max(0, max_repo_skills)]
    failure_selected = sorted(
        [
            decision
            for key, decision in accepted_by_key.items()
            if key[0] == "failure_mode"
        ],
        key=_decision_score,
        reverse=True,
    )[: max(0, max_failure_skills)]
    selected_ids = {id(decision) for decision in repo_selected + failure_selected}
    selected_keys = {_promotion_key(decision) for decision in repo_selected + failure_selected}

    for decision in promotion_decisions:
        if decision.get("candidate_decision") != "accept":
            decision["decision"] = "stage"
            continue
        key = _promotion_key(decision)
        if id(decision) in selected_ids:
            decision["decision"] = "promote"
            decision["promotion_key"] = list(key)
            continue
        decision["decision"] = "stage"
        decision["promotion_key"] = list(key)
        if key in selected_keys:
            decision["reason"] = "superseded by stronger accepted candidate with the same reusable skill key"
            decision["superseded"] = True
        else:
            decision["reason"] = "gate promotion budget exhausted"
            decision["budget_exhausted"] = True


def _yaml_scalar(value: object) -> str:
    return json.dumps(str(value), ensure_ascii=True)


def _decision_support_count(decision: dict[str, Any]) -> int:
    level = str(decision.get("level") or "")
    if level == "repo":
        for key in ("batch_size", "support_tasks"):
            try:
                value = decision.get(key)
                if value is not None:
                    return int(value)
            except (TypeError, ValueError):
                pass
        cluster = decision.get("repo_cluster") if isinstance(decision.get("repo_cluster"), dict) else {}
        try:
            return int(cluster.get("support_tasks") or 0)
        except (TypeError, ValueError):
            return 0
    for key in ("repo_support_count", "positive_task_evidence_count", "event_support_count"):
        try:
            value = decision.get(key)
            if value is not None:
                return int(value)
        except (TypeError, ValueError):
            pass
    candidate = decision.get("candidate") if isinstance(decision.get("candidate"), dict) else {}
    cluster = candidate.get("source_cluster") if isinstance(candidate.get("source_cluster"), dict) else {}
    try:
        return int(cluster.get("repo_support_count") or cluster.get("positive_support") or cluster.get("support_tasks") or 0)
    except (TypeError, ValueError):
        return 0


def render_test_time_skill(*, decision: dict[str, Any], run_name: str) -> str:
    candidate = decision.get("candidate") if isinstance(decision.get("candidate"), dict) else {}
    evaluator = decision.get("evaluator_decision") if isinstance(decision.get("evaluator_decision"), dict) else {}
    level = str(decision.get("level") or candidate.get("level") or "candidate")
    name = str(candidate.get("name") or f"{level}-test-time-skill")
    support = _decision_support_count(decision)
    support_repos = [str(repo) for repo in (decision.get("support_repos") or []) if str(repo).strip()]
    if level == "repo":
        repo = str(decision.get("repo") or candidate.get("repo") or "unknown")
        description = f"Test-time repo skill distilled from evaluator-approved benchmark evidence for {repo}."
        quality_tier = "repo"
        skill_type = "repo"
        use_policy = "evidence-gated"
        status = "test_time_promoted"
        gate_title = "Evidence Gate"
    else:
        signature = str(decision.get("failure_signature") or candidate.get("failure_signature") or "unknown")
        description = f"Test-time failure-mode recovery skill distilled from evaluator-approved benchmark evidence for {signature}."
        quality_tier = "failure_mode"
        skill_type = "failure_mode"
        use_policy = "negative-evidence-gated"
        status = "test_time_promoted"
        gate_title = "Negative Evidence Gate"
    quality = evaluator.get("proxy_reward")
    try:
        quality_value = float(quality)
    except (TypeError, ValueError):
        quality_value = min(0.88, 0.55 + 0.04 * min(support, 6))
    actions = [str(action) for action in (candidate.get("actions") or []) if str(action).strip()]
    if not actions:
        actions = [
            "Reconstruct the smallest current-task symptom from public evidence.",
            "Use this skill only after the current issue, path, traceback, or validation signal matches.",
            "Stop and re-localize if the current evidence diverges from the support summary.",
        ]
    lines = [
        "---",
        f"name: {name}",
        f"description: {description}",
        "active: true",
        f"quality_score: {quality_value:.2f}",
        f"quality_tier: {quality_tier}",
        "risk_flags: []",
        f"use_policy: {use_policy}",
        f"level: {level}",
        f"status: {status}",
        f"skill_type: {_yaml_scalar(skill_type)}",
        f"support_count: {support}",
        "feedback_scope: test_time_evaluator_only",
        "verifier_access: false",
        "---",
        "",
        f"# {name}",
        "",
        "## Status",
        "",
        f"- Run: `{run_name}`",
        f"- Status: `{status}`",
        "- Source: test-time benchmark trace evidence",
        "- Promotion source: evaluator only; hidden verifier is not used for evolution.",
        f"- Evaluator decision: `{evaluator.get('decision') or decision.get('candidate_decision')}`",
        f"- Proxy reward: `{evaluator.get('proxy_reward')}`",
        f"- Reason: {decision.get('reason') or evaluator.get('reason') or 'accepted by evaluator'}",
        f"- Support: `{support}`",
        f"- Support repos: {', '.join(support_repos) if support_repos else str(decision.get('repo') or 'none')}",
        "",
        "## Trigger",
        "",
        str(candidate.get("trigger") or "Use only when the current public trace matches this skill."),
        "",
        f"## {gate_title}",
        "",
        str(candidate.get("evidence_gate") or "Require fresh current-task evidence before applying this skill."),
        "",
        "## Actions",
        "",
        *[f"{index}. {action}" for index, action in enumerate(actions, start=1)],
        "",
        "## Do Not",
        "",
        "1. Do not copy source-task patch shape or hidden benchmark information.",
        "2. Do not use this skill if current issue text, path, traceback, or validation signal does not match.",
        "3. Do not treat evaluator acceptance as verifier success.",
        "",
        "## Stop Condition",
        "",
        str(
            candidate.get("stop_condition")
            or candidate.get("abort_condition")
            or "Stop using this skill when the current trace no longer matches."
        ),
        "",
        "## Support Summary",
        "",
        str(candidate.get("support_summary") or ""),
        "",
    ]
    return "\n".join(lines)


def materialize_gate_library(
    *,
    base_skill_root: Path,
    output_root: Path,
    run_name: str,
    promotion_decisions: list[dict[str, Any]],
    gate_index: int,
    include_base: bool = True,
    clean: bool = True,
) -> dict[str, Any]:
    if clean and output_root.exists():
        shutil.rmtree(output_root)
    output_root.mkdir(parents=True, exist_ok=True)
    copied_base = False
    if include_base and base_skill_root.exists():
        for child in base_skill_root.iterdir():
            target = output_root / child.name
            if child.is_dir():
                shutil.copytree(child, target)
            else:
                shutil.copy2(child, target)
        copied_base = True

    skill_rows: list[dict[str, Any]] = []
    added_count = 0
    updated_count = 0
    for decision in promotion_decisions:
        if decision.get("decision") != "promote":
            continue
        candidate = decision.get("candidate") if isinstance(decision.get("candidate"), dict) else {}
        name = str(candidate.get("name") or f"{decision.get('level')}-test-time-skill")
        level = str(decision.get("level") or candidate.get("level") or "candidate")
        if level == "repo":
            repo = safe_slug(decision.get("repo") or candidate.get("repo") or "unknown")
            skill_dir = output_root / "_test_time" / "repo" / repo / safe_slug(name)
        elif level == "failure_mode":
            signature = safe_slug(decision.get("failure_signature") or candidate.get("failure_signature") or "unknown")
            skill_dir = output_root / "_test_time" / "failure_modes" / signature / safe_slug(name)
        else:
            skill_dir = output_root / "_test_time" / safe_slug(level) / safe_slug(name)
        skill_dir.mkdir(parents=True, exist_ok=True)
        skill_path = skill_dir / "SKILL.md"
        if skill_path.is_file():
            updated_count += 1
        else:
            added_count += 1
        skill_path.write_text(render_test_time_skill(decision=decision, run_name=run_name))
        decision["skill_path"] = str(skill_path)
        skill_rows.append(
            {
                "name": name,
                "level": level,
                "decision": decision.get("decision"),
                "candidate_decision": decision.get("candidate_decision"),
                "reason": decision.get("reason"),
                "repo": decision.get("repo"),
                "failure_signature": decision.get("failure_signature"),
                "support_count": _decision_support_count(decision),
                "skill_path": str(skill_path.relative_to(output_root)),
            }
        )

    base_count = len(list(base_skill_root.rglob("SKILL.md"))) if copied_base else 0
    actual_total = len(list(output_root.rglob("SKILL.md")))
    actual_test_time_total = len(
        list((output_root / "_test_time").rglob("SKILL.md"))
    )
    manifest = {
        "schema_version": 1,
        "kind": "test_time_skill_evolution_gate_library",
        "run_name": run_name,
        "gate_index": gate_index,
        "created_at": utc_now(),
        "base_skill_root": str(base_skill_root),
        "output_root": str(output_root),
        "include_base": include_base,
        "copied_base": copied_base,
        "promotion_source": "evaluator_only",
        "verifier_access_for_evolution": False,
        "verifier_report": {
            "status": "not_run",
            "note": "Verifier metrics may be attached after this frozen gate is evaluated; they are report-only.",
        },
        "skill_counts": {
            "base": base_count,
            "test_time_promoted": len(skill_rows),
            "added_this_gate": added_count,
            "updated_this_gate": updated_count,
            "test_time_total": actual_test_time_total,
            "total": actual_total,
        },
        "skills": skill_rows,
    }
    write_json(output_root / "gate_library_manifest.json", manifest)
    return manifest
