#!/usr/bin/env python
"""pi_deepswe TTS chain with reward-zero or all-task gate evaluation."""
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
SKILL_ROOT_BASE = ROOT / "skills/test_time/pi_deepswe_novita_v0100_tts_evo_20260807"
TTS_RUN_ID = "pi_deepswe_novita_v0100_tts_evo_20260807"
FROZEN_REPORT = ROOT / "run_logs/pi_deepswe_tts/frozen_merged_score_report.json"
BASE_SKILL = ROOT / "skills/downstream/swebench_verified_glm52_novita_v0100_frozen_direct_20260703_182027/v0100"
POLICY = ROOT / "run_logs/swegym_skill_evo/swegym_novita_glm52_c15_resume_merged_20260630_071956/training/policy_state.json"
MAX_GATE = 4
CONCURRENCY = 4
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
    task_file = write_tasks(tasks, ROOT / f"run_logs/pi_deepswe_tts/{job_name}_tasks.txt")
    cmd = [PY, "scripts/run_deepswe.py",
        "--dataset", DATASET, "--benchmark-name", "deepswe",
        "--harness", "pi", "--provider", "macaron", "--provider-model", "glm-5.2",
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
    out = ROOT / f"run_logs/pi_deepswe_tts/{job_name}_score_report.json"
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
    # gate1: reuse existing score_report (already run), skip rerun
    gate1_report = ROOT / "run_logs/pi_deepswe_tts/pi_deepswe_tts_gate1_20260807_score_report.json"
    if gate1_report.is_file():
        print(f"[gate1] reuse existing score_report", flush=True)
        g1 = json.load(open(gate1_report))
        prev_tasks = {t["task_name"]: t["reward"] for t in g1["tasks"]}
        report_path = gate1_report
    else:
        frozen_report = json.load(open(FROZEN_REPORT))
        prev_tasks = {t["task_name"]: t["reward"] for t in frozen_report["tasks"]}
        pending = [tn for tn, rw in prev_tasks.items() if reward_is_selected_for_tts_evaluation(rw, EVALUATION_SCOPE)]
        print(f"[gate1] rerun {len(pending)} tasks with scope={EVALUATION_SCOPE}", flush=True)
        gate1_skill = SKILL_ROOT_BASE / "gate_001"
        run_eval(f"pi_deepswe_tts_gate1_20260807", gate1_skill, pending, env,
                 ROOT / "run_logs/pi_deepswe_tts/gate1.log")
        report_path, prev_tasks = make_report(f"pi_deepswe_tts_gate1_20260807", prev_tasks)
    resolved = sum(1 for v in prev_tasks.values() if v == 1.0)
    print(f"[gate1] done, cumulative resolved={resolved}/113", flush=True)

    # gate2-4: learn from reward-zero traces, then use the configured eval scope.
    for g in range(2, MAX_GATE + 1):
        pending = [tn for tn, rw in prev_tasks.items() if reward_is_selected_for_tts_evaluation(rw, EVALUATION_SCOPE)]
        if not pending:
            print(f"[gate{g}] no pending, stop", flush=True); break
        # materialize gate g from prev gate's report
        prev_gate_skill = SKILL_ROOT_BASE / f"gate_{g-1:03d}"
        print(f"[gate{g}] materialize from {len(pending)} failures", flush=True)
        rc = materialize_gate(g, report_path, prev_gate_skill)
        if rc != 0:
            print(f"[gate{g}] materialize failed rc={rc}", flush=True); break
        gate_skill = SKILL_ROOT_BASE / f"gate_{g:03d}"
        if not gate_skill.is_dir():
            print(f"[gate{g}] skill lib not found, stop", flush=True); break
        print(f"[gate{g}] rerun {len(pending)} tasks with scope={EVALUATION_SCOPE}", flush=True)
        run_eval(f"pi_deepswe_tts_gate{g}_20260807", gate_skill, pending, env,
                 ROOT / f"run_logs/pi_deepswe_tts/gate{g}.log")
        report_path, prev_tasks = make_report(f"pi_deepswe_tts_gate{g}_20260807", prev_tasks)
        resolved = sum(1 for v in prev_tasks.values() if v == 1.0)
        print(f"[gate{g}] done, cumulative resolved={resolved}/113", flush=True)

    resolved = sum(1 for v in prev_tasks.values() if v == 1.0)
    print(f"\n=== FINAL: {resolved}/113 = {resolved/113*100:.1f}% ===", flush=True)

if __name__ == "__main__":
    main()
