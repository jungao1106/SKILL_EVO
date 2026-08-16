#!/usr/bin/env python
"""Run public skills baseline (SWE-Skills-Bench) on SV + DeepSWE, Pi + CC, macaron."""
import os, subprocess, sys, json
from pathlib import Path

ROOT = Path("/vePFS-Mindverse/user/intern/jungao/SKILLS_EVO")
PY = "/vePFS-Mindverse/user/intern/jungao/Marcronv1_SWE/.venv312/bin/python"
SKILL_ROOT = ROOT / "swe_skills_bench_public"
SV_DATASET = "swe-bench/swe-bench-verified@2"
DS_DATASET = "/vePFS-Mindverse/user/intern/jungao/Marcronv1-Coding/deep-swe/tasks"

def load_env():
    env = os.environ.copy()
    for line in (ROOT / ".env").read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line: continue
        k, v = line.split("=", 1)
        env[k.strip()] = v.strip().strip('"').strip("'")
    return env

def run_sv(harness, env):
    """Run SWE-bench Verified 500 tasks with public skills."""
    job_name = f"public_sv_{harness}_macaron_20260815"
    cmd = [PY, "scripts/run_benchmark.py",
        "--dataset", SV_DATASET, "--benchmark-name", "swebench",
        "--harness", harness, "--provider", "macaron", "--provider-model", "glm-5.2",
        "--job-name", job_name, "--concurrency", "8",
        "--agent-timeout-sec", "7200", "--agent-setup-timeout-sec", "1200",
        "--e2b-sandbox-timeout-sec", "10800", "--use-skills",
        "--task-names-file", str(ROOT / "run_logs/public_baseline/sv_500_tasks.txt")]
    env = dict(env)
    env["PI_SKILL_PACK_ROOT"] = str(SKILL_ROOT.resolve())
    env["E2B_CONCURRENCY"] = "8"
    env["LLM_PROVIDER"] = "macaron"
    env["MACARON_REASONING_EFFORT"] = "none"
    env["MACARON_ENABLE_THINKING"] = "false"
    env["FORCE_DISABLE_THINKING"] = "1"
    env["PI_THINKING"] = "off"
    env["CLAUDE_CODE_ATTRIBUTION_HEADER"] = "0"
    env["PI_USE_SKILL_HARNESS_MEMORY"] = "false"
    env["CLAUDE_USE_SKILL_HARNESS_MEMORY"] = "false"
    env["PI_SKILL_RETRIEVAL_SCOPE"] = "transfer"
    env["PI_MAX_PROMPT_SKILLS"] = "49"
    env["HARBOR_NO_DIFF_RESCUE_MAX"] = "0"
    env["CLAUDE_SKILL_INDEX_MAX_ENTRIES"] = "49"
    log = ROOT / f"run_logs/public_baseline/{job_name}.log"
    log.parent.mkdir(parents=True, exist_ok=True)
    print(f"[SV/{harness}] running {job_name}", flush=True)
    with log.open("w") as f:
        f.write(" ".join(cmd) + "\n\n"); f.flush()
        subprocess.run(cmd, cwd=ROOT, env=env, stdout=f, stderr=subprocess.STDOUT, check=False)
    # collect results
    results = {}
    for t in __import__("glob").glob(str(ROOT / "jobs" / job_name / "*/result.json")):
        try:
            r = json.load(open(t)); vr = r.get("verifier_result") or {}
            rw = (vr.get("rewards") or {}).get("reward") if isinstance(vr, dict) else None
            tn = r.get("task_name")
            if tn:
                try: results[tn] = float(rw)
                except: results[tn] = -1.0
        except: pass
    resolved = sum(1 for v in results.values() if v == 1.0)
    print(f"[SV/{harness}] done: {resolved}/{len(results)} resolved", flush=True)
    return resolved, len(results)

def run_deepswe(harness, env):
    """Run DeepSWE 113 tasks with public skills."""
    job_name = f"public_ds_{harness}_macaron_20260815"
    cmd = [PY, "scripts/run_deepswe.py",
        "--dataset", DS_DATASET, "--benchmark-name", "deepswe",
        "--harness", harness, "--provider", "macaron", "--provider-model", "glm-5.2",
        "--job-name", job_name, "--concurrency", "8",
        "--agent-timeout-sec", "9000", "--agent-setup-timeout-sec", "1200",
        "--e2b-sandbox-timeout-sec", "15000", "--verifier-buffer-sec", "4800",
        "--override-storage-mb", "20480", "--use-skills"]
    env = dict(env)
    env["PI_SKILL_PACK_ROOT"] = str(SKILL_ROOT.resolve())
    env["E2B_CONCURRENCY"] = "8"
    env["LLM_PROVIDER"] = "macaron"
    env["MACARON_REASONING_EFFORT"] = "none"
    env["MACARON_ENABLE_THINKING"] = "false"
    env["FORCE_DISABLE_THINKING"] = "1"
    env["PI_THINKING"] = "off"
    env["CLAUDE_CODE_ATTRIBUTION_HEADER"] = "0"
    env["PI_USE_SKILL_HARNESS_MEMORY"] = "false"
    env["CLAUDE_USE_SKILL_HARNESS_MEMORY"] = "false"
    env["PI_SKILL_RETRIEVAL_SCOPE"] = "transfer"
    env["PI_MAX_PROMPT_SKILLS"] = "49"
    env["HARBOR_NO_DIFF_RESCUE_MAX"] = "0"
    env["CLAUDE_SKILL_INDEX_MAX_ENTRIES"] = "49"
    log = ROOT / f"run_logs/public_baseline/{job_name}.log"
    log.parent.mkdir(parents=True, exist_ok=True)
    print(f"[DS/{harness}] running {job_name}", flush=True)
    with log.open("w") as f:
        f.write(" ".join(cmd) + "\n\n"); f.flush()
        subprocess.run(cmd, cwd=ROOT, env=env, stdout=f, stderr=subprocess.STDOUT, check=False)
    # collect results
    results = {}
    for t in __import__("glob").glob(str(ROOT / "jobs" / job_name / "*/result.json")):
        try:
            r = json.load(open(t)); vr = r.get("verifier_result") or {}
            rw = (vr.get("rewards") or {}).get("reward") if isinstance(vr, dict) else None
            tn = r.get("task_name")
            if tn:
                try: results[tn] = float(rw)
                except: results[tn] = -1.0
        except: pass
    resolved = sum(1 for v in results.values() if v == 1.0)
    print(f"[DS/{harness}] done: {resolved}/{len(results)} resolved", flush=True)
    return resolved, len(results)

def main():
    env = load_env()
    # SV first: Pi then CC
    for harness in ["pi", "claude-code"]:
        run_sv(harness, env)
    # DeepSWE: Pi then CC
    for harness in ["pi", "claude-code"]:
        run_deepswe(harness, env)
    print("\n=== All public skills baselines done ===", flush=True)

if __name__ == "__main__":
    main()
