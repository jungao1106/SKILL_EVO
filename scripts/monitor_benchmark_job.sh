#!/usr/bin/env bash
set -euo pipefail

if [[ $# -lt 1 || $# -gt 2 ]]; then
  echo "usage: $0 JOB_NAME [INTERVAL_SEC]" >&2
  exit 2
fi

job="$1"
interval="${2:-300}"
root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
job_dir="${root}/jobs/${job}"
run_log="${root}/run_logs/${job}.out"

while true; do
  timestamp="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  if tmux has-session -t "${job}" 2>/dev/null; then
    running="yes"
  else
    running="no"
  fi

  dirs="$(find "${job_dir}" -mindepth 1 -maxdepth 1 -type d 2>/dev/null | wc -l)"
  results="$(find "${job_dir}" -mindepth 2 -maxdepth 2 -name result.json 2>/dev/null | wc -l)"
  exceptions="$(find "${job_dir}" -mindepth 2 -maxdepth 2 -name exception.txt 2>/dev/null | wc -l)"
  pi_events="$(find "${job_dir}" -mindepth 3 -maxdepth 3 -path '*/agent/pi-events.jsonl' 2>/dev/null | wc -l)"
  rewards="$(find "${job_dir}" -mindepth 3 -maxdepth 3 -path '*/verifier/reward.txt' 2>/dev/null | wc -l)"

  printf '[%s] running=%s dirs=%s results=%s exceptions=%s pi_events=%s rewards=%s\n' \
    "${timestamp}" "${running}" "${dirs}" "${results}" "${exceptions}" "${pi_events}" "${rewards}"

  if [[ -f "${run_log}" ]]; then
    tail -n 8 "${run_log}" | sed 's/^/[tail] /'
  fi
  printf '\n'

  if [[ "${running}" == "no" ]]; then
    break
  fi
  sleep "${interval}"
done
