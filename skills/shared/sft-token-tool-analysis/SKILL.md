---
name: sft-token-tool-analysis
description: "Analyze SWE-style SFT JSONL datasets for token counts, tool-call behavior, tool-result volume, coverage, extremes, and task-key overlap. Use when comparing reward=1 exports, SWE-Gym/SWE-Smith/SWE-Bench training sets, retry/error-set exports, or previous-vs-current dataset collections."
---

# SFT Token Tool Analysis

Use the bundled script from the `Marcronv1_SWE` repository root to compare one
or more SFT JSONL files shaped as:

```json
{"id": "...", "messages": [...]}
```

The script counts tokens with `tiktoken cl100k_base`, including
`messages[].content` plus structured assistant `tool_calls` function
`name/arguments`. It does not add ChatML framing overhead.

It also reports:

- sample count and unique task count
- token count distribution
- structured tool-call distribution
- tool-result token volume
- edit/write/test-command/tool-error coverage
- min/max/extreme samples
- repository/task-family distribution
- pairwise task-key overlap between datasets

Metadata is inferred from `<input>.metadata.jsonl` when present. Pass explicit
metadata files when the sidecar name is different:

```bash
python skills/shared/sft-token-tool-analysis/scripts/analyze_sft_token_tool.py \
  --dataset "Previous=data/swegym_reward1_full_pi_novita_c20_20260518_codex.jsonl" \
  --dataset "Error set=data/swegym_retry_failed_error_local_reward1_sft_20260520_1459.jsonl" \
  --out-md run_logs/swegym_previous_vs_error_set_token_tool_analysis_20260521.md \
  --out-json run_logs/swegym_previous_vs_error_set_token_tool_analysis_20260521.json
```

Use stable labels in `--dataset LABEL=PATH`; the labels are reused in the
Markdown and JSON reports.
