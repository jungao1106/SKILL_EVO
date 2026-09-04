#!/usr/bin/env python
"""Claude Code DeepSWE TTS chain with reward-zero or all-task evaluation."""
import json, glob, os, subprocess, sys, shutil
from pathlib import Path
from datetime import datetime

ROOT = Path("/vePFS-Mindverse/user/intern/jungao/SKILLS_EVO")
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from evolution.tts_evolution import (
    normalize_tts_evaluation_scope,
    reward_is_selected_for_tts_evaluation,
)

PY = "/vePFS-Mindverse/user/intern/jungao/Marcronv1_SWE/.venv312/bin/python"
DATASET = "/vePFS-Mindverse/user/intern/jungao/Marcronv1-Coding/deep-swe/tasks"
SKILL_ROOT_BASE = ROOT / "skills/test_time/cc_deepswe_macaron_v0201_tts_evo_20260811"
TTS_RUN_ID = "cc_deepswe_macaron_v0201_tts_evo_20260811"
# gate1 reuses the already-materialized gate_001 library (evolved from novita frozen traces)
GATE1_SKILL_SRC = ROOT / "skills/test_time/cc_deepswe_macaron_v0201_tts_evo_20260811/gate_001"
FROZEN_REPORT = ROOT / "run_logs/novita_chain_rerun/cc_deepswe_frozen_merged_score_report.json"
BASE_SKILL = ROOT / "skills/downstream/swebench_verified_cc_novita_glm52_v0201_frozen_downstream_20260711_074608/v0201"
POLICY = ROOT / "run_logs/swegym_skill_evo/swegym_pi_novita_glm52_c15_resume_merged_20260709_101635/training/policy_state.json"
MAX_GATE = 4
CONCURRENCY = 8
EVALUATION_SCOPE = normalize_tts_evaluation_scope(
    os.getenv("TTS_EVALUATION_SCOPE", "reward-zero")
)

def load_env():
    env = os.environ.copy()
    for line in (ROOT / ".env").read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line: continue
        k, v = line.split("=", 1)
        env[k.strip()] = v.strip().strip('"').strip("'")
    return env

def selected_tasks(report_path):
    r = json.load(open(report_path))
    return [
        t["task_name"]
        for t in r["tasks"]
        if reward_is_selected_for_tts_evaluation(t.get("reward"), EVALUATION_SCOPE)
    ]

def write_tasks(tasks, path):
    leaves = [t.split("/")[-1] for t in tasks]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(leaves) + "\n")
    return path

def run_eval(job_name, skill_root, tasks, env, log_path):
    task_file = write_tasks(tasks, ROOT / f"run_logs/cc_deepswe_tts_macaron/{job_name}_tasks.txt")
    cmd = [PY, "scripts/run_deepswe.py",
        "--dataset", DATASET, "--benchmark-name", "deepswe",
        "--harness", "claude-code", "--provider", "macaron", "--provider-model", "glm-5.2",
        "--job-name", job_name, "--concurrency", str(CONCURRENCY),
        "--agent-timeout-sec", "9000", "--agent-setup-timeout-sec", "1200",
        "--e2b-sandbox-timeout-sec", "15000", "--verifier-buffer-sec", "4800",
        "--override-storage-mb", "20480",
        "--task-names-file", str(task_file),
        "--use-skills"]
    env = dict(env)
    env["PI_SKILL_PACK_ROOT"] = str(skill_root.resolve())
    env["E2B_CONCURRENCY"] = str(CONCURRENCY)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("w") as f:
        f.write(" ".join(cmd) + "\n\n"); f.flush()
        subprocess.run(cmd, cwd=ROOT, env=env, stdout=f, stderr=subprocess.STDOUT, check=False)

def collect_results(job_name):
    results = {}
    for t in glob.glob(str(ROOT / "jobs" / job_name / "*/result.json")):
        try:
            r = json.load(open(t))
        except: continue
        tn = r.get("task_name")
        if not tn: continue
        vr = r.get("verifier_result") or {}
        rw = (vr.get("rewards") or {}).get("reward") if isinstance(vr, dict) else None
        try: rv = float(rw)
        except: rv = -1.0 if str(rw) == "-1.0" else -99
        results[tn] = {"reward": rv, "result_path": t, "trial_name": r.get("trial_name", Path(t).parent.name)}
    return results

