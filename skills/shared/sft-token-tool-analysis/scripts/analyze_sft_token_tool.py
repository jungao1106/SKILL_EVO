#!/usr/bin/env python3
"""Analyze SWE-style SFT JSONL token, tool, and task-overlap statistics."""

from __future__ import annotations

import argparse
import json
import math
import re
import statistics
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

try:
    import tiktoken
except Exception:  # pragma: no cover - optional fallback for bare environments.
    tiktoken = None


ROOT = Path(__file__).resolve().parents[4]
TEST_COMMAND_RE = re.compile(
    r"\b(pytest|tox|nox|unittest|runtests?|npm\s+test|yarn\s+test|pnpm\s+test|"
    r"go\s+test|cargo\s+test|mvn\s+test|gradle\s+test|bazel\s+test|ctest|rspec)\b",
    re.IGNORECASE,
)
TOOL_ERROR_PREFIX_RE = re.compile(
    r"^\s*(?:"
    r"\[error\]|"
    r"Validation failed|"
    r"Path not found|"
    r"ENOENT:|"
    r"\[fd error\]|"
    r"Could not find|"
    r"No changes made|"
    r"Found \d+ occurrences|"
    r"Command timed out after"
    r")",
    re.IGNORECASE,
)


def repo_path(path: str | Path) -> Path:
    value = Path(path)
    return value if value.is_absolute() else ROOT / value


def display_path(path: Path) -> str:
    try:
        return str(path.relative_to(ROOT))
    except ValueError:
        return str(path)


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8", errors="replace") as handle:
        for line_number, line in enumerate(handle, 1):
            stripped = line.strip()
            if not stripped:
                continue
            try:
                row = json.loads(stripped)
            except json.JSONDecodeError as exc:
                raise SystemExit(f"{path}:{line_number}: invalid JSON: {exc}") from exc
            if isinstance(row, dict):
                rows.append(row)
    return rows


def load_metadata(path: Path | None) -> dict[str, dict[str, Any]]:
    if path is None or not path.exists():
        return {}
    rows = load_jsonl(path)
    metadata: dict[str, dict[str, Any]] = {}
    for row in rows:
        row_id = row.get("id")
        if row_id is not None:
            metadata[str(row_id)] = row
    return metadata


def infer_metadata_path(dataset_path: Path) -> Path:
    return dataset_path.with_suffix(".metadata.jsonl")


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
                value = item.get("text") or item.get("content") or item.get("output")
                if value is not None:
                    parts.append(str(value))
        return "".join(parts)
    if content is None:
        return str(message.get("text") or message.get("value") or "")
    return json.dumps(content, ensure_ascii=False, sort_keys=True)


def tool_function(call: dict[str, Any]) -> tuple[str, str]:
    function = call.get("function") if isinstance(call.get("function"), dict) else {}
    name = str(function.get("name") or call.get("name") or "")
    arguments = function.get("arguments", call.get("arguments", ""))
    if isinstance(arguments, str):
        argument_text = arguments
    else:
        argument_text = json.dumps(arguments, ensure_ascii=False, sort_keys=True)
    return name, argument_text


def parsed_json(value: str) -> Any:
    try:
        return json.loads(value)
    except Exception:
        return None


def command_text_from_arguments(argument_text: str) -> str:
    parsed = parsed_json(argument_text)
    if isinstance(parsed, dict):
        command_parts: list[str] = []
        for key in ("cmd", "command", "script", "args"):
            value = parsed.get(key)
            if isinstance(value, str):
                command_parts.append(value)
            elif isinstance(value, list):
                command_parts.extend(str(item) for item in value)
        if command_parts:
            return " ".join(command_parts)
    return argument_text


def tool_result_inner_text(raw_text: str) -> str:
    parsed = parsed_json(raw_text)
    if not isinstance(parsed, dict):
        return raw_text

    content = parsed.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict):
                value = item.get("text") or item.get("content") or item.get("output")
                if value is not None:
                    parts.append(str(value))
        return "\n".join(parts)
    return raw_text


def get_encoder() -> Any:
    if tiktoken is None:
        return None
    return tiktoken.get_encoding("cl100k_base")


def count_tokens(text: str, encoder: Any) -> int:
    if not text:
        return 0
    if encoder is not None:
        return len(encoder.encode(text))
    return len(re.findall(r"\S+|\s+", text))


