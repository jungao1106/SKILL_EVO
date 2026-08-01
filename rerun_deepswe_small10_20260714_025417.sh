#!/usr/bin/env bash
set -euo pipefail

ROOT="/vePFS-Mindverse/user/intern/jungao/SKILLS_EVO"
DATASET="/vePFS-Mindverse/user/intern/jungao/Marcronv1-Coding/deep-swe/tasks"
RUN_TS="20260714_025417"
OUT_DIR="$ROOT/run_logs/deepswe_small_cc_novita_glm52_${RUN_TS}"

cd "$ROOT"
if [ -f /root/miniforge3/etc/profile.d/conda.sh ]; then
  source /root/miniforge3/etc/profile.d/conda.sh
  conda activate base
fi
set -a; source "$ROOT/.env"; set +a
if true; then :
fi

mkdir -p "$OUT_DIR"

export LLM_PROVIDER=novita
export CLAUDE_CODE_ATTRIBUTION_HEADER=0
export FORCE_DISABLE_THINKING=1
export PYTHONUNBUFFERED=1
export E2B_TEMPLATE_BUILD_CONCURRENCY="${E2B_TEMPLATE_BUILD_CONCURRENCY:-5}"
export PI_SKILL_PACK_ROOT="$ROOT/skills/accepted/v0201"
export PI_SKILL_RETRIEVAL_SCOPE=transfer

TASK_ARGS=(
  --include-task-name abs-module-cache-flags
  --include-task-name abs-stepped-slices
  --include-task-name actionlint-action-pinning-lint
  --include-task-name adaptix-name-mapping-aliases
  --include-task-name aiomonitor-task-snapshots-diff
  --include-task-name anko-default-function-arguments
  --include-task-name anko-typed-variable-bindings
  --include-task-name arcane-drift-detection-baselines
  --include-task-name arktype-json-schema-refs-dependencies
  --include-task-name awilix-async-container-initialization
)

COMMON_ARGS=(
  scripts/run_deepswe.py
  --dataset "$DATASET"
  --benchmark-name deepswe
  --agent claude-code
  --provider novita
  --provider-model zai-org/glm-5.2
  --concurrency 5
  --max-retries 0
  --agent-setup-timeout-sec 1200
  --agent-timeout-sec 7200
  --e2b-sandbox-timeout-sec 9000
  --force-agent-internet
  "${TASK_ARGS[@]}"
)

run_one() {
  local label="$1"
  local skill_flag="$2"
  local job_name="deepswe_cc_novita_glm52_small10_${label}_${RUN_TS}"
  local log_path="$OUT_DIR/${label}.log"
  {
    echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] START label=$label job=$job_name"
    python "${COMMON_ARGS[@]}" \
      --job-name "$job_name" \
      "$skill_flag"
    echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] END label=$label job=$job_name"
  } 2>&1 | tee "$log_path"
}

echo "run_ts=$RUN_TS" | tee "$OUT_DIR/manifest.txt"
{
  echo "PI_SKILL_PACK_ROOT=$PI_SKILL_PACK_ROOT"
  echo "PI_SKILL_RETRIEVAL_SCOPE=$PI_SKILL_RETRIEVAL_SCOPE"
} >> "$OUT_DIR/manifest.txt"
printf '%s\n' "${TASK_ARGS[@]}" >> "$OUT_DIR/manifest.txt"

set +e
run_one "noskills" "--no-skills" &
pid_noskills=$!
sleep 5
run_one "skills" "--use-skills" &
pid_skills=$!

wait "$pid_noskills"
status_noskills=$?
wait "$pid_skills"
status_skills=$?
set -e

{
  echo "noskills_status=$status_noskills"
  echo "skills_status=$status_skills"
  echo "finished_at=$(date -u +%Y-%m-%dT%H:%M:%SZ)"
} | tee -a "$OUT_DIR/manifest.txt"

if [ "$status_noskills" -ne 0 ] || [ "$status_skills" -ne 0 ]; then
  exit 1
fi
