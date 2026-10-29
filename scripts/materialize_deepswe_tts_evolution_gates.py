#!/usr/bin/env python
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
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
    DEFAULT_POLICY_STATE,
    load_evaluator_policy,
)
from scripts.job_run_lock import exclusive_job_run, job_is_running  # noqa: E402


def render_report_md(manifest: dict[str, Any]) -> str:
    summary = manifest["summary"]
    lines = [
        "# DeepSWE Test-Time Skill Evolution Gates",
        "",
        "## Summary",
        "",
        f"- Run id: `{manifest['run_id']}`",
        f"- Source direct run: `{manifest['source_run_id']}`",
        f"- Failed traces used for evolution: `{summary['task_evidence']}`",
        "- Evolution verifier access: `false`",
        "- Promotion source: `evaluator_only`",
        f"- Repo candidates: `{summary['repo_candidates']}`",
        f"- Failure-mode candidates: `{summary['failure_candidates']}`",
        f"- Promoted test-time skills: `{summary['promoted_skills']}`",
        "",
        "## Gates",
        "",
        "| Gate | Skill Root | Base Skills | Test-Time Skills | Verifier Report |",
        "| ---: | --- | ---: | ---: | --- |",
    ]
    for gate in manifest.get("gates") or []:
        counts = gate.get("skill_counts") or {}
        lines.append(
            "| {idx} | `{root}` | {base} | {tts} | {report} |".format(
                idx=gate.get("gate_index"),
                root=gate.get("skill_root"),
                base=counts.get("base"),
                tts=counts.get("test_time_promoted"),
                report=(gate.get("verifier_report") or {}).get("status"),
            )
        )
    return "\n".join(lines) + "\n"


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def sha256_tree(root: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(item for item in root.rglob("*") if item.is_file()):
        relative = path.relative_to(root).as_posix().encode("utf-8")
        digest.update(len(relative).to_bytes(8, "big"))
        digest.update(relative)
        digest.update(bytes.fromhex(sha256_file(path)))
    return digest.hexdigest()


def validate_source_aggregate(
    report: dict[str, Any],
    path: Path,
    *,
    expected_run_id: str | None = None,
    expected_benchmark_name: str | None = None,
) -> None:
    invalid = list(report.get("infra_invalid_trials") or [])
    rows = report.get("tasks") or (report.get("evaluation") or {}).get("tasks") or []
    for row in rows:
        raw_reward = row.get("reward")
        try:
            reward = float(raw_reward) if not isinstance(raw_reward, bool) else None
        except (TypeError, ValueError):
            reward = None
        if reward not in {0.0, 1.0}:
            invalid.append(
                {
                    "trial_name": row.get("trial_name"),
                    "reason": f"invalid-reward:{raw_reward}",
                }
            )
    completeness = report.get("completeness") or {}
    expected = int(completeness.get("expected_trials") or 0)
    actual = int(
        completeness.get("trial_result_files")
        or (report.get("evaluation") or {}).get("n_trials")
        or 0
    )
    task_names = [str(row.get("task_name") or "") for row in rows]
    duplicate_tasks = len(task_names) != len(set(task_names))
    exact_count = not expected or actual == expected == len(rows)
    run_id_matches = expected_run_id is None or report.get("run_id") == expected_run_id
    benchmark_matches = (
        expected_benchmark_name is None
        or report.get("benchmark_name") == expected_benchmark_name
    )
    if (
        report.get("complete") is not True
        or invalid
        or not exact_count
        or duplicate_tasks
        or not run_id_matches
        or not benchmark_matches
    ):
        raise SystemExit(
            "Refusing to materialize skills from an incomplete or infra-invalid "
            f"aggregate: {path} complete={report.get('complete')} "
            f"trials={actual}/{expected or '?'} infra_invalid={len(invalid)} "
            f"duplicate_tasks={duplicate_tasks}"
            f" run_id={report.get('run_id')!r} expected_run_id={expected_run_id!r}"
            f" benchmark={report.get('benchmark_name')!r} "
            f"expected_benchmark={expected_benchmark_name!r}"
        )


def materialization_is_reusable(
    run_dir: Path,
    skill_run_root: Path,
    *,
    expected_fingerprints: dict[str, Any] | None = None,
) -> bool:
    required = (
        run_dir / "manifest.json",
        run_dir / "promotion_decisions.jsonl",
        run_dir / "gates" / "gate_000" / "manifest.json",
        run_dir / "gates" / "gate_001" / "manifest.json",
        skill_run_root / "gate_000" / "gate_library_manifest.json",
        skill_run_root / "gate_001" / "gate_library_manifest.json",
    )
    if not all(path.is_file() for path in required):
        return False
    try:
        manifest = json.loads((run_dir / "manifest.json").read_text())
    except (OSError, json.JSONDecodeError):
        return False
    if (
        expected_fingerprints is not None
        and manifest.get("input_fingerprints") != expected_fingerprints
    ):
        return False
    output_fingerprints = manifest.get("output_fingerprints")
    if not isinstance(output_fingerprints, dict):
        return False
    return output_fingerprints == {
        "gate_000_tree_sha256": sha256_tree(skill_run_root / "gate_000"),
        "gate_001_tree_sha256": sha256_tree(skill_run_root / "gate_001"),
    }


def archive_existing_materialization(run_dir: Path, skill_run_root: Path) -> None:
    archive_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    for source, kind in ((run_dir, "run"), (skill_run_root, "skills")):
        if not source.exists():
            continue
        destination = (
            source.parent
            / ".rematerialization_archive"
            / source.name
            / archive_id
            / kind
        )
        destination.parent.mkdir(parents=True, exist_ok=True)
        os.replace(source, destination)


def active_jobs_referencing_skill_root(skill_run_root: Path) -> list[str]:
    target = skill_run_root.resolve()
    active: list[str] = []
    jobs_root = ROOT / "jobs"
    if not jobs_root.is_dir():
        return active
    for job_dir in sorted(path for path in jobs_root.iterdir() if path.is_dir()):
        if not job_is_running(job_dir):
            continue
        config_path = job_dir / "config.json"
        if not config_path.is_file():
            continue
        try:
            config = json.loads(config_path.read_text(errors="replace"))
        except (OSError, json.JSONDecodeError):
            continue
        roots: list[str] = []
        for agent in config.get("agents") or []:
            contract = ((agent.get("kwargs") or {}).get("resume_contract") or {})
            roots.extend((contract.get("skills") or {}).get("roots") or [])
        for root in roots:
            try:
                resolved = Path(root).expanduser().resolve()
                references_target = resolved == target or resolved.is_relative_to(target)
            except (OSError, ValueError):
                references_target = False
            if references_target:
                active.append(job_dir.name)
                break
    return active


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Materialize DeepSWE test-time skill evolution gate libraries."
    )
    parser.add_argument("--source-run-id", required=True)
    parser.add_argument("--source-aggregate", type=Path, required=True)
    parser.add_argument("--base-skill-root", type=Path, required=True)
    parser.add_argument("--policy-state", type=Path, default=DEFAULT_POLICY_STATE)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--out-root", type=Path, default=ROOT / "run_logs" / "deepswe_tts_evo")
    parser.add_argument("--skill-output-root", type=Path, default=ROOT / "skills" / "test_time")
    parser.add_argument("--benchmark-name", default="deepswe")
    parser.add_argument("--reward-threshold", type=float, default=1.0)
    parser.add_argument("--max-evidence", type=int, default=None)
    parser.add_argument("--repo-update-batch-size", type=int, default=5)
    parser.add_argument("--repo-min-support", type=int, default=2)
    parser.add_argument("--repo-min-positive-support", type=int, default=0)
    parser.add_argument("--failure-mode-min-repo-support", type=int, default=2)
    parser.add_argument("--max-repo-skills-per-gate", type=int, default=12)
    parser.add_argument("--max-failure-skills-per-gate", type=int, default=12)
    parser.add_argument(
        "--force-rematerialize",
        action="store_true",
        help="Replace an existing gate library. By default a complete materialization is reused.",
    )
    return parser.parse_args()


