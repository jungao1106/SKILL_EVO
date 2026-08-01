#!/usr/bin/env python3
"""Materialize repo/failure-mode candidates and promoted decisions as SKILL.md files."""

from __future__ import annotations

import argparse
import json
import shutil
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open() as handle:
        for line in handle:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def slug(value: object, fallback: str = "unknown") -> str:
    text = str(value or fallback)
    safe = "".join(ch if ch.isalnum() or ch in "._-" else "-" for ch in text)
    safe = "-".join(part for part in safe.split("-") if part)
    return safe.strip("-") or fallback


def yaml_value(value: object) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    return json.dumps(str(value), ensure_ascii=True)


def support_info(decision: dict[str, Any]) -> tuple[int, int, str]:
    candidate = decision.get("candidate") if isinstance(decision.get("candidate"), dict) else {}
    cluster = candidate.get("source_cluster") if isinstance(candidate.get("source_cluster"), dict) else {}
    level = str(decision.get("level") or candidate.get("level") or "candidate")
    if level == "failure_mode":
        raw = decision.get("repo_support_count")
        if raw is None:
            raw = cluster.get("repo_support_count")
        if raw is None:
            raw = len(cluster.get("support_repos") or [])
        unit = "repos"
    elif level == "repo":
        raw = decision.get("positive_task_evidence_count")
        if raw is None:
            raw = cluster.get("positive_support")
        unit = "verifier_positive_task_events"
    else:
        raw = cluster.get("support_tasks") or 0
        unit = "events"
    try:
        count = int(raw or 0)
    except (TypeError, ValueError):
        count = 0
    bucket = 3 if count >= 3 else count
    return count, bucket, unit


def selected_metadata(decision: dict[str, Any]) -> dict[str, Any]:
    candidate = decision.get("candidate") if isinstance(decision.get("candidate"), dict) else {}
    evaluator = decision.get("evaluator_decision") if isinstance(decision.get("evaluator_decision"), dict) else {}
    cluster = candidate.get("source_cluster") if isinstance(candidate.get("source_cluster"), dict) else {}
    support_count, support_bucket, support_unit = support_info(decision)
    return {
        "created_at": decision.get("created_at") or candidate.get("created_at"),
        "level": decision.get("level") or candidate.get("level"),
        "name": candidate.get("name"),
        "repo": decision.get("repo") or candidate.get("repo"),
        "failure_signature": decision.get("failure_signature") or candidate.get("failure_signature"),
        "decision": decision.get("decision"),
        "candidate_decision": decision.get("candidate_decision") or evaluator.get("decision"),
        "reason": decision.get("reason") or evaluator.get("reason"),
        "confidence": evaluator.get("confidence"),
        "proxy_reward": evaluator.get("proxy_reward"),
        "support_count": support_count,
        "support_bucket": support_bucket,
        "support_unit": support_unit,
        "event_support_count": decision.get("event_support_count") or cluster.get("event_support_count"),
        "support_repos": decision.get("support_repos") or cluster.get("support_repos"),
        "official_skill_path": decision.get("skill_path"),
    }