def make_report(job_name, prev_report_tasks):
    """Build score_report for materialize: prev solved + this gate results."""
    results = collect_results(job_name)
    tasks = []
    for tn, prev_rw in prev_report_tasks.items():
        if tn in results and reward_is_selected_for_tts_evaluation(prev_rw, EVALUATION_SCOPE):
            rw = results[tn]["reward"]
            rp = results[tn]["result_path"]
            trial = results[tn]["trial_name"]
            if rw not in (0.0, 1.0): rw = 0.0  # clean -1.0/timeout to 0
        else:
            rw = prev_rw
            rp = ""
            trial = tn
        tasks.append({"task_name": tn, "reward": rw, "trial_name": trial, "result_path": rp, "exception_type": None})
    report = {
        "schema_version": 1, "run_id": job_name, "created_at": datetime.now().isoformat(),
        "complete": True, "benchmark_name": "deepswe", "infra_invalid_trials": [],
        "evaluation": {"n_trials": len(tasks), "n_errors": 0}, "tasks": tasks,
        "completeness": {"expected_trials": len(tasks), "trial_result_files": len(tasks)},
    }
    out = ROOT / f"run_logs/cc_deepswe_tts_macaron/{job_name}_score_report.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2, ensure_ascii=False))
    return out, {t["task_name"]: t["reward"] for t in tasks}

def materialize_gate(gate_index, source_report, base_skill_root):
    """Materialize gate N skill library from previous gate's failed traces."""
    import sys as _sys
    _sys.path.insert(0, str(ROOT))
    from evolution.tts_evolution import (
        collect_failed_trace_evidence, generate_test_time_decisions,
        materialize_gate_library,
    )
    from scripts.materialize_swebench_tts_evolution_gates import load_evaluator_policy, load_writer_policy
    evaluator_policy = load_evaluator_policy(POLICY)
    writer_policy = load_writer_policy(POLICY)
    evidence_rows = collect_failed_trace_evidence(
        aggregate_report_path=source_report,
        max_evidence=None,
        benchmark_name="deepswe",
    )
    generated = generate_test_time_decisions(
        evidence_rows=evidence_rows,
        run_name=TTS_RUN_ID,
        benchmark_name="deepswe",
        writer_policy=writer_policy,
        evaluator_policy=evaluator_policy,
    )
    gate_root = SKILL_ROOT_BASE / f"gate_{gate_index:03d}"
    materialize_gate_library(
        base_skill_root=base_skill_root,
        output_root=gate_root,
        run_name=TTS_RUN_ID,
        promotion_decisions=generated["promotion_decisions"],
        gate_index=gate_index,
        include_base=True,
        clean=True,
    )
    promoted = sum(1 for d in generated["promotion_decisions"] if d.get("decision") == "promote")
    print(f"[gate{gate_index}] materialize: evidence={len(evidence_rows)} promoted={promoted}", flush=True)
    return 0

