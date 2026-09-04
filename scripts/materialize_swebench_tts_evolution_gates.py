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
from agents.skill_writer import normalize_writer_policy  # noqa: E402


DEFAULT_SOURCE_RUN_ID = "swebench_verified_glm52_novita_v0100_frozen_direct_20260703_182027"
DEFAULT_BASE_SKILL_ROOT = ROOT / "skills" / "downstream" / DEFAULT_SOURCE_RUN_ID / "v0100"
DEFAULT_SOURCE_AGGREGATE = (
    ROOT
    / "run_logs"
    / "swebench_verified_frozen_shards"
    / DEFAULT_SOURCE_RUN_ID
    / "aggregate"
    / "score_report.json"
)
DEFAULT_POLICY_STATE = (
    ROOT
    / "run_logs"
    / "swegym_skill_evo"
    / "swegym_novita_glm52_c15_resume_merged_20260630_071956"
    / "training"
    / "policy_state.json"
)
DEFAULT_TASK_FILE_GLOB = (
    "run_logs/skill_evo_shards/"
    "skill_evo_verified_glm51_full_v0001_[0-9][0-9][0-9]_[0-9][0-9][0-9]_20260602_1921.txt"
)
DEFAULT_PYTHON = "/vePFS-Mindverse/user/intern/jungao/Marcronv1_SWE/.venv312/bin/python"


def utc_tag() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(errors="replace"))


def shell_join(parts: list[object]) -> str:
    return " ".join(shlex.quote(str(part)) for part in parts)


def load_evaluator_policy(path: Path | None) -> dict[str, Any]:
    if path is None or not path.exists():
        return {"rules": [], "update_count": 0, "path": str(path) if path else None}
    data = read_json(path)
    calibration = data.get("policy_calibration") if isinstance(data.get("policy_calibration"), dict) else {}
    evaluator_policy = calibration.get("evaluator_policy") if isinstance(calibration.get("evaluator_policy"), dict) else {}
    rules = evaluator_policy.get("rules") or calibration.get("evaluator_rules") or []
    return {
        "rules": [str(rule) for rule in rules if str(rule).strip()],
        "update_count": evaluator_policy.get("update_count") or (data.get("clocks") or {}).get("evaluator_policy_updates", 0),
        "path": str(path),
    }


def load_writer_policy(path: Path | None) -> dict[str, Any]:
    if path is None or not path.exists():
        return normalize_writer_policy({"path": str(path) if path else None})
    data = read_json(path)
    calibration = data.get("policy_calibration") if isinstance(data.get("policy_calibration"), dict) else {}
    writer_policy = calibration.get("writer_policy") if isinstance(calibration.get("writer_policy"), dict) else {}
    policy = normalize_writer_policy(
        {
            "rules": writer_policy.get("rules") or calibration.get("writer_rules") or [],
            "directives": writer_policy.get("directives") or calibration.get("writer_directives") or {},
            "update_count": writer_policy.get("update_count")
            or (data.get("clocks") or {}).get("writer_policy_updates", 0),
            "path": str(path),
        }
    )
    return policy


