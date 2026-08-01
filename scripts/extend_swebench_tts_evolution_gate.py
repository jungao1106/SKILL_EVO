#!/usr/bin/env python
from __future__ import annotations

import argparse
import json
import shutil
import shlex
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from evolution.tts_evolution import (  # noqa: E402
    collect_failed_trace_evidence,
    generate_test_time_decisions,
    materialize_gate_library,
    safe_slug,
    write_json,
    write_jsonl,
)
from scripts.materialize_swebench_tts_evolution_gates import (  # noqa: E402
    DEFAULT_PYTHON,
    DEFAULT_TASK_FILE_GLOB,
    load_evaluator_policy,
    render_eval_launcher,
)


DEFAULT_POLICY_STATE = (
    ROOT
    / "run_logs"
    / "swegym_skill_evo"
    / "swegym_novita_glm52_c15_resume_merged_20260630_071956"
    / "training"
    / "policy_state.json"
)


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(errors="replace"))


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def shell_join(parts: list[object]) -> str:
    return " ".join(shlex.quote(str(part)) for part in parts)


def find_previous_gate(manifest: dict[str, Any], previous_gate: int | None) -> dict[str, Any]:
    gates = manifest.get("gates") or []
    if previous_gate is None:
        available = [
            gate
            for gate in gates
            if gate.get("verifier_report", {}).get("status") == "available"
        ]
        if not available:
            raise SystemExit("No previous gate with available verifier report")
        return max(available, key=lambda item: int(item.get("gate_index") or 0))
    for gate in gates:
        if int(gate.get("gate_index") or 0) == previous_gate:
            return gate
    raise SystemExit(f"Missing previous gate: {previous_gate}")