def render_skill(
    *,
    decision: dict[str, Any],
    run_name: str,
    source_index: int,
    status: str,
) -> str:
    candidate = decision.get("candidate") if isinstance(decision.get("candidate"), dict) else {}
    evaluator = decision.get("evaluator_decision") if isinstance(decision.get("evaluator_decision"), dict) else {}
    meta = selected_metadata(decision)
    level = str(meta.get("level") or "candidate")
    name = str(meta.get("name") or f"{level}-skill")
    active = status == "promoted"
    description = (
        f"{status} {level} skill materialized from training candidate evidence."
        if status != "promoted"
        else f"promoted {level} skill materialized from verifier-gated training evidence."
    )
    frontmatter = {
        "name": name,
        "description": description,
        "active": active,
        "status": status,
        "level": level,
        "decision": meta.get("decision"),
        "candidate_decision": meta.get("candidate_decision"),
        "support_bucket": meta.get("support_bucket"),
        "support_count": meta.get("support_count"),
        "support_unit": meta.get("support_unit"),
        "run": run_name,
        "source_decision_index": source_index,
        "repo": meta.get("repo"),
        "failure_signature": meta.get("failure_signature"),
        "confidence": meta.get("confidence"),
        "proxy_reward": meta.get("proxy_reward"),
        "official_skill_path": meta.get("official_skill_path"),
    }
    lines = ["---"]
    for key, value in frontmatter.items():
        if value is not None:
            lines.append(f"{key}: {yaml_value(value)}")
    lines.extend(
        [
            "---",
            "",
            f"# {name}",
            "",
            "## Status",
            "",
            f"- Status: `{status}`",
            f"- Decision: `{meta.get('decision')}`",
            f"- Candidate decision: `{meta.get('candidate_decision')}`",
            f"- Support: `{meta.get('support_count')}` `{meta.get('support_unit')}`; bucket `support_{meta.get('support_bucket')}`",
        ]
    )
    if meta.get("reason"):
        lines.append(f"- Reason: {meta.get('reason')}")
    if meta.get("support_repos"):
        lines.append(f"- Support repos: {', '.join(str(repo) for repo in meta.get('support_repos') or [])}")
    if meta.get("event_support_count") is not None:
        lines.append(f"- Event support count: `{meta.get('event_support_count')}`")
    if meta.get("official_skill_path"):
        lines.append(f"- Official promoted path: `{meta.get('official_skill_path')}`")

    sections = [
        ("Trigger", candidate.get("trigger")),
        ("Evidence Gate", candidate.get("evidence_gate") or "Use only when current public evidence matches the support summary."),
        ("Actions", candidate.get("actions") or []),
        ("Validation Hint", candidate.get("validation_hint")),
        ("Stop Condition", candidate.get("stop_condition") or candidate.get("abort_condition")),
        ("Support Summary", candidate.get("support_summary")),
        ("Evaluator Summary", evaluator.get("reason")),
    ]
    for title, value in sections:
        if not value:
            continue
        lines.extend(["", f"## {title}", ""])
        if isinstance(value, list):
            lines.extend(f"{index}. {item}" for index, item in enumerate(value, start=1))
        else:
            lines.append(str(value))
    lines.append("")
    return "\n".join(lines)


def skill_path_for(
    *,
    root: Path,
    decision: dict[str, Any],
    source_index: int,
    status: str,
) -> Path:
    candidate = decision.get("candidate") if isinstance(decision.get("candidate"), dict) else {}
    meta = selected_metadata(decision)
    level = slug(meta.get("level") or "candidate")
    name = slug(meta.get("name") or f"{level}-skill")
    support_bucket = meta.get("support_bucket")
    support_dir = f"support_{support_bucket}"
    prefix = f"decision_{source_index:06d}"
    if level == "failure_mode":
        signature = slug(meta.get("failure_signature") or candidate.get("failure_signature") or "unknown")
        return root / status / support_dir / level / signature / f"{prefix}__{name}" / "SKILL.md"
    if level == "repo":
        repo = slug(meta.get("repo") or candidate.get("repo") or "unknown")
        return root / status / support_dir / level / repo / f"{prefix}__{name}" / "SKILL.md"
    return root / status / support_dir / level / f"{prefix}__{name}" / "SKILL.md"