def run_materialization(args: argparse.Namespace) -> None:
    args.source_aggregate = args.source_aggregate.expanduser().resolve()
    args.base_skill_root = args.base_skill_root.expanduser().resolve()
    args.policy_state = args.policy_state.expanduser().resolve()
    args.out_root = args.out_root.expanduser().resolve()
    args.skill_output_root = args.skill_output_root.expanduser().resolve()
    if not args.source_aggregate.exists():
        raise SystemExit(f"Missing source aggregate report: {args.source_aggregate}")
    if not args.base_skill_root.exists():
        raise SystemExit(f"Missing base skill root: {args.base_skill_root}")
    if not args.policy_state.is_file():
        raise SystemExit(f"Missing evaluator policy state: {args.policy_state}")
    try:
        source_report = json.loads(args.source_aggregate.read_text(errors="replace"))
    except json.JSONDecodeError as exc:
        raise SystemExit(
            f"Invalid source aggregate JSON: {args.source_aggregate}: {exc}"
        ) from exc
    validate_source_aggregate(
        source_report,
        args.source_aggregate,
        expected_run_id=args.source_run_id,
        expected_benchmark_name=args.benchmark_name,
    )

    input_fingerprints = {
        "source_aggregate_sha256": sha256_file(args.source_aggregate),
        "base_skill_tree_sha256": sha256_tree(args.base_skill_root),
        "policy_state_sha256": sha256_file(args.policy_state),
        "code_sha256": {
            str(path.relative_to(ROOT)): sha256_file(path)
            for path in (
                ROOT / "evolution" / "tts_evolution.py",
                ROOT / "scripts" / "materialize_deepswe_tts_evolution_gates.py",
                ROOT / "scripts" / "materialize_swebench_tts_evolution_gates.py",
            )
        },
        "source_run_id": args.source_run_id,
        "benchmark_name": args.benchmark_name,
        "reward_threshold": args.reward_threshold,
        "max_evidence": args.max_evidence,
        "repo_update_batch_size": args.repo_update_batch_size,
        "repo_min_support": args.repo_min_support,
        "repo_min_positive_support": args.repo_min_positive_support,
        "failure_mode_min_repo_support": args.failure_mode_min_repo_support,
        "max_repo_skills_per_gate": args.max_repo_skills_per_gate,
        "max_failure_skills_per_gate": args.max_failure_skills_per_gate,
    }

    run_id = safe_slug(args.run_id, limit=150)
    run_dir = args.out_root / run_id
    skill_run_root = args.skill_output_root / run_id
    if not args.force_rematerialize and materialization_is_reusable(
        run_dir,
        skill_run_root,
        expected_fingerprints=input_fingerprints,
    ):
        print(f"[deepswe-tts-evo] reuse existing run_dir={run_dir}")
        print(f"[deepswe-tts-evo] reuse existing skill_run_root={skill_run_root}")
        return
    if not args.force_rematerialize and (
        run_dir.exists() or skill_run_root.exists()
    ):
        raise SystemExit(
            "Existing DeepSWE gate materialization is incomplete; inspect it or rerun "
            "with --force-rematerialize: "
            f"run_dir={run_dir} skill_run_root={skill_run_root}"
        )
    if args.force_rematerialize:
        active_jobs = active_jobs_referencing_skill_root(skill_run_root)
        if active_jobs:
            raise SystemExit(
                "Refusing to rematerialize a skill root used by active jobs: "
                + ", ".join(active_jobs)
            )
        archive_existing_materialization(run_dir, skill_run_root)
    evaluator_policy = load_evaluator_policy(args.policy_state)

    evidence_rows = collect_failed_trace_evidence(
        aggregate_report_path=args.source_aggregate,
        reward_threshold=args.reward_threshold,
        max_evidence=args.max_evidence,
        benchmark_name=args.benchmark_name,
    )
    generated = generate_test_time_decisions(
        evidence_rows=evidence_rows,
        run_name=run_id,
        benchmark_name=args.benchmark_name,
        evaluator_policy=evaluator_policy,
        repo_update_batch_size=args.repo_update_batch_size,
        repo_min_support=args.repo_min_support,
        repo_min_positive_support=args.repo_min_positive_support,
        failure_mode_min_repo_support=args.failure_mode_min_repo_support,
        max_repo_skills_per_gate=args.max_repo_skills_per_gate,
        max_failure_skills_per_gate=args.max_failure_skills_per_gate,
    )

    gate0_root = skill_run_root / "gate_000"
    gate1_root = skill_run_root / "gate_001"
    if gate0_root.exists():
        shutil.rmtree(gate0_root)
    gate0_root.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(args.base_skill_root, gate0_root)
    base_count = len(list(args.base_skill_root.rglob("SKILL.md")))
    gate0_manifest = {
        "schema_version": 1,
        "kind": "test_time_skill_evolution_gate_library",
        "run_name": run_id,
        "gate_index": 0,
        "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "base_skill_root": str(args.base_skill_root),
        "output_root": str(gate0_root),
        "include_base": True,
        "copied_base": True,
        "promotion_source": "none",
        "verifier_access_for_evolution": False,
        "verifier_report": {"status": "not_run"},
        "skill_counts": {"base": base_count, "test_time_promoted": 0, "total": base_count},
        "skills": [],
    }
    write_json(gate0_root / "gate_library_manifest.json", gate0_manifest)

    gate1_manifest = materialize_gate_library(
        base_skill_root=args.base_skill_root,
        output_root=gate1_root,
        run_name=run_id,
        promotion_decisions=generated["promotion_decisions"],
        gate_index=1,
        include_base=True,
        clean=True,
    )
    promoted = sum(1 for row in generated["promotion_decisions"] if row.get("decision") == "promote")
    manifest: dict[str, Any] = {
        "schema_version": 1,
        "kind": "deepswe_test_time_skill_evolution",
        "run_id": run_id,
        "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "benchmark_name": args.benchmark_name,
        "source_run_id": args.source_run_id,
        "source_aggregate": str(args.source_aggregate),
        "base_skill_root": str(args.base_skill_root),
        "run_dir": str(run_dir),
        "skill_run_root": str(skill_run_root),
        "evaluator_policy": evaluator_policy,
        "input_fingerprints": input_fingerprints,
        "output_fingerprints": {
            "gate_000_tree_sha256": sha256_tree(gate0_root),
            "gate_001_tree_sha256": sha256_tree(gate1_root),
        },
        "evolution_contract": {
            "target_trace_filter": "source direct run reward != 1",
            "candidate_generation": "writer_from_public_trace_evidence",
            "promotion": "evaluator_only",
            "verifier_access_for_evolution": False,
            "verifier_metrics": "report_only_after_each_gate_eval",
        },
        "parameters": {
            "reward_threshold": args.reward_threshold,
            "repo_update_batch_size": args.repo_update_batch_size,
            "repo_min_support": args.repo_min_support,
            "repo_min_positive_support": args.repo_min_positive_support,
            "failure_mode_min_repo_support": args.failure_mode_min_repo_support,
            "max_repo_skills_per_gate": args.max_repo_skills_per_gate,
            "max_failure_skills_per_gate": args.max_failure_skills_per_gate,
        },
        "summary": {
            "task_evidence": len(evidence_rows),
            "repo_clusters": len(generated["repo_clusters"]),
            "failure_clusters": len(generated["failure_clusters"]),
            "repo_candidates": len(generated["repo_candidates"]),
            "failure_candidates": len(generated["failure_candidates"]),
            "evaluator_decisions": len(generated["evaluator_decisions"]),
            "promoted_skills": promoted,
        },
        "gates": [
            {
                "gate_index": 0,
                "skill_root": str(gate0_root),
                "skill_counts": gate0_manifest["skill_counts"],
                "promotion_source": "none",
                "verifier_report": gate0_manifest["verifier_report"],
            },
            {
                "gate_index": 1,
                "skill_root": str(gate1_root),
                "skill_counts": gate1_manifest["skill_counts"],
                "promotion_source": "evaluator_only",
                "verifier_report": {"status": "not_run"},
            },
        ],
    }
    for gate_index, gate_manifest in ((0, gate0_manifest), (1, gate1_manifest)):
        write_json(run_dir / "gates" / f"gate_{gate_index:03d}" / "manifest.json", gate_manifest)
    write_jsonl(run_dir / "evidence" / "task_evidence.jsonl", evidence_rows)
    write_jsonl(run_dir / "evidence" / "repo_clusters.jsonl", generated["repo_clusters"])
    write_jsonl(run_dir / "evidence" / "failure_clusters.jsonl", generated["failure_clusters"])
    write_jsonl(run_dir / "candidates" / "repo_candidates.jsonl", generated["repo_candidates"])
    write_jsonl(run_dir / "candidates" / "failure_mode_candidates.jsonl", generated["failure_candidates"])
    write_jsonl(run_dir / "evaluator" / "evaluator_decisions.jsonl", generated["evaluator_decisions"])
    write_jsonl(run_dir / "evaluator" / "evaluator_calibration.jsonl", generated["evaluator_calibration"])
    write_jsonl(run_dir / "promotion_decisions.jsonl", generated["promotion_decisions"])
    write_json(run_dir / "manifest.json", manifest)
    (run_dir / "report.md").write_text(render_report_md(manifest))
    print(f"[deepswe-tts-evo] run_dir={run_dir}")
    print(f"[deepswe-tts-evo] skill_run_root={skill_run_root}")
    print(
        "[deepswe-tts-evo] evidence={evidence} repo_candidates={repo_candidates} "
        "failure_candidates={failure_candidates} promoted={promoted}".format(
            evidence=len(evidence_rows),
            repo_candidates=len(generated["repo_candidates"]),
            failure_candidates=len(generated["failure_candidates"]),
            promoted=promoted,
        )
    )


def main() -> None:
    args = parse_args()
    lock_dir = (
        args.out_root.expanduser().resolve()
        / ".evolution-locks"
        / safe_slug(args.run_id, limit=150)
    )
    with exclusive_job_run(lock_dir):
        run_materialization(args)


if __name__ == "__main__":
    main()
