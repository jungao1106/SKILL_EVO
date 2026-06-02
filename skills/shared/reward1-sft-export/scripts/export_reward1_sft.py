#!/usr/bin/env python3
"""Export reward=1 job traces as SWE-smith-style SFT JSONL."""

from __future__ import annotations

import argparse
import json
import re
import statistics
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[4]


ROLE_MAP = {
    "system": "system",
    "human": "user",
    "user": "user",
    "gpt": "assistant",
    "assistant": "assistant",
    "tool": "tool",
}

TOOL_CALL_RE = re.compile(
    r'^\s*<tool_call\s+name="(?P<name>[^"]+)"\s+id="(?P<id>[^"]*)">\n(?P<args>[\s\S]*?)\n</tool_call>\s*$'
)
TOOL_RESULT_RE = re.compile(
    r'^\s*<tool_result\s+name="(?P<name>[^"]+)"\s+id="(?P<id>[^"]*)">\n(?P<result>[\s\S]*?)\n</tool_result>\s*$'
)
FORBIDDEN_ASSISTANT_CONTENT_MARKERS = (
    "</think>",
    "<think>",
    "<tool_call>",
    "</tool_call>",
    "<arg_key>",
    "</arg_key>",
    "<arg_value>",
    "</arg_value>",
)


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8", errors="replace"))


def reward_value(value: Any) -> float | None:
    if isinstance(value, dict):
        for key in ("reward", "resolved", "pass", "success"):
            if key in value:
                return reward_value(value[key])
        if value:
            return reward_value(next(iter(value.values())))
        return None
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def trial_reward(trial_dir: Path) -> float | None:
    reward_path = trial_dir / "verifier" / "reward.txt"
    if reward_path.exists():
        return reward_value(reward_path.read_text(encoding="utf-8", errors="replace").strip())

    result_path = trial_dir / "result.json"
    if not result_path.exists():
        return None
    try:
        result = load_json(result_path)
    except json.JSONDecodeError:
        return None
    for path in (
        ("verifier_result", "rewards", "reward"),
        ("verifier_result", "reward"),
        ("reward",),
        ("agent_result", "reward"),
    ):
        current: Any = result
        for key in path:
            if not isinstance(current, dict) or key not in current:
                current = None
                break
            current = current[key]
        reward = reward_value(current)
        if reward is not None:
            return reward
    return None


def task_info(trial_dir: Path) -> tuple[str, str]:
    result_path = trial_dir / "result.json"
    if result_path.exists():
        try:
            result = load_json(result_path)
        except json.JSONDecodeError:
            result = {}
        task_name = str(result.get("task_name") or "")
        trial_name = str(result.get("trial_name") or trial_dir.name)
        if task_name:
            return task_name, trial_name

    config_path = trial_dir / "config.json"
    if config_path.exists():
        try:
            config = load_json(config_path)
        except json.JSONDecodeError:
            config = {}
        task = config.get("task") if isinstance(config.get("task"), dict) else {}
        task_path = Path(str(task.get("path") or ""))
        source = str(task.get("source") or "")
        if source and task_path.name:
            dataset_prefix = "swegym" if "swegym" in source else source
            return f"{dataset_prefix}/{task_path.name}", str(config.get("trial_name") or trial_dir.name)

    return trial_dir.name.rsplit("__", 1)[0], trial_dir.name


def read_metadata(agent_dir: Path) -> dict[str, Any]:
    for name in ("pi-metadata.json", "mini-metadata.json"):
        path = agent_dir / name
        if path.exists():
            try:
                return load_json(path)
            except json.JSONDecodeError:
                return {}
    return {}


def message_text(message: dict[str, Any]) -> str:
    content = message.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict):
                text = item.get("text") or item.get("content") or item.get("output")
                if text is not None:
                    parts.append(str(text))
        return "".join(parts)
    return str(message.get("text") or message.get("value") or "")