def write_summary(
    *,
    output_root: Path,
    run_name: str,
    records: list[dict[str, Any]],
) -> None:
    by_status_bucket = Counter((row["status"], row["support_bucket"]) for row in records)
    by_status_level = Counter((row["status"], row["level"]) for row in records)
    by_status_decision = Counter((row["status"], row["decision"], row["candidate_decision"]) for row in records)
    by_failure_candidate_signature: dict[str, Counter[int]] = defaultdict(Counter)
    by_failure_promoted_signature: dict[str, Counter[int]] = defaultdict(Counter)
    for row in records:
        if row.get("level") != "failure_mode":
            continue
        signature = str(row.get("failure_signature") or "unknown")
        bucket = int(row["support_bucket"])
        if row.get("status") == "candidate":
            by_failure_candidate_signature[signature][bucket] += 1
        elif row.get("status") == "promoted":
            by_failure_promoted_signature[signature][bucket] += 1

    summary = {
        "run_name": run_name,
        "total_skills": len(records),
        "candidate_skills": sum(1 for row in records if row.get("status") == "candidate"),
        "promoted_skills": sum(1 for row in records if row.get("status") == "promoted"),
        "counts_by_status_bucket": {
            f"{status}/support_{bucket}": count
            for (status, bucket), count in sorted(by_status_bucket.items())
        },
        "counts_by_status_level": {
            f"{status}/{level}": count
            for (status, level), count in sorted(by_status_level.items())
        },
        "counts_by_status_decision": {
            f"{status}/{decision}/{candidate_decision}": count
            for (status, decision, candidate_decision), count in sorted(by_status_decision.items())
        },
        "failure_mode_candidate_buckets": {
            signature: {f"support_{bucket}": count for bucket, count in sorted(counter.items())}
            for signature, counter in sorted(by_failure_candidate_signature.items())
        },
        "failure_mode_promoted_buckets": {
            signature: {f"support_{bucket}": count for bucket, count in sorted(counter.items())}
            for signature, counter in sorted(by_failure_promoted_signature.items())
        },
    }
    (output_root / "summary.json").write_text(json.dumps(summary, indent=2, ensure_ascii=False) + "\n")

    readme_lines = [
        f"# Materialized candidate/promoted skills for `{run_name}`",
        "",
        "This directory is an analysis skill pack. It does not replace `skills/accepted/<version>`.",
        "",
        "- `candidates/`: every repo/failure-mode candidate decision rendered as a `SKILL.md`.",
        "- `promoted/`: the subset whose final decision was `promote`, also rendered as `SKILL.md`.",
        "- `support_1`, `support_2`, `support_3`: support buckets; `support_3` means three or more supports.",
        "- `support_0`: repo candidates with zero verifier-positive task support; kept separate from the requested 1/2/3 buckets.",
        "- For failure-mode skills, support means distinct supporting repos.",
        "- For repo skills, support means verifier-positive task evidence count.",
        "",
        "## Counts",
        "",
    ]
    for key, value in summary["counts_by_status_bucket"].items():
        readme_lines.append(f"- `{key}`: {value}")
    readme_lines.extend(["", "## Failure-Mode Candidate Buckets", ""])
    for signature, buckets in summary["failure_mode_candidate_buckets"].items():
        bucket_text = ", ".join(f"{bucket}={count}" for bucket, count in buckets.items())
        readme_lines.append(f"- `{signature}`: {bucket_text}")
    readme_lines.extend(["", "## Promoted Failure-Mode Buckets", ""])
    for signature, buckets in summary["failure_mode_promoted_buckets"].items():
        bucket_text = ", ".join(f"{bucket}={count}" for bucket, count in buckets.items())
        readme_lines.append(f"- `{signature}`: {bucket_text}")
    readme_lines.append("")
    (output_root / "README.md").write_text("\n".join(readme_lines))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-dir", required=True, type=Path)
    parser.add_argument("--output-root", type=Path)
    parser.add_argument("--clean", action="store_true")
    args = parser.parse_args()

    run_dir = args.run_dir.resolve()
    run_name = run_dir.name
    output_root = args.output_root or Path("skills") / "organized" / run_name
    output_root = output_root.resolve()
    promotion_path = run_dir / "training" / "promotion_decisions.jsonl"
    if not promotion_path.exists():
        raise SystemExit(f"missing promotion decisions: {promotion_path}")
    if output_root.exists():
        if not args.clean:
            raise SystemExit(f"output exists; pass --clean to overwrite: {output_root}")
        shutil.rmtree(output_root)
    output_root.mkdir(parents=True, exist_ok=True)

    decisions = read_jsonl(promotion_path)
    manifest_rows: list[dict[str, Any]] = []
    for index, decision in enumerate(decisions, start=1):
        if not isinstance(decision.get("candidate"), dict):
            continue
        for status in ("candidates", "promoted"):
            if status == "promoted" and decision.get("decision") != "promote":
                continue
            skill_path = skill_path_for(root=output_root, decision=decision, source_index=index, status=status)
            skill_path.parent.mkdir(parents=True, exist_ok=True)
            skill_path.write_text(
                render_skill(
                    decision=decision,
                    run_name=run_name,
                    source_index=index,
                    status="candidate" if status == "candidates" else "promoted",
                )
            )
            meta = selected_metadata(decision)
            meta.update(
                {
                    "status": "candidate" if status == "candidates" else "promoted",
                    "source_decision_index": index,
                    "skill_path": str(skill_path.relative_to(output_root)),
                }
            )
            manifest_rows.append(meta)

    manifest_path = output_root / "manifest.jsonl"
    manifest_path.write_text("\n".join(json.dumps(row, ensure_ascii=False) for row in manifest_rows) + "\n")
    write_summary(output_root=output_root, run_name=run_name, records=manifest_rows)
    print(f"wrote {len(manifest_rows)} skills to {output_root}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
