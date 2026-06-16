#!/usr/bin/env python
import argparse
import json
import re
import shutil
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SKILL_ROOT = ROOT / "skills" / "tasks"
DEFAULT_CASES_ROOT = ROOT / "run_logs" / "policy_updates"
STAGES = ("reproduce", "localize", "edit", "validate", "recover")
POLICY_BLOCK_BEGIN = "<!-- skill-evo-stage-update:start -->"
POLICY_BLOCK_END = "<!-- skill-evo-stage-update:end -->"
REWARD_AGENT_BLOCK_BEGIN = "<!-- reward-agent-stage-update:start -->"
REWARD_AGENT_BLOCK_END = "<!-- reward-agent-stage-update:end -->"


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open() as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            rows.append(json.loads(line))
    return rows


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


def find_task_dir(version_root: Path, task_name: str) -> Path | None:
    task_slug = task_slug_from_name(task_name)
    if not task_slug:
        return None
    repo = repo_from_task_slug(task_slug)
    candidates = sorted((version_root / repo).glob(f"{task_slug}__*"))
    if not candidates:
        candidates = sorted(version_root.glob(f"*/{task_slug}__*"))
    return candidates[0] if candidates else None


def copy_tree(src: Path, dst: Path) -> None:
    if dst.exists():
        shutil.rmtree(dst)
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(src, dst)


def stage_skill_paths(task_dir: Path, stages: list[str]) -> list[Path]:
    paths: list[Path] = []
    for stage in stages:
        stage_dir = task_dir / stage
        if not stage_dir.exists():
            continue
        paths.extend(sorted(stage_dir.glob("**/SKILL.md")))
    return paths


def strip_legacy_policy_sections(text: str) -> str:
    headings = (
        "Policy Iteration Preserve Note",
        "Policy Iteration Strong Negative Guard",
        "Policy Iteration Weak Negative Guard",
    )
    for heading in headings:
        pattern = re.compile(
            rf"\n## {re.escape(heading)}\n\n.*?(?=\n## |\n<!-- skill-evo-stage-update:start -->|\Z)",
            flags=re.DOTALL,
        )
        text = pattern.sub("", text)
    return text


def strip_reward_agent_sections(text: str) -> str:
    pattern = re.compile(
        re.escape(REWARD_AGENT_BLOCK_BEGIN) + r".*?" + re.escape(REWARD_AGENT_BLOCK_END),
        flags=re.DOTALL,
    )
    return pattern.sub("", text)


def clean_legacy_policy_sections(root: Path) -> int:
    cleaned = 0
    for skill_path in sorted(root.glob("*/*/*/*/SKILL.md")):
        text = skill_path.read_text(errors="replace")
        updated = strip_reward_agent_sections(strip_legacy_policy_sections(text)).rstrip() + "\n"
        if updated != text:
            skill_path.write_text(updated)
            cleaned += 1
    return cleaned


def replace_or_append_stage_block(skill_path: Path, *, heading: str, lines: list[str]) -> bool:
    text = skill_path.read_text(errors="replace").rstrip()
    block = "\n".join([POLICY_BLOCK_BEGIN, heading, "", *lines, POLICY_BLOCK_END])
    pattern = re.compile(
        re.escape(POLICY_BLOCK_BEGIN) + r".*?" + re.escape(POLICY_BLOCK_END),
        flags=re.DOTALL,
    )
    cleaned = pattern.sub("", text)
    cleaned = strip_legacy_policy_sections(cleaned).rstrip()
    updated = cleaned + "\n\n" + block
    if updated.rstrip() == text.rstrip():
        return False
    skill_path.write_text(updated.rstrip() + "\n")
    return True


def update_stage_skills(task_dir: Path, *, stages: list[str], heading: str, lines: list[str]) -> dict[str, Any]:
    edited_paths: list[str] = []
    for skill_path in stage_skill_paths(task_dir, stages):
        if replace_or_append_stage_block(skill_path, heading=heading, lines=lines):
            edited_paths.append(str(skill_path))
    return {
        "edited_files": len(edited_paths),
        "edited_paths": edited_paths,
        "stages": stages,
    }