def normalize_messages(sharegpt: dict[str, Any]) -> list[dict[str, Any]]:
    raw_messages = sharegpt.get("messages")
    if not isinstance(raw_messages, list):
        raw_messages = sharegpt.get("conversations")
    if not isinstance(raw_messages, list):
        return []

    messages: list[dict[str, Any]] = []
    for item in raw_messages:
        if not isinstance(item, dict):
            continue
        raw_role = str(item.get("role") or item.get("from") or "").strip()
        role = ROLE_MAP.get(raw_role, raw_role or "user")
        text = str(item.get("content") if item.get("content") is not None else item.get("value") or "")

        tool_call_match = TOOL_CALL_RE.match(text)
        if role == "assistant" and tool_call_match:
            messages.append(
                {
                    "role": "assistant",
                    "content": "",
                    "tool_calls": [
                        {
                            "id": tool_call_match.group("id"),
                            "type": "function",
                            "function": {
                                "name": tool_call_match.group("name"),
                                "arguments": tool_call_match.group("args"),
                            },
                        }
                    ],
                }
            )
            continue

        tool_result_match = TOOL_RESULT_RE.match(text)
        if role == "tool" and tool_result_match:
            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": tool_result_match.group("id"),
                    "content": tool_result_match.group("result"),
                }
            )
            continue

        message: dict[str, Any] = {"role": role, "content": text}
        if isinstance(item.get("tool_calls"), list):
            message["tool_calls"] = item["tool_calls"]
        if item.get("tool_call_id") is not None:
            message["tool_call_id"] = item["tool_call_id"]
        messages.append(message)
    return messages


def missing_tool_result_ids(messages: list[dict[str, Any]]) -> list[str]:
    result_ids = {
        str(message.get("tool_call_id"))
        for message in messages
        if message.get("role") == "tool" and message.get("tool_call_id") is not None
    }
    missing: list[str] = []
    for message in messages:
        if message.get("role") != "assistant":
            continue
        for call in message.get("tool_calls") or []:
            if not isinstance(call, dict):
                continue
            call_id = str(call.get("id") or "")
            if call_id and call_id not in result_ids:
                missing.append(call_id)
    return missing


