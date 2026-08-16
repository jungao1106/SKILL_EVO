#!/usr/bin/env python
"""Chain rerun reward!=1 subsets on novita, 4 concurrency, per-setting skills.

For each cell, rerun frozen -> gate1 -> gate2 -> gate3 in chain:
  - frozen: rerun frozen's reward!=1 tasks with frozen skills
  - gate1: rerun (frozen rerun still !=1) tasks with gate1 skills
  - gate2: rerun (gate1 rerun still !=1) tasks with gate2 skills
  - gate3: rerun (gate2 rerun still !=1) tasks with gate3 skills

Each setting uses its own skill library from results/<cell>/<setting>/skills.
Agent timeout 7200, e2b sandbox timeout 10800, concurrency 4, novita provider.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path("/vePFS-Mindverse/user/intern/jungao/SKILLS_EVO")
RESULTS = ROOT / "results"
PY = "/vePFS-Mindverse/user/intern/jungao/Marcronv1_SWE/.venv312/bin/python"
AGENT_TIMEOUT = 7200
E2B_TIMEOUT = 10800
CONCURRENCY = 4

# cells: (cell_dir, harness, dataset, provider_model, settings list)
CELLS = {
    "cc_deepswe": {
        "harness": "claude-code",
        "dataset": "/vePFS-Mindverse/user/intern/jungao/Marcronv1-Coding/deep-swe/tasks",
        "benchmark": "deepswe",
        "settings": ["frozen_skills", "gate1", "gate2", "gate3"],
    },
}


def load_summary(cell: str, setting: str) -> dict:
    p = RESULTS / cell / setting / "summary.json"
    return json.loads(p.read_text())


def not1_tasks(summary: dict) -> list[str]:
    return [t["task_name"] for t in summary["tasks"] if t["reward"] != 1.0]


def write_task_file(cell: str, setting: str, tasks: list[str]) -> Path:
    d = ROOT / "run_logs" / "novita_chain_rerun" / cell
    d.mkdir(parents=True, exist_ok=True)
    f = d / f"{setting}_tasks.txt"
    f.write_text("\n".join(tasks) + "\n")
    return f


def deepswe_leaf_names(full_names: list[str]) -> list[str]:
    """deepswe --task-names-file wants leaf dir names (after last /)."""
    return [n.split("/")[-1] for n in full_names]


def run_setting(cell: str, cfg: dict, setting: str, tasks: list[str]) -> dict:
    """Rerun `tasks` for this setting; return {task_name: reward}."""
    skill_root = RESULTS / cell / setting / "skills"
    job_name = f"novita_rerun_{cell}_{setting}_20260802"
    task_file = write_task_file(cell, setting, tasks)

    # deepswe needs leaf names; swebench uses full swe-bench/xxx names
    if cfg["benchmark"] == "deepswe":
        tf_for_run = write_task_file(cell, setting + "_leaf", deepswe_leaf_names(tasks))
    else:
        tf_for_run = task_file

    env = os.environ.copy()
    # load .env
    env_file = ROOT / ".env"
    if env_file.is_file():
        for line in env_file.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            env[k.strip()] = v.strip().strip('"').strip("'")
    env.update(
        {
            "LLM_PROVIDER": "novita",
            "NOVITA_REASONING_EFFORT": "none",
            "NOVITA_ENABLE_THINKING": "false",
            "FORCE_DISABLE_THINKING": "1",
            "PI_THINKING": "off",
            "CLAUDE_CODE_ATTRIBUTION_HEADER": "0",
            "E2B_CONCURRENCY": str(CONCURRENCY),
            "PI_SKILL_PACK_ROOT": str(skill_root.resolve()),
            "PI_USE_SKILL_HARNESS_MEMORY": "false",
            "CLAUDE_USE_SKILL_HARNESS_MEMORY": "false",
            "PI_SKILL_RETRIEVAL_SCOPE": "transfer",
        }
    )

    runner = "scripts/run_deepswe.py" if cfg["benchmark"] == "deepswe" else "scripts/run_benchmark.py"
    cmd = [
        PY, runner,
        "--dataset", cfg["dataset"],
        "--benchmark-name", cfg["benchmark"],
        "--harness", cfg["harness"],
        "--provider", "novita",
        "--provider-model", "zai-org/glm-5.2",
        "--job-name", job_name,
        "--concurrency", str(CONCURRENCY),
        "--agent-timeout-sec", str(AGENT_TIMEOUT),
        "--agent-setup-timeout-sec", "1200",
        "--e2b-sandbox-timeout-sec", str(E2B_TIMEOUT),
        "--task-names-file", str(tf_for_run),
        "--use-skills",
    ]

    log_path = ROOT / "run_logs" / "novita_chain_rerun" / cell / f"{setting}.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    print(f"[{cell}/{setting}] rerun {len(tasks)} tasks, skills={skill_root.name}", flush=True)
    with log_path.open("w") as log:
        log.write(f"cmd: {' '.join(cmd)}\n\n")
        log.flush()
        subprocess.run(cmd, cwd=ROOT, env=env, stdout=log, stderr=subprocess.STDOUT, check=False)

    # collect results
    job_dir = ROOT / "jobs" / job_name
    results = {}
    for trial_dir in sorted(p for p in job_dir.iterdir() if p.is_dir()):
        rp = trial_dir / "result.json"
        if not rp.is_file():
            continue
        try:
            r = json.loads(rp.read_text(errors="replace"))
        except json.JSONDecodeError:
            continue
        tn = r.get("task_name")
        if not tn:
            continue
        vr = r.get("verifier_result") or {}
        rewards = vr.get("rewards") if isinstance(vr, dict) else {}
        rw = rewards.get("reward") if isinstance(rewards, dict) else None
        try:
            results[tn] = float(rw)
        except (TypeError, ValueError):
            results[tn] = -1.0
    return results


def main() -> None:
    out = ROOT / "run_logs" / "novita_chain_rerun" / "chain_results.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    all_results = {}
    for cell, cfg in CELLS.items():
        print(f"\n=== {cell} ===", flush=True)
        cell_res = {}
        # chain: start from frozen's reward!=1
        prev_summary = load_summary(cell, "frozen_skills")
        pending = not1_tasks(prev_summary)
        for setting in cfg["settings"]:
            if not pending:
                print(f"  {setting}: no pending tasks, skip", flush=True)
                cell_res[setting] = {"ran": 0, "resolved": 0, "still_not1": 0}
                continue
            res = run_setting(cell, cfg, setting, pending)
            resolved = sum(1 for v in res.values() if v == 1.0)
            still = [t for t in pending if res.get(t) != 1.0]
            cell_res[setting] = {
                "ran": len(pending),
                "resolved": resolved,
                "still_not1": len(still),
            }
            print(f"  {setting}: ran={len(pending)} resolved={resolved} still_not1={len(still)}", flush=True)
            pending = still  # chain to next setting
            all_results[cell] = cell_res
            out.write_text(json.dumps(all_results, ensure_ascii=False, indent=2) + "\n")
    print("\nDone. Results:", out)


if __name__ == "__main__":
    main()
