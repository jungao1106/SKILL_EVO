---
name: reward1-sft-export
description: "Export reward=1 benchmark traces from a Marcronv1_SWE job into SWE-smith-style SFT JSONL. Use for SWE-Gym, SWE-smith, SWE-Bench, Harbor, Pi, or mini-SWE-agent jobs when successful agent/sharegpt.json traces need to become {id, messages} training data with metadata, word counts, and stats sidecars."
---

# Reward=1 SFT Export

Use the bundled script to convert one completed job into the same SFT shape as
`data/swesmith_reward1_lx_*.jsonl`:

```json
{"id": "...", "messages": [...]}
```

The script scans `jobs/<job>/*`, keeps trials whose reward is `1`, reads
`agent/sharegpt.json`, normalizes ShareGPT `conversations` into OpenAI-style
`messages`, skips traces with assistant tool calls that are missing matching
tool result messages, and writes:

- `<prefix>.jsonl`
- `<prefix>.metadata.jsonl`
- `<prefix>.word_counts.jsonl`
- `<prefix>.stats.json`

Run from the `Marcronv1_SWE` repo root:

```bash
python skills/shared/reward1-sft-export/scripts/export_reward1_sft.py \
  --job jobs/<job-name> \
  --dataset swegym \
  --out-prefix data/<output-prefix>
```

For repeatable outputs, pass an explicit `--out-prefix` rather than relying on
the timestamp default.