def forbidden_assistant_content_markers(
    messages: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    hits: list[dict[str, Any]] = []
    for index, message in enumerate(messages):
        if message.get("role") != "assistant":
            continue
        text = message_text(message)
        found = [
            marker
            for marker in FORBIDDEN_ASSISTANT_CONTENT_MARKERS
            if marker in text
        ]
        if found:
            hits.append({"message_index": index, "markers": found})
    return hits


def word_count_record(record: dict[str, Any]) -> int:
    words: list[str] = []
    for message in record.get("messages") or []:
        if isinstance(message, dict):
            words.extend(re.findall(r"\S+", message_text(message)))
            for call in message.get("tool_calls") or []:
                if isinstance(call, dict):
                    function = call.get("function") if isinstance(call.get("function"), dict) else {}
                    words.extend(re.findall(r"\S+", str(function.get("name") or "")))
                    words.extend(re.findall(r"\S+", str(function.get("arguments") or "")))
    return len(words)


def output_paths(prefix: Path) -> dict[str, Path]:
    return {
        "output_jsonl": prefix.with_suffix(".jsonl"),
        "metadata_jsonl": prefix.with_suffix(".metadata.jsonl"),
        "word_counts_jsonl": prefix.with_suffix(".word_counts.jsonl"),
        "stats_json": prefix.with_suffix(".stats.json"),
    }


def export_job(job_dir: Path, dataset: str, out_prefix: Path, require_sharegpt: bool) -> dict[str, Any]:
    job_dir = job_dir if job_dir.is_absolute() else ROOT / job_dir
    out_prefix = out_prefix if out_prefix.is_absolute() else ROOT / out_prefix
    paths = output_paths(out_prefix)
    for path in paths.values():
        path.parent.mkdir(parents=True, exist_ok=True)

    records: list[dict[str, Any]] = []
    metadata_rows: list[dict[str, Any]] = []
    word_rows: list[dict[str, Any]] = []
    skipped = Counter()
    by_job = Counter()
    missing_tool_result_count = 0

    for trial_dir in sorted(path for path in job_dir.iterdir() if path.is_dir()):
        reward = trial_reward(trial_dir)
        if reward != 1.0:
            skipped["non_reward1"] += 1
            continue

        agent_dir = trial_dir / "agent"
        sharegpt_path = agent_dir / "sharegpt.json"
        if not sharegpt_path.exists():
            skipped["missing_sharegpt"] += 1
            if require_sharegpt:
                continue
            messages: list[dict[str, Any]] = []
            sharegpt = {}
        else:
            try:
                sharegpt = load_json(sharegpt_path)
            except json.JSONDecodeError:
                skipped["bad_sharegpt_json"] += 1
                continue
            messages = normalize_messages(sharegpt)
            missing_tool_results = missing_tool_result_ids(messages)
            if missing_tool_results:
                skipped["missing_tool_result_trace"] += 1
                missing_tool_result_count += len(missing_tool_results)
                continue
            if forbidden_assistant_content_markers(messages):
                skipped["forbidden_assistant_content_trace"] += 1
                continue

        if not messages:
            skipped["empty_messages"] += 1
            continue

        task_name, trial_name = task_info(trial_dir)
        metadata = read_metadata(agent_dir)
        source_model = str(
            metadata.get("model")
            or metadata.get("provider_model")
            or sharegpt.get("model")
            or ""
        )
        record_id = f"{dataset}::{job_dir.name}::{trial_name}"
        record = {"id": record_id, "messages": messages}
        words = word_count_record(record)

        records.append(record)
        by_job[job_dir.name] += 1
        word_rows.append({"id": record_id, "word_count": words})
        metadata_rows.append(
            {
                "id": record_id,
                "job": job_dir.name,
                "message_count": len(messages),
                "reward": reward,
                "sharegpt_path": str(sharegpt_path.resolve()) if sharegpt_path.exists() else None,
                "source_model": source_model,
                "source_sharegpt_id": sharegpt.get("id"),
                "task_name": task_name,
                "task_path": str((trial_dir / "config.json").resolve()) if (trial_dir / "config.json").exists() else None,
                "trial_dir": str(trial_dir.resolve()),
                "trial_name": trial_name,
                "word_count": words,
            }
        )

    with paths["output_jsonl"].open("w", encoding="utf-8") as handle:
        for record in records:
            handle.write(json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n")
    with paths["metadata_jsonl"].open("w", encoding="utf-8") as handle:
        for row in metadata_rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")
    with paths["word_counts_jsonl"].open("w", encoding="utf-8") as handle:
        for row in word_rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")

    counts = [row["word_count"] for row in word_rows]
    stats = {
        "by_job": dict(by_job),
        "count": len(records),
        "input": f"{job_dir}/ */agent/sharegpt.json with reward=1",
        "metadata_jsonl": str(paths["metadata_jsonl"].resolve()),
        "output_jsonl": str(paths["output_jsonl"].resolve()),
        "skipped": dict(skipped),
        "missing_tool_results_in_skipped_traces": missing_tool_result_count,
        "word_count_definition": "Count of non-whitespace spans (regex \\S+) over converted message text, including tool call function names/arguments and tool result content; excluding message ids/tool_call_id.",
        "word_counts_jsonl": str(paths["word_counts_jsonl"].resolve()),
        "words": {
            "average": statistics.fmean(counts) if counts else None,
            "max": max(counts) if counts else None,
            "median": statistics.median(counts) if counts else None,
            "min": min(counts) if counts else None,
        },
    }
    paths["stats_json"].write_text(json.dumps(stats, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return stats


def default_prefix(job: Path, dataset: str) -> Path:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    safe_job = re.sub(r"[^A-Za-z0-9_.-]+", "_", job.name).strip("_")
    return ROOT / "data" / f"{dataset}_reward1_{safe_job}_{stamp}"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--job", type=Path, required=True, help="job directory or name under jobs/")
    parser.add_argument("--dataset", default="swegym", help="dataset label used in exported ids")
    parser.add_argument("--out-prefix", type=Path, default=None, help="output path without suffix")
    parser.add_argument("--allow-missing-sharegpt", action="store_true")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    job = args.job
    if not job.exists() and not job.is_absolute():
        job = ROOT / "jobs" / args.job
    out_prefix = args.out_prefix or default_prefix(job, args.dataset)
    stats = export_job(
        job_dir=job,
        dataset=args.dataset,
        out_prefix=out_prefix,
        require_sharegpt=not args.allow_missing_sharegpt,
    )
    print(json.dumps(stats, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