def next_gate_index(manifest: dict[str, Any], previous_gate: dict[str, Any]) -> int:
    raw = int(previous_gate.get("gate_index") or 0) + 1
    existing = {int(gate.get("gate_index") or 0) for gate in manifest.get("gates") or []}
    while raw in existing:
        raw += 1
    return raw


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Extend an existing SWE-bench Verified TTS evolution run by one frozen gate."
    )
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--previous-gate", type=int, default=None)
    parser.add_argument("--max-gate", type=int, default=8)
    parser.add_argument("--tts-root", type=Path, default=ROOT / "run_logs" / "swebench_verified_tts_evo")
    parser.add_argument("--verified-root", type=Path, default=ROOT / "run_logs" / "swebench_verified_frozen_shards")
    parser.add_argument("--skill-output-root", type=Path, default=ROOT / "skills" / "test_time")
    parser.add_argument("--policy-state", type=Path, default=DEFAULT_POLICY_STATE)
    parser.add_argument("--reward-threshold", type=float, default=1.0)
    parser.add_argument("--repo-update-batch-size", type=int, default=5)
    parser.add_argument("--repo-min-support", type=int, default=2)
    parser.add_argument("--repo-min-positive-support", type=int, default=0)
    parser.add_argument("--failure-mode-min-repo-support", type=int, default=2)
    parser.add_argument("--max-repo-skills-per-gate", type=int, default=12)
    parser.add_argument("--max-failure-skills-per-gate", type=int, default=12)
    parser.add_argument("--task-file-glob", default=DEFAULT_TASK_FILE_GLOB)
    parser.add_argument("--num-shards", type=int, default=5)
    parser.add_argument("--concurrency-per-shard", type=int, default=10)
    parser.add_argument("--python", default=DEFAULT_PYTHON)
    parser.add_argument("--env-file", type=Path, default=ROOT / ".env")
    parser.add_argument("--agent-timeout-sec", type=float, default=3600)
    parser.add_argument("--agent-setup-timeout-sec", type=float, default=1200)
    parser.add_argument("--e2b-sandbox-timeout-sec", type=int, default=7200)
    parser.add_argument(
        "--materialize-eval-scripts-only",
        action=argparse.BooleanOptionalAction,
        default=False,
        help="When rendering the gate eval launcher, add --materialize-only so it writes shard scripts but does not start tmux.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print the planned next gate without writing artifacts.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    run_dir = args.tts_root.expanduser().resolve() / args.run_id
    manifest_path = run_dir / "manifest.json"
    if not manifest_path.exists():
        raise SystemExit(f"Missing TTS manifest: {manifest_path}")
    manifest = read_json(manifest_path)
    previous_gate = find_previous_gate(manifest, args.previous_gate)
    previous_gate_index = int(previous_gate.get("gate_index") or 0)
    gate_index = next_gate_index(manifest, previous_gate)
    if gate_index > args.max_gate:
        raise SystemExit(f"Next gate {gate_index:03d} exceeds max gate {args.max_gate:03d}")

    verifier = previous_gate.get("verifier_report") or {}
    if verifier.get("status") != "available":
        raise SystemExit(f"Previous gate {previous_gate_index:03d} verifier report is not available")
    aggregate_path = Path(verifier["aggregate_path"]).expanduser().resolve()
    if not aggregate_path.exists():
        raise SystemExit(f"Missing previous gate aggregate: {aggregate_path}")
    base_skill_root = Path(previous_gate["skill_root"]).expanduser().resolve()
    if not base_skill_root.exists():
        raise SystemExit(f"Missing previous gate skill root: {base_skill_root}")

    args.policy_state = args.policy_state.expanduser().resolve() if args.policy_state else None
    args.env_file = args.env_file.expanduser().resolve()
    args.python = str(Path(args.python).expanduser()) if "/" in args.python else args.python
    evaluator_policy = load_evaluator_policy(args.policy_state)
    evidence_rows = collect_failed_trace_evidence(
        aggregate_report_path=aggregate_path,
        reward_threshold=args.reward_threshold,
    )
    generated = generate_test_time_decisions(
        evidence_rows=evidence_rows,
        run_name=f"{args.run_id}_gate{gate_index:03d}",
        evaluator_policy=evaluator_policy,
        repo_update_batch_size=args.repo_update_batch_size,
        repo_min_support=args.repo_min_support,
        repo_min_positive_support=args.repo_min_positive_support,
        failure_mode_min_repo_support=args.failure_mode_min_repo_support,
        max_repo_skills_per_gate=args.max_repo_skills_per_gate,
        max_failure_skills_per_gate=args.max_failure_skills_per_gate,
    )
    promoted = [
        row
        for row in generated["promotion_decisions"]
        if row.get("decision") == "promote"
    ]
    gate_root = args.skill_output_root.expanduser().resolve() / args.run_id / f"gate_{gate_index:03d}"
    gate_dir = run_dir / "gates" / f"gate_{gate_index:03d}"
    preview = {
        "run_id": args.run_id,
        "previous_gate": previous_gate_index,
        "gate_index": gate_index,
        "base_skill_root": str(base_skill_root),
        "gate_skill_root": str(gate_root),
        "source_aggregate": str(aggregate_path),
        "task_evidence": len(evidence_rows),
        "repo_candidates": len(generated["repo_candidates"]),
        "failure_candidates": len(generated["failure_candidates"]),
        "promoted_skills": len(promoted),
    }
    if args.dry_run:
        print(json.dumps(preview, ensure_ascii=False, indent=2, sort_keys=True))
        return

    gate_manifest = materialize_gate_library(
        base_skill_root=base_skill_root,
        output_root=gate_root,
        run_name=f"{args.run_id}_gate{gate_index:03d}",
        promotion_decisions=generated["promotion_decisions"],
        gate_index=gate_index,
        include_base=True,
        clean=True,
    )
    launcher_path = gate_dir / "eval_launcher.sh"
    launcher_path.parent.mkdir(parents=True, exist_ok=True)
    launcher_path.write_text(
        render_eval_launcher(
            run_id=args.run_id,
            gate_index=gate_index,
            gate_skill_root=gate_root,
            args=args,
        )
    )
    launcher_path.chmod(0o755)
    gate_manifest["eval_launcher"] = str(launcher_path)
    write_json(gate_dir / "manifest.json", gate_manifest)

    evidence_dir = run_dir / "evidence" / f"gate_{gate_index:03d}"
    candidates_dir = run_dir / "candidates" / f"gate_{gate_index:03d}"
    evaluator_dir = run_dir / "evaluator" / f"gate_{gate_index:03d}"
    write_jsonl(evidence_dir / "task_evidence.jsonl", evidence_rows)
    write_jsonl(evidence_dir / "repo_clusters.jsonl", generated["repo_clusters"])
    write_jsonl(evidence_dir / "failure_clusters.jsonl", generated["failure_clusters"])
    write_jsonl(candidates_dir / "repo_candidates.jsonl", generated["repo_candidates"])
    write_jsonl(candidates_dir / "failure_mode_candidates.jsonl", generated["failure_candidates"])
    write_jsonl(evaluator_dir / "evaluator_decisions.jsonl", generated["evaluator_decisions"])
    write_jsonl(evaluator_dir / "evaluator_calibration.jsonl", generated["evaluator_calibration"])
    write_jsonl(gate_dir / "promotion_decisions.jsonl", generated["promotion_decisions"])

    gate_row = {
        "gate_index": gate_index,
        "skill_root": str(gate_root),
        "skill_counts": gate_manifest["skill_counts"],
        "promotion_source": "evaluator_only",
        "source_previous_gate": previous_gate_index,
        "source_aggregate": str(aggregate_path),
        "eval_launcher": str(launcher_path),
        "verifier_report": {
            "status": "not_run",
            "note": "Verifier metrics may be attached after this frozen gate is evaluated; they are report-only.",
        },
        "evolution_summary": {
            "task_evidence": len(evidence_rows),
            "repo_candidates": len(generated["repo_candidates"]),
            "failure_candidates": len(generated["failure_candidates"]),
            "evaluator_decisions": len(generated["evaluator_decisions"]),
            "promoted_skills": len(promoted),
        },
    }
    manifest.setdefault("gates", []).append(gate_row)
    manifest.setdefault("extensions", []).append(
        {
            "created_at": utc_now(),
            "previous_gate": previous_gate_index,
            "gate_index": gate_index,
            "source_aggregate": str(aggregate_path),
            "summary": gate_row["evolution_summary"],
        }
    )
    write_json(manifest_path, manifest)
    print(json.dumps(preview, ensure_ascii=False, indent=2, sort_keys=True))
    print(f"[tts-evo-extend] gate_manifest={gate_dir / 'manifest.json'}")
    print(f"[tts-evo-extend] eval_launcher={launcher_path}")


if __name__ == "__main__":
    main()
