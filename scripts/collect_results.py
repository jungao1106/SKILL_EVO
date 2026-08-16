#!/usr/bin/env python
"""Aggregate all experiment results into ./results/<cell>/<setting>/{traces,skills}.

For each (cell, setting) we collect every trial across all shards/runs, then
keep only the best trial per task_name:
  - reward==1 preferred over reward!=1
  - among multiple reward==1, keep the newest (latest finished_at)
  - among multiple reward!=1, keep the newest

Shards are merged first (a setting may span 5 shards x 100 tasks, or multiple
run batches). Skills used by each setting are copied into skills/.
"""
from __future__ import annotations

import json
import os
import shutil
from collections import defaultdict
from pathlib import Path
from typing import Any

ROOT = Path("/vePFS-Mindverse/user/intern/jungao/SKILLS_EVO")
RESULTS = ROOT / "results"

# trial files to keep (relative to trial dir). None entry = keep whole subdir.
KEEP_FILES = ["result.json", "config.json", "trial.log"]
KEEP_SUBDIRS = ["agent", "artifacts", "verifier"]
# within agent/, drop these bulky/redundant files
AGENT_DROP = {
    "claude-config",  # claude session state, huge, not a trace
    "openai-reasoning-proxy.log",
    "pi-stderr.txt",
    "claude-agent-sdk-stderr.txt",
}


def trial_reward(result: dict[str, Any]) -> float:
    vr = result.get("verifier_result") or {}
    rewards = vr.get("rewards") if isinstance(vr, dict) else {}
    if not isinstance(rewards, dict):
        return 0.0
    r = rewards.get("reward")
    try:
        return float(r)
    except (TypeError, ValueError):
        return -1.0


def trial_finished(result: dict[str, Any]) -> str:
    return str(result.get("finished_at") or result.get("started_at") or "")


def load_trial(trial_dir: Path) -> dict[str, Any] | None:
    rp = trial_dir / "result.json"
    if not rp.is_file():
        return None
    try:
        return json.loads(rp.read_text(errors="replace"))
    except json.JSONDecodeError:
        return None


def collect_trials(job_dirs: list[Path]) -> dict[str, list[dict]]:
    """task_name -> list of {trial_dir, result, reward, finished}."""
    by_task: dict[str, list[dict]] = defaultdict(list)
    for jd in job_dirs:
        if not jd.is_dir():
            continue
        for trial_dir in sorted(p for p in jd.iterdir() if p.is_dir()):
            r = load_trial(trial_dir)
            if r is None:
                continue
            tn = r.get("task_name") or trial_dir.name
            by_task[tn].append(
                {
                    "trial_dir": trial_dir,
                    "result": r,
                    "reward": trial_reward(r),
                    "finished": trial_finished(r),
                }
            )
    return by_task


def pick_best(trials: list[dict]) -> dict:
    """Best trial: reward==1 preferred; tie-break by newest finished_at."""
    ones = [t for t in trials if t["reward"] == 1.0]
    pool = ones if ones else trials
    pool.sort(key=lambda t: t["finished"], reverse=True)
    return pool[0]


def copy_trial(src: Path, dst: Path) -> None:
    dst.mkdir(parents=True, exist_ok=True)
    for name in KEEP_FILES:
        s = src / name
        if s.is_file():
            shutil.copy2(s, dst / name)
    for sub in KEEP_SUBDIRS:
        s = src / sub
        if not s.is_dir():
            continue
        d = dst / sub
        d.mkdir(parents=True, exist_ok=True)
        for item in s.iterdir():
            if item.name in AGENT_DROP and sub == "agent":
                continue
            if item.is_dir():
                # skip nested claude-config etc inside agent
                if sub == "agent" and item.name in AGENT_DROP:
                    continue
                shutil.copytree(item, d / item.name, dirs_exist_ok=True)
            else:
                shutil.copy2(item, d / item.name)


def copy_skills(src: Path, dst: Path) -> None:
    dst.mkdir(parents=True, exist_ok=True)
    if not src.is_dir():
        (dst / "EMPTY").write_text("no skills used in this setting\n")
        return
    for item in src.iterdir():
        target = dst / item.name
        if item.is_dir():
            shutil.copytree(item, target, dirs_exist_ok=True)
        else:
            shutil.copy2(item, target)


def write_summary(setting_dir: Path, setting: str, by_task: dict[str, list], best: dict[str, dict]) -> None:
    resolved = sum(1 for t in best.values() if t["reward"] == 1.0)
    total = len(best)
    rows = []
    for tn in sorted(best):
        t = best[tn]
        try:
            src = str(t["trial_dir"].resolve().relative_to(ROOT))
        except ValueError:
            src = str(t["trial_dir"].resolve())
        rows.append(
            {
                "task_name": tn,
                "reward": t["reward"],
                "finished_at": t["finished"],
                "source_trial": src,
            }
        )
    summary = {
        "setting": setting,
        "total_tasks": total,
        "resolved": resolved,
        "resolve_rate": round(resolved / total, 4) if total else 0.0,
        "tasks": rows,
    }
    (setting_dir / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n"
    )
    print(f"  {setting}: {resolved}/{total} resolved ({summary['resolve_rate']})")


