#!/usr/bin/env python
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


DEFAULT_TTS_ROOT = ROOT / "run_logs" / "swebench_verified_tts_evo"
DEFAULT_VERIFIED_ROOT = ROOT / "run_logs" / "swebench_verified_frozen_shards"


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(errors="replace"))


def write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True) + "\n")


def verifier_report_for_gate(*, run_id: str, gate_index: int, verified_root: Path) -> dict[str, Any]:
    eval_run_id = f"{run_id}_gate{gate_index:03d}_verified_eval"
    aggregate_path = verified_root / eval_run_id / "aggregate" / "score_report.json"
    if not aggregate_path.exists():
        return {
            "status": "missing",
            "eval_run_id": eval_run_id,
            "aggregate_path": str(aggregate_path),
            "note": "Run the gate eval launcher, then aggregate the shards before reporting verifier metrics.",
        }
    report = read_json(aggregate_path)
    evaluation = report.get("evaluation") if isinstance(report.get("evaluation"), dict) else {}
    return {
        "status": "available",
        "eval_run_id": eval_run_id,
        "aggregate_path": str(aggregate_path),
        "complete": report.get("complete"),
        "n_trials": evaluation.get("n_trials"),
        "n_errors": evaluation.get("n_errors"),
        "resolved": evaluation.get("resolved"),
        "mean_reward": evaluation.get("mean_reward"),
    }


def render_markdown(manifest: dict[str, Any]) -> str:
    summary = manifest.get("summary") or {}
    lines = [
        "# SWE-bench Verified Test-Time Skill Evolution Gate Report",
        "",
        "## Evolution",
        "",
        f"- Run id: `{manifest.get('run_id')}`",
        f"- Source run: `{manifest.get('source_run_id')}`",
        f"- Task evidence: `{summary.get('task_evidence')}`",
        f"- Repo candidates: `{summary.get('repo_candidates')}`",
        f"- Failure-mode candidates: `{summary.get('failure_candidates')}`",
        f"- Promoted skills: `{summary.get('promoted_skills')}`",
        "- Evolution verifier access: `false`",
        "- Promotion source: `evaluator_only`",
        "",
        "## Gate Verifier Metrics",
        "",
        "| Gate | Skills | Verifier Status | Complete | Resolved | Mean Reward | Errors | Eval Run |",
        "| ---: | ---: | --- | --- | ---: | ---: | ---: | --- |",
    ]
    for gate in manifest.get("gates") or []:
        counts = gate.get("skill_counts") or {}
        verifier = gate.get("verifier_report") or {}
        lines.append(
            "| {gate} | {skills} | {status} | {complete} | {resolved} | {mean} | {errors} | `{run}` |".format(
                gate=gate.get("gate_index"),
                skills=counts.get("total"),
                status=verifier.get("status"),
                complete=verifier.get("complete"),
                resolved=verifier.get("resolved"),
                mean=verifier.get("mean_reward"),
                errors=verifier.get("n_errors"),
                run=verifier.get("eval_run_id") or "",
            )
        )
    lines.extend(
        [
            "",
            "## Boundary",
            "",
            "Verifier metrics in this report are computed only after each gate is frozen. They are not consumed by candidate generation, evaluator decisions, or promotion.",
            "",
        ]
    )
    return "\n".join(lines)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Attach report-only verifier metrics to a SWE-bench Verified test-time skill evolution manifest."
    )
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--tts-root", type=Path, default=DEFAULT_TTS_ROOT)
    parser.add_argument("--verified-root", type=Path, default=DEFAULT_VERIFIED_ROOT)
    parser.add_argument("--out-md", type=Path, default=None)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    run_dir = args.tts_root.expanduser().resolve() / args.run_id
    verified_root = args.verified_root.expanduser().resolve()
    manifest_path = run_dir / "manifest.json"
    if not manifest_path.exists():
        raise SystemExit(f"Missing TTS manifest: {manifest_path}")
    manifest = read_json(manifest_path)
    for gate in manifest.get("gates") or []:
        gate_index = int(gate.get("gate_index") or 0)
        gate["verifier_report"] = verifier_report_for_gate(
            run_id=args.run_id,
            gate_index=gate_index,
            verified_root=verified_root,
        )
        gate_manifest_path = run_dir / "gates" / f"gate_{gate_index:03d}" / "manifest.json"
        if gate_manifest_path.exists():
            gate_manifest = read_json(gate_manifest_path)
            gate_manifest["verifier_report"] = gate["verifier_report"]
            write_json(gate_manifest_path, gate_manifest)
    write_json(manifest_path, manifest)
    out_md = args.out_md.expanduser().resolve() if args.out_md else run_dir / "verifier_report.md"
    out_md.write_text(render_markdown(manifest) + "\n")
    print(f"[tts-evo-report] manifest={manifest_path}")
    print(f"[tts-evo-report] report={out_md}")


if __name__ == "__main__":
    main()