def replace_or_append_reward_agent_block(skill_path: Path, *, heading: str, lines: list[str]) -> bool:
    text = skill_path.read_text(errors="replace").rstrip()
    block = "\n".join([REWARD_AGENT_BLOCK_BEGIN, heading, "", *lines, REWARD_AGENT_BLOCK_END])
    cleaned = strip_reward_agent_sections(text).rstrip()
    updated = cleaned + "\n\n" + block
    if updated.rstrip() == text.rstrip():
        return False
    skill_path.write_text(updated.rstrip() + "\n")
    return True


def upsert_frontmatter_fields(text: str, fields: dict[str, Any]) -> str:
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        frontmatter = ["---"]
        for key, value in fields.items():
            frontmatter.append(f"{key}: {frontmatter_value(value)}")
        frontmatter.append("---")
        return "\n".join(frontmatter + [""] + lines).rstrip() + "\n"
    end = None
    for index, line in enumerate(lines[1:], start=1):
        if line.strip() == "---":
            end = index
            break
    if end is None:
        return text
    existing = lines[1:end]
    field_keys = set(fields)
    updated: list[str] = []
    seen: set[str] = set()
    for line in existing:
        key = line.split(":", 1)[0].strip() if ":" in line else ""
        if key in field_keys:
            updated.append(f"{key}: {frontmatter_value(fields[key])}")
            seen.add(key)
        else:
            updated.append(line)
    for key, value in fields.items():
        if key not in seen:
            updated.append(f"{key}: {frontmatter_value(value)}")
    return "\n".join(["---", *updated, "---", *lines[end + 1 :]]).rstrip() + "\n"


def frontmatter_value(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (list, dict)):
        return json.dumps(value, ensure_ascii=False)
    if isinstance(value, (int, float)):
        return str(value)
    return json.dumps(str(value), ensure_ascii=False)


def update_skill_activation(skill_path: Path, *, feedback: dict[str, Any]) -> bool:
    activation = str(feedback.get("activation_policy") or "")
    action = str(feedback.get("skill_action") or "")
    if activation not in {
        "inactive_memory_only",
        "diagnostic_memory_only",
        "active_evidence_gated",
    }:
        return False
    text = skill_path.read_text(errors="replace")
    if activation in {"inactive_memory_only", "diagnostic_memory_only"}:
        fields = {
            "active": False,
            "use_policy": "memory-only",
            "risk_flags": sorted(
                set(["reward_agent_demoted", action])
                | {str(item) for item in (feedback.get("must_remove") or [])}
            ),
        }
    else:
        if not feedback.get("must_preserve"):
            return False
        fields = {
            "active": True,
            "use_policy": "evidence-gated",
        }
    updated = upsert_frontmatter_fields(text, fields)
    if updated == text:
        return False
    skill_path.write_text(updated)
    return True


def update_stage_skills_from_reward_feedback(
    task_dir: Path,
    *,
    stages: list[str],
    heading: str,
    lines: list[str],
    feedback: dict[str, Any],
) -> dict[str, Any]:
    edited_paths: list[str] = []
    activation_edits = 0
    for skill_path in stage_skill_paths(task_dir, stages):
        block_changed = replace_or_append_reward_agent_block(skill_path, heading=heading, lines=lines)
        activation_changed = update_skill_activation(skill_path, feedback=feedback)
        if block_changed or activation_changed:
            edited_paths.append(str(skill_path))
        if activation_changed:
            activation_edits += 1
    return {
        "edited_files": len(edited_paths),
        "edited_paths": edited_paths,
        "stages": stages,
        "activation_edits": activation_edits,
    }


def _list_lines(label: str, values: Any) -> list[str]:
    if not isinstance(values, list) or not values:
        return [f"- {label}: none."]
    lines = [f"- {label}:"]
    for value in values[:6]:
        lines.append(f"  - {value}")
    return lines


def reward_case_payload(case: dict[str, Any]) -> dict[str, Any]:
    if "curator_feedback" in case or "label_free_judgment" in case:
        return case
    return {}