def render_eval_launcher(
    *,
    run_id: str,
    gate_index: int,
    gate_skill_root: Path,
    args: argparse.Namespace,
) -> str:
    eval_run_id = f"{run_id}_gate{gate_index:03d}_verified_eval"
    command = [
        args.python,
        "scripts/run_swebench_verified_frozen_library_shards.py",
        "--run-id",
        eval_run_id,
        "--run-prefix",
        eval_run_id,
        "--external-skill-pack-root",
        gate_skill_root,
        "--task-file-glob",
        args.task_file_glob,
        "--num-shards",
        args.num_shards,
        "--concurrency-per-shard",
        args.concurrency_per_shard,
        "--harness",
        args.harness,
        "--provider",
        args.provider,
        "--python",
        args.python,
        "--env-file",
        args.env_file,
        "--agent-timeout-sec",
        args.agent_timeout_sec,
        "--agent-setup-timeout-sec",
        args.agent_setup_timeout_sec,
        "--e2b-sandbox-timeout-sec",
        args.e2b_sandbox_timeout_sec,
    ]
    if args.provider_base_url:
        command.extend(["--provider-base-url", args.provider_base_url])
    if args.provider_anthropic_base_url:
        command.extend(["--provider-anthropic-base-url", args.provider_anthropic_base_url])
    if args.provider_model:
        command.extend(["--provider-model", args.provider_model])
    if args.provider_api:
        command.extend(["--provider-api", args.provider_api])
    if args.claude_max_turns is not None:
        command.extend(["--claude-max-turns", args.claude_max_turns])
    if args.claude_max_budget_usd is not None:
        command.extend(["--claude-max-budget-usd", args.claude_max_budget_usd])
    if args.materialize_eval_scripts_only:
        command.append("--materialize-only")
    lines = [
        "#!/usr/bin/env bash",
        "set -euo pipefail",
        f"cd {shlex.quote(str(ROOT))}",
        "",
        "# This launches the frozen eval for this gate.",
        "# Add --materialize-only to the Python command below if you only want shard scripts/manifests.",
        "# Evolution/promotion has already happened and used evaluator decisions only.",
        shell_join(command),
        "",
        "cat <<'EOF'",
        "After the eval finishes, aggregate verifier metrics with:",
        f"{shell_join([args.python, 'scripts/aggregate_swebench_verified_frozen_shards.py', '--run-id', eval_run_id])}",
        "EOF",
    ]
    return "\n".join(lines) + "\n"