def main():
    env = load_env()
    env.update({"LLM_PROVIDER": "macaron", "MACARON_REASONING_EFFORT": "none", "MACARON_ENABLE_THINKING": "false",
                "FORCE_DISABLE_THINKING": "1", "PI_THINKING": "off", "CLAUDE_CODE_ATTRIBUTION_HEADER": "0",
                "PI_USE_SKILL_HARNESS_MEMORY": "false", "CLAUDE_USE_SKILL_HARNESS_MEMORY": "false",
                "PI_SKILL_RETRIEVAL_SCOPE": "transfer"})
    # resume from gate2 score_report (gate1/gate2 already done, gate3/4 failed due to key)
    gate2_report = ROOT / "run_logs/cc_deepswe_tts_macaron/cc_deepswe_tts_gate2_macaron_20260811_score_report.json"
    if gate2_report.is_file():
        print(f"[resume] reuse gate2 score_report", flush=True)
        g2 = json.load(open(gate2_report))
        prev_tasks = {t["task_name"]: t["reward"] for t in g2["tasks"]}
        report_path = gate2_report
        resolved = sum(1 for v in prev_tasks.values() if v == 1.0)
        print(f"[gate2] done, cumulative resolved={resolved}/113", flush=True)
        start_gate = 3
    else:
        # frozen: reuse existing job results (already run), regenerate score_report with correct result_path
        import tomllib as _tomllib
        all_tasks = []
        for p in Path(DATASET).iterdir():
            if not (p.is_dir() and (p / "task.toml").is_file()):
                continue
            try:
                cfg = _tomllib.loads((p / "task.toml").read_text(encoding="utf-8"))
                name = (cfg.get("task") or {}).get("name") or f"datacurve/{p.name}"
            except Exception:
                name = f"datacurve/{p.name}"
            all_tasks.append(name)
        frozen_job = "cc_deepswe_tts_frozen_macaron_20260811"
        if (ROOT / "jobs" / frozen_job).is_dir():
            print(f"[frozen] reuse existing job {frozen_job}", flush=True)
        else:
            print(f"[frozen] rerun all 113 tasks", flush=True)
            run_eval(frozen_job, BASE_SKILL, all_tasks, env,
                     ROOT / "run_logs/cc_deepswe_tts_macaron/frozen.log")
        report_path, prev_tasks = make_report(frozen_job, {t: 0.0 for t in all_tasks})
        resolved = sum(1 for v in prev_tasks.values() if v == 1.0)
        print(f"[frozen] done, resolved={resolved}/113", flush=True)

        # Gate 1 learns from reward-zero traces, then uses the configured eval scope.
        pending = [tn for tn, rw in prev_tasks.items() if reward_is_selected_for_tts_evaluation(rw, EVALUATION_SCOPE)]
        print(f"[gate1] materialize from {len(pending)} failures", flush=True)
        rc = materialize_gate(1, report_path, BASE_SKILL)
        if rc != 0:
            print(f"[gate1] materialize failed rc={rc}", flush=True); return
        gate_skill = SKILL_ROOT_BASE / "gate_001"
        if not gate_skill.is_dir():
            print(f"[gate1] skill lib not found, stop", flush=True); return
        print(f"[gate1] rerun {len(pending)} tasks with scope={EVALUATION_SCOPE}", flush=True)
        run_eval(f"cc_deepswe_tts_gate1_macaron_20260811", gate_skill, pending, env,
                 ROOT / "run_logs/cc_deepswe_tts_macaron/gate1.log")
        report_path, prev_tasks = make_report(f"cc_deepswe_tts_gate1_macaron_20260811", prev_tasks)
        resolved = sum(1 for v in prev_tasks.values() if v == 1.0)
        print(f"[gate1] done, cumulative resolved={resolved}/113", flush=True)
        start_gate = 2

    # Later gates reuse the same configurable reward condition.
    for g in range(start_gate, MAX_GATE + 1):
        pending = [tn for tn, rw in prev_tasks.items() if reward_is_selected_for_tts_evaluation(rw, EVALUATION_SCOPE)]
        if not pending:
            print(f"[gate{g}] no pending, stop", flush=True); break
        prev_gate_skill = SKILL_ROOT_BASE / f"gate_{g-1:03d}"
        print(f"[gate{g}] materialize from {len(pending)} failures", flush=True)
        rc = materialize_gate(g, report_path, prev_gate_skill)
        if rc != 0:
            print(f"[gate{g}] materialize failed rc={rc}", flush=True); break
        gate_skill = SKILL_ROOT_BASE / f"gate_{g:03d}"
        if not gate_skill.is_dir():
            print(f"[gate{g}] skill lib not found, stop", flush=True); break
        print(f"[gate{g}] rerun {len(pending)} tasks with scope={EVALUATION_SCOPE}", flush=True)
        run_eval(f"cc_deepswe_tts_gate{g}_macaron_20260811", gate_skill, pending, env,
                 ROOT / f"run_logs/cc_deepswe_tts_macaron/gate{g}.log")
        report_path, prev_tasks = make_report(f"cc_deepswe_tts_gate{g}_macaron_20260811", prev_tasks)
        resolved = sum(1 for v in prev_tasks.values() if v == 1.0)
        print(f"[gate{g}] done, cumulative resolved={resolved}/113", flush=True)

    resolved = sum(1 for v in prev_tasks.values() if v == 1.0)
    print(f"\n=== FINAL: {resolved}/113 = {resolved/113*100:.1f}% ===", flush=True)

if __name__ == "__main__":
    main()