def edit_from_reward_case(task_dir: Path, case: dict[str, Any]) -> dict[str, Any] | None:
    reward_case = reward_case_payload(case)
    if not reward_case:
        return None
    feedback = reward_case.get("curator_feedback") or {}
    if not isinstance(feedback, dict):
        return None
    stages = [
        str(stage)
        for stage in (feedback.get("target_stages") or [])
        if str(stage) in STAGES
    ]
    if not stages:
        return {
            "operation": "reward_agent_policy_only",
            "edited_files": 0,
            "edited_paths": [],
            "stages": [],
        }
    max_stage_edits = int(feedback.get("max_stage_edits") or len(stages))
    stages = stages[: max(0, max_stage_edits)]
    if not stages:
        return {
            "operation": "reward_agent_no_allowed_stage_edits",
            "edited_files": 0,
            "edited_paths": [],
            "stages": [],
        }
    judgment = reward_case.get("label_free_judgment") or {}
    critique = reward_case.get("label_known_critique") or {}
    heading = "## Reward Agent Task Contract"
    lines = [
        f"- Harness label: `{reward_case.get('case_type')}`.",
        f"- Label-free decision: `{judgment.get('decision')}`; confidence: `{judgment.get('confidence')}`; predicted_delta: `{judgment.get('predicted_delta')}`.",
        f"- Label-known error: `{critique.get('reward_agent_error')}`; actual outcome: `{critique.get('actual_outcome')}`.",
        f"- Edit degree: `{feedback.get('allowed_edit_degree')}`; max stage edits: `{feedback.get('max_stage_edits')}`.",
        f"- Skill action: `{feedback.get('skill_action')}`; activation policy: `{feedback.get('activation_policy')}`.",
        f"- Rewrite goal: {feedback.get('rewrite_goal') or 'narrow the skill using task evidence.'}",
        f"- Policy lesson: {reward_case.get('policy_lesson') or critique.get('new_rule') or 'none'}",
    ]
    lines.extend(_list_lines("Must preserve", feedback.get("must_preserve")))
    lines.extend(_list_lines("Must remove", feedback.get("must_remove")))
    risk_signals = judgment.get("risk_signals") or []
    lines.extend(_list_lines("Reward-agent risk signals", risk_signals))
    lines.append("- Executor instruction: prioritize the preserved repository-relative evidence, ignore guidance listed under must remove, and stop this stage if the first concrete check does not match the current repository.")
    result = update_stage_skills_from_reward_feedback(
        task_dir,
        stages=stages,
        heading=heading,
        lines=lines,
        feedback=feedback,
    )
    result["operation"] = "reward_agent_rewrite_contract"
    result["reward_agent_error"] = critique.get("reward_agent_error")
    return result


def edit_positive(task_dir: Path, case: dict[str, Any]) -> dict[str, Any]:
    heading = "## Policy Iteration Preserve Note"
    stages = ["validate"]
    lines = [
        "- Harness label: positive (`0 -> 1`).",
        "- Edit degree: preserve_distill_only; do not broaden this task skill.",
        f"- Base reward: `{(case.get('base') or {}).get('reward')}`; previous reward: `{(case.get('previous') or {}).get('reward')}`; current reward: `{(case.get('current') or {}).get('reward')}`.",
        "- Preserve the behavior that helped this task, and only compress wording if needed.",
    ]
    result = update_stage_skills(task_dir, stages=stages, heading=heading, lines=lines)
    result["operation"] = "preserve_distill_only"
    return result


def edit_strong_negative(task_dir: Path, case: dict[str, Any]) -> dict[str, Any]:
    heading = "## Policy Iteration Strong Negative Guard"
    stages = ["reproduce", "localize", "edit", "validate", "recover"]
    lines = [
        "- Harness label: strong_negative (`1 -> 0`).",
        "- Edit degree: major_delete_or_rewrite; this skill harmed a previously solved task.",
        f"- Base reward: `{(case.get('base') or {}).get('reward')}`; previous reward: `{(case.get('previous') or {}).get('reward')}`; current reward: `{(case.get('current') or {}).get('reward')}`.",
        "- Before following this skill, verify the exact task evidence. Prefer no-skill behavior if this guidance conflicts with repository evidence.",
        "- Stop early if the first reproduction/localization attempt does not produce concrete evidence.",
    ]
    result = update_stage_skills(task_dir, stages=stages, heading=heading, lines=lines)
    result["operation"] = "major_delete_or_rewrite"
    return result