def render_report_md(manifest: dict[str, Any]) -> str:
    summary = manifest["summary"]
    lines = [
        "# SWE-bench Verified Test-Time Skill Evolution Gates",
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
        "| Gate | Skill Root | Base Skills | Test-Time Skills | Eval Command | Verifier Report |",
        "| ---: | --- | ---: | ---: | --- | --- |",
    ]
    for gate in manifest.get("gates") or []:
        counts = gate.get("skill_counts") or {}
        lines.append(
            "| {idx} | `{root}` | {base} | {tts} | `{cmd}` | {report} |".format(
                idx=gate.get("gate_index"),
                root=gate.get("skill_root"),
                base=counts.get("base"),
                tts=counts.get("test_time_promoted"),
                cmd=gate.get("eval_launcher"),
                report=(gate.get("verifier_report") or {}).get("status"),
            )
        )
    lines.extend(
        [
            "",
            "## Boundary",
            "",
            "The source direct-run reward is used only to select `reward != 1` traces for evolution and for later reporting. Candidate generation and promotion do not consume hidden verifier labels; accepted gate skills come from the evaluator decision log.",
            "",
        ]
    )
    return "\n".join(lines)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Materialize SWE-bench Verified test-time skill evolution gate libraries without launching evals by default."
    )
    parser.add_argument("--source-run-id", default=DEFAULT_SOURCE_RUN_ID)
    parser.add_argument("--source-aggregate", type=Path, default=DEFAULT_SOURCE_AGGREGATE)
    parser.add_argument("--base-skill-root", type=Path, default=DEFAULT_BASE_SKILL_ROOT)
    parser.add_argument("--policy-state", type=Path, default=DEFAULT_POLICY_STATE)
    parser.add_argument("--run-id", default=None)
    parser.add_argument("--out-root", type=Path, default=ROOT / "run_logs" / "swebench_verified_tts_evo")
    parser.add_argument("--skill-output-root", type=Path, default=ROOT / "skills" / "test_time")
    parser.add_argument("--reward-threshold", type=float, default=1.0)
    parser.add_argument("--max-evidence", type=int, default=None)
    parser.add_argument("--repo-update-batch-size", type=int, default=5)
    parser.add_argument("--repo-min-support", type=int, default=2)
    parser.add_argument(
        "--repo-min-positive-support",
        type=int,
        default=0,
        help="Default is 0 because this script evolves from direct-run reward!=1 traces; repeated public repo signals still gate repo candidates.",
    )
    parser.add_argument("--failure-mode-min-repo-support", type=int, default=2)
    parser.add_argument("--max-repo-skills-per-gate", type=int, default=12)
    parser.add_argument("--max-failure-skills-per-gate", type=int, default=12)
    parser.add_argument("--task-file-glob", default=DEFAULT_TASK_FILE_GLOB)
    parser.add_argument("--num-shards", type=int, default=5)
    parser.add_argument("--concurrency-per-shard", type=int, default=10)
    parser.add_argument("--harness", choices=["pi", "claude-code"], default="pi")
    parser.add_argument("--provider", default="openai")
    parser.add_argument("--provider-base-url", default=None)
    parser.add_argument("--provider-anthropic-base-url", default=None)
    parser.add_argument("--provider-model", default=None)
    parser.add_argument("--provider-api", default=None)
    parser.add_argument("--claude-max-turns", type=int, default=None)
    parser.add_argument("--claude-max-budget-usd", type=float, default=None)
    parser.add_argument("--python", default=DEFAULT_PYTHON)
    parser.add_argument("--env-file", type=Path, default=ROOT / ".env")
    parser.add_argument("--agent-timeout-sec", type=float, default=3600)
    parser.add_argument("--agent-setup-timeout-sec", type=float, default=1200)
    parser.add_argument("--e2b-sandbox-timeout-sec", type=int, default=7200)
    parser.add_argument(
        "--materialize-eval-scripts-only",
        action=argparse.BooleanOptionalAction,
        default=False,
        help="When rendering gate eval launchers, add --materialize-only so the launcher writes shard scripts but does not start tmux.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print the planned manifest instead of writing artifacts.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    args.source_aggregate = args.source_aggregate.expanduser().resolve()
    args.base_skill_root = args.base_skill_root.expanduser().resolve()
    args.policy_state = args.policy_state.expanduser().resolve() if args.policy_state else None
    args.out_root = args.out_root.expanduser().resolve()
    args.skill_output_root = args.skill_output_root.expanduser().resolve()
    args.env_file = args.env_file.expanduser().resolve()
    args.python = str(Path(args.python).expanduser()) if "/" in args.python else args.python

    if not args.source_aggregate.exists():
        raise SystemExit(f"Missing source aggregate report: {args.source_aggregate}")
    if not args.base_skill_root.exists():
        raise SystemExit(f"Missing base skill root: {args.base_skill_root}")

    run_id = safe_slug(args.run_id or f"swebench_verified_tts_evo_{utc_tag()}", limit=150)
    run_dir = args.out_root / run_id
    skill_run_root = args.skill_output_root / run_id
    evaluator_policy = load_evaluator_policy(args.policy_state)
    writer_policy = load_writer_policy(args.policy_state)

    evidence_rows = collect_failed_trace_evidence(
        aggregate_report_path=args.source_aggregate,
        reward_threshold=args.reward_threshold,
        max_evidence=args.max_evidence,
    )
    generated = generate_test_time_decisions(
        evidence_rows=evidence_rows,
        run_name=run_id,
        writer_policy=writer_policy,
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
        "verifier_report": {
            "status": "not_run",
            "note": "Base gate eval report is attached only after frozen eval.",
        },
        "skill_counts": {
            "base": len(list(args.base_skill_root.rglob("SKILL.md"))),
            "test_time_promoted": 0,
            "total": len(list(args.base_skill_root.rglob("SKILL.md"))),
        },
        "skills": [],
    }
    promotion_decisions = generated["promotion_decisions"]
    promoted = [row for row in promotion_decisions if row.get("decision") == "promote"]
    gate1_preview = {
        "schema_version": 1,
        "kind": "test_time_skill_evolution_gate_library",
        "run_name": run_id,
        "gate_index": 1,
        "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "base_skill_root": str(args.base_skill_root),
        "output_root": str(gate1_root),
        "include_base": True,
        "copied_base": True,
        "promotion_source": "evaluator_only",
        "verifier_access_for_evolution": False,
        "verifier_report": {
            "status": "not_run",
            "note": "Verifier metrics may be attached after this frozen gate is evaluated; they are report-only.",
        },
        "skill_counts": {
            "base": len(list(args.base_skill_root.rglob("SKILL.md"))),
            "test_time_promoted": len(promoted),
            "total": len(list(args.base_skill_root.rglob("SKILL.md"))) + len(promoted),
        },
        "skills": [],
    }

    manifest: dict[str, Any] = {
        "schema_version": 1,
        "kind": "swebench_verified_test_time_skill_evolution",
        "run_id": run_id,
        "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "source_run_id": args.source_run_id,
        "source_aggregate": str(args.source_aggregate),
        "base_skill_root": str(args.base_skill_root),
        "run_dir": str(run_dir),
        "skill_run_root": str(skill_run_root),
        "evaluator_policy": evaluator_policy,
        "writer_policy": writer_policy,
        "evolution_contract": {
            "target_trace_filter": "source direct run reward != 1",
            "candidate_generation": "trained_writer_policy_from_public_trace_evidence",
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
            "harness": args.harness,
            "provider": args.provider,
            "provider_model": args.provider_model,
            "provider_base_url": args.provider_base_url,
            "provider_anthropic_base_url": args.provider_anthropic_base_url,
        },
        "summary": {
            "task_evidence": len(evidence_rows),
            "repo_clusters": len(generated["repo_clusters"]),
            "failure_clusters": len(generated["failure_clusters"]),
            "repo_candidates": len(generated["repo_candidates"]),
            "failure_candidates": len(generated["failure_candidates"]),
            "evaluator_decisions": len(generated["evaluator_decisions"]),
            "promoted_skills": len(promoted),
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
                "skill_counts": gate1_preview["skill_counts"],
                "promotion_source": "evaluator_only",
                "verifier_report": gate1_preview["verifier_report"],
            },
        ],
    }

    if args.dry_run:
        print(json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True))
        return

    if gate0_root.exists():
        shutil.rmtree(gate0_root)
    gate0_root.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(args.base_skill_root, gate0_root)
    write_json(gate0_root / "gate_library_manifest.json", gate0_manifest)
    gate1_manifest = materialize_gate_library(
        base_skill_root=args.base_skill_root,
        output_root=gate1_root,
        run_name=run_id,
        promotion_decisions=promotion_decisions,
        gate_index=1,
        include_base=True,
        clean=True,
    )

    for gate_index, gate_manifest in ((0, gate0_manifest), (1, gate1_manifest)):
        gate_dir = run_dir / "gates" / f"gate_{gate_index:03d}"
        gate_skill_root = Path(gate_manifest["output_root"])
        launcher_path = gate_dir / "eval_launcher.sh"
        launcher_path.parent.mkdir(parents=True, exist_ok=True)
        launcher_path.write_text(
            render_eval_launcher(
                run_id=run_id,
                gate_index=gate_index,
                gate_skill_root=gate_skill_root,
                args=args,
            )
        )
        launcher_path.chmod(0o755)
        write_json(gate_dir / "manifest.json", {**gate_manifest, "eval_launcher": str(launcher_path)})
        manifest["gates"][gate_index]["eval_launcher"] = str(launcher_path)
        manifest["gates"][gate_index]["skill_counts"] = gate_manifest["skill_counts"]

    write_jsonl(run_dir / "evidence" / "task_evidence.jsonl", evidence_rows)
    write_jsonl(run_dir / "evidence" / "repo_clusters.jsonl", generated["repo_clusters"])
    write_jsonl(run_dir / "evidence" / "failure_clusters.jsonl", generated["failure_clusters"])
    write_jsonl(run_dir / "candidates" / "repo_candidates.jsonl", generated["repo_candidates"])
    write_jsonl(run_dir / "candidates" / "failure_mode_candidates.jsonl", generated["failure_candidates"])
    write_jsonl(run_dir / "evaluator" / "evaluator_decisions.jsonl", generated["evaluator_decisions"])
    write_jsonl(run_dir / "evaluator" / "evaluator_calibration.jsonl", generated["evaluator_calibration"])
    write_jsonl(run_dir / "promotion_decisions.jsonl", promotion_decisions)
    write_json(run_dir / "manifest.json", manifest)
    (run_dir / "report.md").write_text(render_report_md(manifest) + "\n")

    print(f"[tts-evo] run_dir={run_dir}")
    print(f"[tts-evo] skill_run_root={skill_run_root}")
    print(
        "[tts-evo] evidence={evidence} repo_candidates={repo_candidates} "
        "failure_candidates={failure_candidates} promoted={promoted}".format(
            evidence=len(evidence_rows),
            repo_candidates=len(generated["repo_candidates"]),
            failure_candidates=len(generated["failure_candidates"]),
            promoted=len(promoted),
        )
    )
    print("[tts-evo] eval launchers:")
    for gate in manifest["gates"]:
        print(f"  gate_{gate['gate_index']:03d}: {gate.get('eval_launcher')}")


if __name__ == "__main__":
    main()