def percentile(values: list[float], pct: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    if len(ordered) == 1:
        return float(ordered[0])
    position = (len(ordered) - 1) * pct
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return float(ordered[lower])
    weight = position - lower
    return float(ordered[lower] * (1 - weight) + ordered[upper] * weight)


def summarize(values: list[float]) -> dict[str, float | int | None]:
    if not values:
        return {
            "count": 0,
            "sum": 0,
            "min": None,
            "max": None,
            "mean": None,
            "median": None,
            "p90": None,
        }
    return {
        "count": len(values),
        "sum": sum(values),
        "min": min(values),
        "max": max(values),
        "mean": statistics.fmean(values),
        "median": statistics.median(values),
        "p90": percentile(values, 0.90),
    }


def format_number(value: float | int | None, digits: int = 1) -> str:
    if value is None:
        return "-"
    if isinstance(value, int):
        return f"{value:,}"
    if abs(value - round(value)) < 1e-9:
        return f"{round(value):,}"
    return f"{value:,.{digits}f}"


def format_ratio(value: float | None, digits: int = 2) -> str:
    if value is None:
        return "-"
    return f"{value:.{digits}f}x"


def is_tool_error(message: dict[str, Any]) -> bool:
    text = message_text(message)
    parsed = parsed_json(text)
    if isinstance(parsed, dict):
        for key in ("is_error", "isError", "error", "failed"):
            if parsed.get(key):
                return True
        if str(parsed.get("type") or "").lower() == "error":
            return True
    inner_text = tool_result_inner_text(text)
    return bool(TOOL_ERROR_PREFIX_RE.search(text) or TOOL_ERROR_PREFIX_RE.search(inner_text))


def task_key_for(record: dict[str, Any], metadata: dict[str, Any]) -> str:
    for key in ("task_name", "task_id", "instance_id"):
        value = metadata.get(key)
        if value:
            return str(value)
    trial_name = str(metadata.get("trial_name") or "")
    if trial_name:
        return trial_name.rsplit("__", 1)[0]
    record_id = str(record.get("id") or "")
    trial = record_id.split("::")[-1] if record_id else ""
    if trial:
        return trial.rsplit("__", 1)[0]
    return record_id


def repository_key_for(task_key: str) -> str:
    task = task_key.split("/", 1)[-1]
    parts = task.split("__")
    if len(parts) >= 2:
        repository_name = re.sub(r"-\d+$", "", parts[1])
        return f"{parts[0]}__{repository_name}"
    return task


def analyze_record(record: dict[str, Any], metadata: dict[str, Any], encoder: Any) -> dict[str, Any]:
    messages = record.get("messages") if isinstance(record.get("messages"), list) else []
    token_count = 0
    tool_result_tokens = 0
    tool_call_count = 0
    tool_result_count = 0
    test_command_calls = 0
    tool_error_results = 0
    tool_names: Counter[str] = Counter()
    result_ids: set[str] = set()
    call_ids: list[str] = []

    for message in messages:
        if not isinstance(message, dict):
            continue
        text = message_text(message)
        token_count += count_tokens(text, encoder)

        if message.get("role") == "tool":
            tool_result_count += 1
            tool_result_tokens += count_tokens(text, encoder)
            if message.get("tool_call_id") is not None:
                result_ids.add(str(message.get("tool_call_id")))
            if is_tool_error(message):
                tool_error_results += 1

        for call in message.get("tool_calls") or []:
            if not isinstance(call, dict):
                continue
            name, argument_text = tool_function(call)
            token_count += count_tokens(name, encoder)
            token_count += count_tokens(argument_text, encoder)
            tool_call_count += 1
            tool_names[name or "<unknown>"] += 1
            if call.get("id") is not None:
                call_ids.append(str(call.get("id")))
            if name == "bash" and TEST_COMMAND_RE.search(command_text_from_arguments(argument_text)):
                test_command_calls += 1

    edit_calls = tool_names.get("edit", 0)
    write_calls = tool_names.get("write", 0)
    missing_result_count = sum(1 for call_id in call_ids if call_id not in result_ids)

    return {
        "id": str(record.get("id") or ""),
        "task_key": task_key_for(record, metadata),
        "tokens": token_count,
        "messages": len(messages),
        "tool_calls": tool_call_count,
        "tool_results": tool_result_count,
        "tool_result_tokens": tool_result_tokens,
        "tool_names": dict(tool_names),
        "bash_calls": tool_names.get("bash", 0),
        "test_command_calls": test_command_calls,
        "edit_calls": edit_calls,
        "write_calls": write_calls,
        "tool_error_results": tool_error_results,
        "missing_tool_result_count": missing_result_count,
        "has_tool_calls": tool_call_count > 0,
        "has_edit_or_write": (edit_calls + write_calls) > 0,
        "has_test_command": test_command_calls > 0,
        "has_tool_error": tool_error_results > 0,
    }


def load_dataset(spec: dict[str, Any], encoder: Any) -> dict[str, Any]:
    path = repo_path(spec["path"])
    metadata_path = repo_path(spec["metadata"]) if spec.get("metadata") else infer_metadata_path(path)
    records = load_jsonl(path)
    metadata_by_id = load_metadata(metadata_path)

    sample_metrics: list[dict[str, Any]] = []
    tool_names: Counter[str] = Counter()
    for record in records:
        metadata = metadata_by_id.get(str(record.get("id") or ""), {})
        metric = analyze_record(record, metadata, encoder)
        sample_metrics.append(metric)
        tool_names.update(metric["tool_names"])

    ids = [metric["id"] for metric in sample_metrics if metric["id"]]
    task_keys = [metric["task_key"] for metric in sample_metrics if metric["task_key"]]
    repository_counts = Counter(repository_key_for(task_key) for task_key in task_keys)
    duplicate_ids = len(ids) - len(set(ids))
    duplicate_tasks = len(task_keys) - len(set(task_keys))

    aggregate = {
        "label": spec["label"],
        "path": display_path(path),
        "metadata_path": display_path(metadata_path) if metadata_path.exists() else None,
        "sample_count": len(sample_metrics),
        "unique_id_count": len(set(ids)),
        "duplicate_id_count": duplicate_ids,
        "unique_task_count": len(set(task_keys)),
        "duplicate_task_count": duplicate_tasks,
        "token_stats": summarize([metric["tokens"] for metric in sample_metrics]),
        "tool_call_stats": summarize([metric["tool_calls"] for metric in sample_metrics]),
        "message_stats": summarize([metric["messages"] for metric in sample_metrics]),
        "tool_result_stats": summarize([metric["tool_results"] for metric in sample_metrics]),
        "tool_result_token_stats": summarize([metric["tool_result_tokens"] for metric in sample_metrics]),
        "bash_call_stats": summarize([metric["bash_calls"] for metric in sample_metrics]),
        "test_command_call_stats": summarize([metric["test_command_calls"] for metric in sample_metrics]),
        "edit_call_stats": summarize([metric["edit_calls"] for metric in sample_metrics]),
        "write_call_stats": summarize([metric["write_calls"] for metric in sample_metrics]),
        "tool_error_result_stats": summarize([metric["tool_error_results"] for metric in sample_metrics]),
        "tool_names": dict(sorted(tool_names.items())),
        "repository_counts": dict(sorted(repository_counts.items())),
        "coverage": {
            "with_tool_calls": sum(1 for metric in sample_metrics if metric["has_tool_calls"]),
            "with_edit_or_write": sum(1 for metric in sample_metrics if metric["has_edit_or_write"]),
            "with_test_command": sum(1 for metric in sample_metrics if metric["has_test_command"]),
            "with_tool_error": sum(1 for metric in sample_metrics if metric["has_tool_error"]),
            "with_missing_tool_results": sum(
                1 for metric in sample_metrics if metric["missing_tool_result_count"] > 0
            ),
        },
        "missing_tool_result_total": sum(metric["missing_tool_result_count"] for metric in sample_metrics),
        "samples": sample_metrics,
    }
    aggregate["extremes"] = extremes(sample_metrics)
    return aggregate


def extremes(samples: list[dict[str, Any]]) -> dict[str, dict[str, Any] | None]:
    if not samples:
        return {"token_min": None, "token_max": None, "tool_calls_max": None}
    token_min = min(samples, key=lambda item: item["tokens"])
    token_max = max(samples, key=lambda item: item["tokens"])
    tool_calls_max = max(samples, key=lambda item: item["tool_calls"])
    return {
        "token_min": compact_sample(token_min),
        "token_max": compact_sample(token_max),
        "tool_calls_max": compact_sample(tool_calls_max),
    }


def compact_sample(sample: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": sample["id"],
        "task_key": sample["task_key"],
        "tokens": sample["tokens"],
        "tool_calls": sample["tool_calls"],
        "messages": sample["messages"],
    }


def pairwise_overlap(datasets: list[dict[str, Any]]) -> list[dict[str, Any]]:
    overlaps: list[dict[str, Any]] = []
    for left_index, left in enumerate(datasets):
        left_tasks = {sample["task_key"] for sample in left["samples"] if sample["task_key"]}
        left_ids = {sample["id"] for sample in left["samples"] if sample["id"]}
        for right in datasets[left_index + 1 :]:
            right_tasks = {sample["task_key"] for sample in right["samples"] if sample["task_key"]}
            right_ids = {sample["id"] for sample in right["samples"] if sample["id"]}
            task_intersection = sorted(left_tasks & right_tasks)
            left_only = sorted(left_tasks - right_tasks)
            right_only = sorted(right_tasks - left_tasks)
            id_intersection = sorted(left_ids & right_ids)
            overlaps.append(
                {
                    "left": left["label"],
                    "right": right["label"],
                    "left_unique_tasks": len(left_tasks),
                    "right_unique_tasks": len(right_tasks),
                    "task_intersection": len(task_intersection),
                    "left_only_tasks": len(left_only),
                    "right_only_tasks": len(right_only),
                    "task_union": len(left_tasks | right_tasks),
                    "task_overlap_left_pct": (len(task_intersection) / len(left_tasks) * 100)
                    if left_tasks
                    else None,
                    "task_overlap_right_pct": (len(task_intersection) / len(right_tasks) * 100)
                    if right_tasks
                    else None,
                    "id_intersection": len(id_intersection),
                    "task_intersection_examples": task_intersection[:20],
                    "left_only_examples": left_only[:20],
                    "right_only_examples": right_only[:20],
                }
            )
    return overlaps


def stat_value(dataset: dict[str, Any], stat_name: str, key: str) -> float | int | None:
    return dataset[stat_name].get(key)


def ratio(left: float | int | None, right: float | int | None) -> float | None:
    if left is None or right in (None, 0):
        return None
    return float(left) / float(right)


def markdown_table(headers: list[str], rows: list[list[str]]) -> list[str]:
    lines = ["| " + " | ".join(headers) + " |"]
    lines.append("|" + "|".join("---" for _ in headers) + "|")
    for row in rows:
        lines.append("| " + " | ".join(row) + " |")
    return lines


def build_markdown(report: dict[str, Any]) -> str:
    datasets = report["datasets"]
    lines: list[str] = [
        "# SFT Token / Tool / Set Analysis",
        "",
        "## Scope",
        "",
        "- Tokens use `tiktoken cl100k_base` when available.",
        "- Token counts include `messages[].content` and structured assistant `tool_calls` function `name/arguments`.",
        "- ChatML framing overhead is not included.",
        "- Tool calls count structured `assistant.tool_calls`, not examples embedded in prompt text.",
        "- Tool-error coverage counts explicit tool wrapper failures such as `[error]`, missing paths, validation failures, edit-match failures, and command timeouts; ordinary failing test output is not counted as a tool error.",
        "- Task overlap uses `metadata.task_name` when available, falling back to trial/id-derived keys.",
        "",
        "## Overview",
        "",
    ]

    overview_rows = []
    for dataset in datasets:
        overview_rows.append(
            [
                dataset["label"],
                f"`{dataset['path']}`",
                format_number(dataset["sample_count"], 0),
                format_number(dataset["unique_task_count"], 0),
                format_number(dataset["duplicate_task_count"], 0),
            ]
        )
    lines.extend(
        markdown_table(
            ["Dataset", "File", "Samples", "Unique tasks", "Duplicate tasks"],
            overview_rows,
        )
    )

    if report["pairwise_overlap"]:
        lines.extend(["", "## Task Set Overlap", ""])
        rows = []
        for item in report["pairwise_overlap"]:
            rows.append(
                [
                    item["left"],
                    item["right"],
                    format_number(item["task_intersection"], 0),
                    format_number(item["left_only_tasks"], 0),
                    format_number(item["right_only_tasks"], 0),
                    format_number(item["task_union"], 0),
                    f"{format_number(item['task_overlap_left_pct'], 1)}%",
                    f"{format_number(item['task_overlap_right_pct'], 1)}%",
                    format_number(item["id_intersection"], 0),
                ]
            )
        lines.extend(
            markdown_table(
                [
                    "Left",
                    "Right",
                    "Task overlap",
                    "Left only",
                    "Right only",
                    "Union",
                    "Overlap / left",
                    "Overlap / right",
                    "Exact ID overlap",
                ],
                rows,
            )
        )

    lines.extend(["", "## Repository Distribution", ""])
    all_repositories: Counter[str] = Counter()
    for dataset in datasets:
        all_repositories.update(dataset["repository_counts"])
    repository_rows = []
    for repository, _count in all_repositories.most_common(25):
        row = [f"`{repository}`"]
        for dataset in datasets:
            count = dataset["repository_counts"].get(repository, 0)
            pct = count / dataset["sample_count"] * 100 if dataset["sample_count"] else 0
            row.extend([format_number(count, 0), f"{format_number(pct, 1)}%"])
        repository_rows.append(row)
    repository_headers = ["Repository"]
    for dataset in datasets:
        repository_headers.extend([f"{dataset['label']} count", f"{dataset['label']} share"])
    lines.extend(markdown_table(repository_headers, repository_rows))

    lines.extend(["", "## Token Counts", ""])
    rows = []
    for dataset in datasets:
        stats = dataset["token_stats"]
        rows.append(
            [
                dataset["label"],
                format_number(stats["mean"], 1),
                format_number(stats["min"], 0),
                format_number(stats["max"], 0),
                format_number(stats["median"], 1),
                format_number(stats["p90"], 1),
            ]
        )
    lines.extend(markdown_table(["Dataset", "Avg tokens", "Min", "Max", "P50", "P90"], rows))

    lines.extend(["", "## Tool Call Counts", ""])
    rows = []
    for dataset in datasets:
        stats = dataset["tool_call_stats"]
        rows.append(
            [
                dataset["label"],
                format_number(stats["mean"], 2),
                format_number(stats["min"], 0),
                format_number(stats["max"], 0),
                format_number(stats["median"], 1),
                format_number(stats["p90"], 1),
            ]
        )
    lines.extend(markdown_table(["Dataset", "Avg tool calls", "Min", "Max", "P50", "P90"], rows))

    lines.extend(["", "## Tool Type Distribution", ""])
    all_tools: Counter[str] = Counter()
    for dataset in datasets:
        all_tools.update(dataset["tool_names"])
    tool_rows = []
    for tool, _count in all_tools.most_common():
        row = [f"`{tool}`"]
        for dataset in datasets:
            count = dataset["tool_names"].get(tool, 0)
            avg = count / dataset["sample_count"] if dataset["sample_count"] else 0
            row.extend([format_number(count, 0), format_number(avg, 2)])
        tool_rows.append(row)
    tool_headers = ["Tool"]
    for dataset in datasets:
        tool_headers.extend([f"{dataset['label']} count", f"{dataset['label']} avg/sample"])
    lines.extend(markdown_table(tool_headers, tool_rows))

    lines.extend(["", "## Behavior Stats", ""])
    behavior_rows = []
    behavior_specs = [
        ("Avg messages", "message_stats", "mean", 2),
        ("Max messages", "message_stats", "max", 0),
        ("Avg tool results", "tool_result_stats", "mean", 2),
        ("Avg tool result tokens", "tool_result_token_stats", "mean", 1),
        ("Avg `bash` calls", "bash_call_stats", "mean", 2),
        ("Avg test command calls", "test_command_call_stats", "mean", 2),
        ("Avg `edit` calls", "edit_call_stats", "mean", 2),
        ("Avg `write` calls", "write_call_stats", "mean", 2),
        ("Avg tool error results", "tool_error_result_stats", "mean", 2),
    ]
    for label, stat_name, key, digits in behavior_specs:
        row = [label]
        for dataset in datasets:
            row.append(format_number(stat_value(dataset, stat_name, key), digits))
        behavior_rows.append(row)
    lines.extend(markdown_table(["Metric"] + [dataset["label"] for dataset in datasets], behavior_rows))

    lines.extend(["", "## Coverage", ""])
    coverage_specs = [
        ("Samples with tool calls", "with_tool_calls"),
        ("Samples with `edit` or `write`", "with_edit_or_write"),
        ("Samples with test command", "with_test_command"),
        ("Samples with tool error", "with_tool_error"),
        ("Samples with missing tool results", "with_missing_tool_results"),
    ]
    coverage_rows = []
    for label, key in coverage_specs:
        row = [label]
        for dataset in datasets:
            value = dataset["coverage"][key]
            total = dataset["sample_count"]
            pct = value / total * 100 if total else 0
            row.append(f"{format_number(value, 0)} / {format_number(total, 0)} ({format_number(pct, 1)}%)")
        coverage_rows.append(row)
    lines.extend(markdown_table(["Metric"] + [dataset["label"] for dataset in datasets], coverage_rows))

    lines.extend(["", "## Extreme Samples", ""])
    extreme_rows = []
    for dataset in datasets:
        for kind, label in (
            ("token_min", "token min"),
            ("token_max", "token max"),
            ("tool_calls_max", "tool calls max"),
        ):
            sample = dataset["extremes"].get(kind)
            if not sample:
                continue
            extreme_rows.append(
                [
                    dataset["label"],
                    label,
                    f"`{sample['task_key']}`",
                    f"`{sample['id']}`",
                    format_number(sample["tokens"], 0),
                    format_number(sample["tool_calls"], 0),
                    format_number(sample["messages"], 0),
                ]
            )
    lines.extend(
        markdown_table(
            ["Dataset", "Type", "Task", "ID", "Tokens", "Tool calls", "Messages"],
            extreme_rows,
        )
    )

    if len(datasets) >= 2:
        left, right = datasets[0], datasets[1]
        token_ratio = ratio(right["token_stats"]["mean"], left["token_stats"]["mean"])
        tool_ratio = ratio(right["tool_call_stats"]["mean"], left["tool_call_stats"]["mean"])
        message_ratio = ratio(right["message_stats"]["mean"], left["message_stats"]["mean"])
        lines.extend(["", "## Comparison Notes", ""])
        lines.append(
            f"- `{right['label']}` has {format_ratio(token_ratio)} the average tokens of `{left['label']}`."
        )
        lines.append(
            f"- `{right['label']}` has {format_ratio(tool_ratio)} the average tool calls of `{left['label']}`."
        )
        lines.append(
            f"- `{right['label']}` has {format_ratio(message_ratio)} the average messages of `{left['label']}`."
        )
        if report["pairwise_overlap"]:
            overlap = report["pairwise_overlap"][0]
            lines.append(
                f"- Task overlap is {format_number(overlap['task_intersection'], 0)} tasks; "
                f"{format_number(overlap['right_only_tasks'], 0)} tasks appear only in `{right['label']}`."
            )
            if overlap["task_intersection_examples"]:
                examples = ", ".join(f"`{item}`" for item in overlap["task_intersection_examples"][:10])
                lines.append(f"- Overlap examples: {examples}.")
            if overlap["right_only_examples"]:
                examples = ", ".join(f"`{item}`" for item in overlap["right_only_examples"][:10])
                lines.append(f"- `{right['label']}` only examples: {examples}.")

    lines.append("")
    return "\n".join(lines)


def parse_dataset(value: str) -> dict[str, str]:
    if "=" not in value:
        raise argparse.ArgumentTypeError("--dataset must be LABEL=PATH")
    label, path = value.split("=", 1)
    label = label.strip()
    path = path.strip()
    if not label or not path:
        raise argparse.ArgumentTypeError("--dataset must be LABEL=PATH")
    return {"label": label, "path": path}


def parse_metadata(value: str) -> tuple[str, str]:
    if "=" not in value:
        raise argparse.ArgumentTypeError("--metadata must be LABEL=PATH")
    label, path = value.split("=", 1)
    label = label.strip()
    path = path.strip()
    if not label or not path:
        raise argparse.ArgumentTypeError("--metadata must be LABEL=PATH")
    return label, path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", action="append", type=parse_dataset, required=True)
    parser.add_argument(
        "--metadata",
        action="append",
        type=parse_metadata,
        default=[],
        help="Optional LABEL=PATH sidecar metadata override.",
    )
    parser.add_argument("--out-md", required=True)
    parser.add_argument("--out-json")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    metadata_overrides = dict(args.metadata)
    encoder = get_encoder()

    dataset_specs = []
    for spec in args.dataset:
        spec = dict(spec)
        if spec["label"] in metadata_overrides:
            spec["metadata"] = metadata_overrides[spec["label"]]
        dataset_specs.append(spec)

    datasets = [load_dataset(spec, encoder) for spec in dataset_specs]
    report = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "tokenizer": "cl100k_base" if encoder is not None else "regex-fallback",
        "datasets": datasets,
        "pairwise_overlap": pairwise_overlap(datasets),
    }

    markdown = build_markdown(report)
    out_md = repo_path(args.out_md)
    out_md.parent.mkdir(parents=True, exist_ok=True)
    out_md.write_text(markdown, encoding="utf-8")

    if args.out_json:
        out_json = repo_path(args.out_json)
        out_json.parent.mkdir(parents=True, exist_ok=True)
        out_json.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    print(f"wrote {display_path(out_md)}")
    if args.out_json:
        print(f"wrote {display_path(repo_path(args.out_json))}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