def edit_weak_negative(task_dir: Path, case: dict[str, Any]) -> dict[str, Any]:
    heading = "## Policy Iteration Weak Negative Guard"
    stages = ["validate", "recover"]
    lines = [
        "- Harness label: weak_negative (`0 -> 0`).",
        "- Edit degree: bounded_targeted_rewrite; do not make broad changes.",
        f"- Base reward: `{(case.get('base') or {}).get('reward')}`; previous reward: `{(case.get('previous') or {}).get('reward')}`; current reward: `{(case.get('current') or {}).get('reward')}`.",
        "- Use this skill only if it narrows reproduction, localization, or validation in one or two concrete moves.",
    ]
    result = update_stage_skills(task_dir, stages=stages, heading=heading, lines=lines)
    result["operation"] = "bounded_targeted_rewrite"
    return result


def apply_case(task_dir: Path, case: dict[str, Any]) -> dict[str, Any]:
    reward_result = edit_from_reward_case(task_dir, case)
    if reward_result is not None:
        return reward_result
    case_type = str(case.get("case_type") or "")
    if case_type == "positive":
        return edit_positive(task_dir, case)
    if case_type == "strong_negative":
        return edit_strong_negative(task_dir, case)
    if case_type == "weak_negative":
        return edit_weak_negative(task_dir, case)
    if case_type == "stable_positive":
        return {"operation": "policy_only_preserve", "edited_files": 0, "edited_paths": [], "stages": []}
    return {"operation": "ignored", "edited_files": 0}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Create the next task-specific skill version from fixed-harness policy update cases."
    )
    parser.add_argument("--previous-version", required=True)
    parser.add_argument("--next-version", required=True)
    parser.add_argument(
        "--cases-jsonl",
        type=Path,
        required=True,
        help="Path to selected_cases.jsonl or reward_cases.jsonl. Reward cases drive stage-specific rewrite contracts.",
    )
    parser.add_argument("--skill-root", type=Path, default=DEFAULT_SKILL_ROOT)
    parser.add_argument("--out", type=Path, default=None)
    parser.add_argument("--overwrite", action="store_true")
    parser.add_argument("--max-cases", type=int, default=None)
    parser.add_argument("--keep-legacy-policy-notes", action="store_true")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    skill_root = args.skill_root.expanduser().resolve()
    previous_root = skill_root / args.previous_version
    next_root = skill_root / args.next_version
    if not previous_root.exists():
        raise SystemExit(f"Missing previous skill version: {previous_root}")
    if next_root.exists():
        if not args.overwrite:
            raise SystemExit(f"Next skill version already exists: {next_root}")
        shutil.rmtree(next_root)
    shutil.copytree(previous_root, next_root)
    cleaned_legacy_files = 0
    if not args.keep_legacy_policy_notes:
        cleaned_legacy_files = clean_legacy_policy_sections(next_root)

    cases = load_jsonl(args.cases_jsonl.expanduser())
    if args.max_cases is not None:
        cases = cases[: max(0, args.max_cases)]
    manifest_rows: list[dict[str, Any]] = []
    missing: list[str] = []
    counts = Counter()
    for case in cases:
        task_name = str(case.get("task_name") or "")
        task_dir = find_task_dir(next_root, task_name)
        if task_dir is None:
            missing.append(task_name)
            continue
        result = apply_case(task_dir, case)
        counts[str(case.get("case_type") or "unknown")] += 1
        manifest_rows.append(
            {
                "task_name": task_name,
                "case_type": case.get("case_type"),
                "allowed_edit_degree": case.get("allowed_edit_degree"),
                "task_dir": str(task_dir),
                "result": result,
            }
        )

    manifest = {
        "schema_version": 1,
        "created_at": utc_now(),
        "previous_version": args.previous_version,
        "next_version": args.next_version,
        "previous_root": str(previous_root),
        "next_root": str(next_root),
        "cases_jsonl": str(args.cases_jsonl),
        "n_cases": len(cases),
        "n_applied": len(manifest_rows),
        "cleaned_legacy_policy_files": cleaned_legacy_files,
        "missing_tasks": missing,
        "case_type_counts": dict(counts),
    }
    out = args.out
    if out is None:
        out = ROOT / "run_logs" / "skill_versions" / args.next_version / "curation_manifest.json"
    out = out.expanduser().resolve()
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
    rows_path = out.with_suffix(".jsonl")
    rows_path.write_text(
        "\n".join(json.dumps(row, ensure_ascii=False, sort_keys=True) for row in manifest_rows) + "\n"
    )
    print(f"Wrote {out}")
    print(f"Wrote {rows_path}")
    print(f"Created {next_root}")


if __name__ == "__main__":
    main()
