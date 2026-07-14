#!/usr/bin/env python
from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.materialize_deepswe_tts_evolution_gates import (  # noqa: E402
    render_report_md,
    sha256_tree,
    validate_source_aggregate,
)
from evolution.tts_evolution import safe_slug  # noqa: E402
from scripts.job_run_lock import exclusive_job_run  # noqa: E402

def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(errors="replace"))


def write_json_atomic(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    with tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        dir=path.parent,
        prefix=f".{path.name}.",
        delete=False,
    ) as handle:
        handle.write(payload)
        temporary = Path(handle.name)
    os.replace(temporary, path)


def attach_gate_report(
    *,
    manifest_path: Path,
    gate_index: int,
    aggregate_path: Path,
    eval_run_id: str,
    expected_benchmark_name: str,
) -> dict[str, Any]:
    manifest = read_json(manifest_path)
    report = read_json(aggregate_path)
    validate_source_aggregate(
        report,
        aggregate_path,
        expected_run_id=eval_run_id,
        expected_benchmark_name=expected_benchmark_name,
    )
    if manifest.get("benchmark_name") != expected_benchmark_name:
        raise SystemExit(
            "Evolution manifest benchmark does not match the aggregate: "
            f"manifest={manifest.get('benchmark_name')} "
            f"expected={expected_benchmark_name}"
        )
    if gate_index == 0 and manifest.get("source_run_id") != eval_run_id:
        raise SystemExit(
            "Gate 0 must be attached to the evolution source run: "
            f"source={manifest.get('source_run_id')} eval={eval_run_id}"
        )
    gates = [
        gate
        for gate in manifest.get("gates") or []
        if int(gate.get("gate_index") or 0) == gate_index
    ]
    if len(gates) != 1:
        raise SystemExit(
            f"Expected exactly one gate_{gate_index:03d} in {manifest_path}"
        )
    gate = gates[0]
    if gate_index > 0:
        gate_root = Path(str(gate.get("skill_root") or "")).expanduser().resolve()
        contract = (report.get("provenance") or {}).get("resume_contract") or {}
        observed_trees = (contract.get("skills") or {}).get("tree_sha256") or []
        expected_tree = sha256_tree(gate_root)
        if observed_trees != [expected_tree]:
            raise SystemExit(
                "Gate aggregate was not produced with the frozen gate skill tree: "
                f"gate={gate_index} observed={observed_trees} "
                f"expected={[expected_tree]}"
            )

    evaluation = report.get("evaluation") or {}
    verifier_report = {
        "status": "available",
        "eval_run_id": eval_run_id,
        "aggregate_path": str(aggregate_path),
        "complete": report.get("complete"),
        "n_trials": evaluation.get("n_trials"),
        "n_errors": evaluation.get("n_errors"),
        "resolved": evaluation.get("resolved"),
        "mean_reward": evaluation.get("mean_reward"),
    }
    gate["verifier_report"] = verifier_report

    run_dir = manifest_path.parent
    run_gate_manifest_path = (
        run_dir / "gates" / f"gate_{gate_index:03d}" / "manifest.json"
    )
    if not run_gate_manifest_path.is_file():
        raise SystemExit(f"Missing run gate manifest: {run_gate_manifest_path}")
    run_gate_manifest = read_json(run_gate_manifest_path)
    run_gate_manifest["verifier_report"] = verifier_report
    write_json_atomic(run_gate_manifest_path, run_gate_manifest)
    write_json_atomic(manifest_path, manifest)
    (run_dir / "report.md").write_text(render_report_md(manifest))
    return verifier_report


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Validate and attach a DeepSWE aggregate to one frozen evolution gate."
    )
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--gate-index", type=int, required=True)
    parser.add_argument("--aggregate", type=Path, required=True)
    parser.add_argument("--eval-run-id", required=True)
    parser.add_argument("--benchmark-name", default="deepswe")
    parser.add_argument(
        "--tts-root",
        type=Path,
        default=ROOT / "run_logs" / "deepswe_tts_evo",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    manifest_path = (
        args.tts_root.expanduser().resolve() / args.run_id / "manifest.json"
    )
    aggregate_path = args.aggregate.expanduser().resolve()
    if not manifest_path.is_file():
        raise SystemExit(f"Missing evolution manifest: {manifest_path}")
    if not aggregate_path.is_file():
        raise SystemExit(f"Missing aggregate report: {aggregate_path}")
    lock_dir = (
        args.tts_root.expanduser().resolve()
        / ".evolution-locks"
        / safe_slug(args.run_id, limit=150)
    )
    with exclusive_job_run(lock_dir):
        verifier_report = attach_gate_report(
            manifest_path=manifest_path,
            gate_index=args.gate_index,
            aggregate_path=aggregate_path,
            eval_run_id=args.eval_run_id,
            expected_benchmark_name=args.benchmark_name,
        )
    print(
        json.dumps(
            {
                "manifest": str(manifest_path),
                "gate_index": args.gate_index,
                "verifier_report": verifier_report,
            },
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