def build_setting(cell_dir: Path, setting: str, job_dirs: list[Path], skill_root: Path | None) -> None:
    setting_dir = cell_dir / setting
    if setting_dir.exists():
        shutil.rmtree(setting_dir)
    traces_dir = setting_dir / "traces"
    skills_dir = setting_dir / "skills"
    by_task = collect_trials(job_dirs)
    best = {tn: pick_best(ts) for tn, ts in by_task.items()}
    for tn, t in best.items():
        safe = tn.replace("/", "__")
        copy_trial(t["trial_dir"], traces_dir / safe)
    if skill_root is None:
        skills_dir.mkdir(parents=True, exist_ok=True)
        (skills_dir / "EMPTY").write_text("no skills used in this setting\n")
    else:
        copy_skills(skill_root, skills_dir)
    write_summary(setting_dir, setting, by_task, best)


# ---- job dir globs (shards merged by collect_trials) ----
PI_SV = "jobs/swebench_verified_glm52_novita"
CC_SV = "jobs/swebench_verified_cc_novita_glm52_v0201"
MACRON_SWE = "/vePFS-Mindverse/user/intern/jungao/Marcronv1_SWE/jobs"

SETTINGS: dict[str, dict[str, dict]] = {
    "pi_swebench_verified": {
        "no_skills": {
            "jobs": [Path(f"{PI_SV}_noskills_baseline_20260704_032544_{s}_baseline_noskills") for s in ["001_100","101_200","201_300","301_400","401_500"]],
            "skills": None,
        },
        "frozen_skills": {
            "jobs": [Path(f"{PI_SV}_v0100_frozen_direct_20260703_182027_{s}_skills") for s in ["001_100","101_200","201_300","301_400","401_500"]],
            "skills": ROOT / "skills/accepted/v0100",
        },
        "gate1": {
            "jobs": [Path(f"jobs/swebench_verified_tts_evo_from_direct_failures_20260704_gate001_verified_eval_{s}_skills") for s in ["001_100","101_200","201_300","301_400","401_500"]],
            "skills": ROOT / "skills/test_time/swebench_verified_tts_evo_from_direct_failures_20260704/gate_001",
        },
        "gate2": {
            "jobs": [Path(f"jobs/swebench_verified_tts_evo_from_direct_failures_20260704_gate002_verified_eval_{s}_skills") for s in ["001_100","101_200","201_300","301_400","401_500"]],
            "skills": ROOT / "skills/test_time/swebench_verified_tts_evo_from_direct_failures_20260704/gate_002",
        },
        "gate3": {
            "jobs": [Path("jobs/swebench_verified_tts_evo_from_direct_failures_20260704_gate003_on_gate002_unresolved_subset_eval")],
            "skills": ROOT / "skills/test_time/swebench_verified_tts_evo_from_direct_failures_20260704/gate_003",
        },
        "gate4": {
            "jobs": [Path("jobs/swebench_verified_tts_evo_from_direct_failures_20260704_gate004_on_gate003_unresolved_subset_eval")],
            "skills": ROOT / "skills/test_time/swebench_verified_tts_evo_from_direct_failures_20260704/gate_004",
        },
    },
    "cc_swebench_verified": {
        "no_skills": {
            "jobs": [Path(f"{MACRON_SWE}/claude_novita_glm52_swebench_verified_noskill_{s}_c20_t900_20260623_0152") for s in ["001_100","101_200","201_300","301_400","401_500"]],
            "skills": None,
        },
        "frozen_skills": {
            "jobs": [Path(f"{CC_SV}_frozen_downstream_20260711_074608_{s}_skills") for s in ["001_100","101_200","201_300","301_400","401_500"]],
            "skills": ROOT / "skills/downstream/swebench_verified_cc_novita_glm52_v0201_frozen_downstream_20260711_074608/v0201",
        },
        "gate1": {
            "jobs": [Path(f"{CC_SV}_tts_evo_20260711_074608_gate001_verified_eval_{s}_skills") for s in ["001_100","101_200","201_300","301_400","401_500"]],
            "skills": ROOT / "skills/test_time/swebench_verified_cc_novita_glm52_v0201_tts_evo_20260711_074608/gate_001",
        },
        "gate2": {"jobs": [Path(f"{CC_SV}_tts_evo_20260711_074608_gate002_on_gate001_unresolved_subset_eval")], "skills": ROOT / "skills/test_time/swebench_verified_cc_novita_glm52_v0201_tts_evo_20260711_074608/gate_002"},
        "gate3": {"jobs": [Path(f"{CC_SV}_tts_evo_20260711_074608_gate003_on_gate002_unresolved_subset_eval")], "skills": ROOT / "skills/test_time/swebench_verified_cc_novita_glm52_v0201_tts_evo_20260711_074608/gate_003"},
        "gate4": {"jobs": [Path(f"{CC_SV}_tts_evo_20260711_074608_gate004_on_gate003_unresolved_subset_eval")], "skills": ROOT / "skills/test_time/swebench_verified_cc_novita_glm52_v0201_tts_evo_20260711_074608/gate_004"},
        "gate5": {"jobs": [Path(f"{CC_SV}_tts_evo_20260711_074608_gate005_on_gate004_unresolved_subset_eval")], "skills": ROOT / "skills/test_time/swebench_verified_cc_novita_glm52_v0201_tts_evo_20260711_074608/gate_005"},
        "gate6": {"jobs": [Path(f"{CC_SV}_tts_evo_20260711_074608_gate006_on_gate005_unresolved_subset_eval")], "skills": ROOT / "skills/test_time/swebench_verified_cc_novita_glm52_v0201_tts_evo_20260711_074608/gate_006"},
        "gate7": {"jobs": [Path(f"{CC_SV}_tts_evo_20260711_074608_gate007_on_gate006_unresolved_subset_eval")], "skills": ROOT / "skills/test_time/swebench_verified_cc_novita_glm52_v0201_tts_evo_20260711_074608/gate_007"},
        "gate8": {"jobs": [Path(f"{CC_SV}_tts_evo_20260711_074608_gate008_on_gate007_unresolved_subset_eval")], "skills": ROOT / "skills/test_time/swebench_verified_cc_novita_glm52_v0201_tts_evo_20260711_074608/gate_008"},
    },
    "cc_deepswe": {
        # macaron line (07-16): complete frozen + gate1-3, single provider, subset-composition
        "frozen_skills": {
            "jobs": [Path("run_logs/deepswe_commit_worktrees/macaron_e9203f0/jobs/deepswe_cc_macaron_glm52_cn_e9203f0_c8_frozen_20260716_183006")],
            "skills": ROOT / "skills/downstream/swebench_verified_cc_novita_glm52_v0201_frozen_downstream_20260711_074608/v0201",
        },
        "gate1": {
            "jobs": [Path("run_logs/deepswe_commit_worktrees/macaron_e9203f0/jobs/deepswe_cc_macaron_glm52_cn_e9203f0_c8_tts_evo_20260716_183006_gate001_eval")],
            "skills": Path("run_logs/deepswe_commit_worktrees/macaron_e9203f0/skills/test_time/deepswe_cc_macaron_glm52_cn_e9203f0_c8_tts_evo_20260716_183006/gate_001"),
        },
        "gate2": {
            "jobs": [Path("run_logs/deepswe_commit_worktrees/macaron_e9203f0/jobs/deepswe_cc_macaron_glm52_cn_e9203f0_c8_tts_evo_20260716_183006_gate002_on_gate001_unresolved_subset_eval_278c123944_af8cd434b7")],
            "skills": Path("run_logs/deepswe_commit_worktrees/macaron_e9203f0/skills/test_time/deepswe_cc_macaron_glm52_cn_e9203f0_c8_tts_evo_20260716_183006/gate_002"),
        },
        "gate3": {
            "jobs": [Path("run_logs/deepswe_commit_worktrees/macaron_e9203f0/jobs/deepswe_cc_macaron_glm52_cn_e9203f0_c8_tts_evo_20260716_183006_gate003_on_gate002_unresolved_subset_eval_8a5dcb7b3e_80460ec4fd")],
            "skills": Path("run_logs/deepswe_commit_worktrees/macaron_e9203f0/skills/test_time/deepswe_cc_macaron_glm52_cn_e9203f0_c8_tts_evo_20260716_183006/gate_003"),
        },
    },
    # pi_deepswe: no jobs exist
}


def main() -> None:
    if RESULTS.exists():
        shutil.rmtree(RESULTS)
    RESULTS.mkdir(parents=True)
    for cell, settings in SETTINGS.items():
        cell_dir = RESULTS / cell
        cell_dir.mkdir(parents=True, exist_ok=True)
        print(f"\n=== {cell} ===")
        for setting, cfg in settings.items():
            job_dirs = cfg["jobs"]
            existing = [d for d in job_dirs if d.is_dir()]
            if not existing:
                print(f"  {setting}: [no jobs found, skipped]")
                continue
            build_setting(cell_dir, setting, existing, cfg.get("skills"))
    # pi_deepswe empty marker
    pd = RESULTS / "pi_deepswe"
    pd.mkdir(parents=True, exist_ok=True)
    (pd / "NO_RUN.txt").write_text("Pi harness did not run DeepSWE; no jobs exist.\n")
    print("\nDone. Results at", RESULTS)


if __name__ == "__main__":
    main()
