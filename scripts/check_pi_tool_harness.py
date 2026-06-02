#!/usr/bin/env python
import argparse
import json
import re
import sys
from collections import Counter
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]

REQUIRED_ARGUMENTS = {
    "bash": ("command",),
    "read": ("path",),
    "write": ("path", "content"),
    "edit": ("path", "edits"),
    "grep": ("pattern",),
    "find": ("pattern",),
}

TOOL_BLOCK_RE = re.compile(
    r"<tool_call\b(?P<attrs>[^>]*)>(?P<body>[\s\S]*?)(?:</tool_call>|$)",
    re.IGNORECASE,
)
NAME_RE = re.compile(r"\bname\s*=\s*[\"']?(?P<name>[^\"'\s>]+)", re.IGNORECASE)
ID_RE = re.compile(r"\bid\s*=", re.IGNORECASE)


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(errors="replace"))


def latest_job_dir() -> Path:
    result_paths = list((ROOT / "jobs").glob("*/result.json"))
    if not result_paths:
        raise SystemExit(f"No job result.json files found under {ROOT / 'jobs'}")
    return max(result_paths, key=lambda path: path.stat().st_mtime).parent


def conversations(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    data = load_json(path)
    if isinstance(data, dict):
        value = data.get("conversations") or []
        return value if isinstance(value, list) else []
    return data if isinstance(data, list) else []


def parse_json_body(body: str) -> dict[str, Any] | None:
    text = body.strip()
    if not text.startswith("{"):
        return None
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None


def jsonl_events(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    events: list[dict[str, Any]] = []
    for line in path.read_text(errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(event, dict):
            events.append(event)
    return events


def tool_calls_from_pi_events(path: Path) -> list[dict[str, Any]]:
    calls: list[dict[str, Any]] = []
    for event in jsonl_events(path):
        if event.get("type") != "tool_execution_start":
            continue
        args = event.get("args")
        calls.append(
            {
                "name": str(event.get("toolName") or ""),
                "arguments": args if isinstance(args, dict) else None,
                "raw_body": json.dumps(args, ensure_ascii=False),
                "raw": json.dumps(event, ensure_ascii=False),
                "closed": True,
                "source": "pi-events",
            }
        )
    return calls


def provider_errors_from_pi_events(path: Path) -> list[str]:
    errors: list[str] = []
    seen: set[str] = set()
    for event in jsonl_events(path):
        message = event.get("message")
        if not isinstance(message, dict):
            continue
        if message.get("role") != "assistant":
            continue
        error = message.get("errorMessage") or message.get("error")
        if message.get("stopReason") == "error" or error:
            if isinstance(error, dict):
                error = json.dumps(error, ensure_ascii=False)
            detail = str(error or "unknown provider error")
            if detail not in seen:
                seen.add(detail)
                errors.append(detail)
    return errors


def tool_calls_from_sharegpt(path: Path) -> list[dict[str, Any]]:
    calls: list[dict[str, Any]] = []
    for message in conversations(path):
        if message.get("from") != "gpt":
            continue
        text = str(message.get("value") or "")
        for match in TOOL_BLOCK_RE.finditer(text):
            attrs = match.group("attrs") or ""
            if not ID_RE.search(attrs):
                continue
            name_match = NAME_RE.search(attrs)
            name = name_match.group("name") if name_match else ""
            body = match.group("body") or ""
            calls.append(
                {
                    "name": name,
                    "arguments": parse_json_body(body),
                    "raw_body": body,
                    "raw": match.group(0),
                    "closed": match.group(0).lower().rstrip().endswith("</tool_call>"),
                    "source": "sharegpt",
                }
            )
    return calls


def text_tool_intents_from_sharegpt(path: Path) -> list[dict[str, Any]]:
    intents: list[dict[str, Any]] = []
    for message in conversations(path):
        if message.get("from") != "gpt":
            continue
        text = str(message.get("value") or "")
        if "<tool_call" in text or "<function=" in text or "Tool call:" in text:
            matches = list(TOOL_BLOCK_RE.finditer(text))
            if not matches:
                intents.append(
                    {
                        "detail": "embedded_tool_intent_without_parseable_tool_block",
                        "raw": text,
                        "partial": True,
                    }
                )
                continue
            for match in matches:
                attrs = match.group("attrs") or ""
                if ID_RE.search(attrs):
                    continue
                raw = match.group(0)
                intents.append(
                    {
                        "detail": "assistant_text_serialized_tool_call",
                        "raw": raw,
                        "partial": not raw.lower().rstrip().endswith("</tool_call>"),
                    }
                )
    return intents


def validation_errors_from_trajectory(path: Path) -> list[str]:
    if not path.exists():
        return []
    data = load_json(path)
    errors: list[str] = []
    for step in data.get("steps") or []:
        observation_results = (step.get("observation") or {}).get("results") or []
        for result in observation_results:
            content = str(result.get("content") or "")
            errors.extend(
                re.findall(r"Validation failed for tool \\?\"([^\"\\]+)", content)
            )
    return errors


def check_job(job_dir: Path) -> dict[str, Any]:
    counters = Counter()
    samples: dict[str, list[dict[str, str]]] = {
        "missing_required_argument": [],
        "empty_required_arguments": [],
        "partial_tool_call": [],
        "validation_error": [],
        "provider_error": [],
        "text_only_tool_intent": [],
        "only_ls": [],
        "no_edit_or_write": [],
    }

    trial_dirs = sorted(path.parent for path in job_dir.glob("*/result.json"))
    for trial_dir in trial_dirs:
        sharegpt_path = trial_dir / "agent" / "sharegpt.json"
        jsonl_path = trial_dir / "agent" / "pi-events.jsonl"
        trajectory_path = trial_dir / "agent" / "trajectory.json"
        native_calls = tool_calls_from_pi_events(jsonl_path)
        serialized_calls = tool_calls_from_sharegpt(sharegpt_path)
        provider_errors = provider_errors_from_pi_events(jsonl_path)
        calls = native_calls or serialized_calls
        counters["trials"] += 1
        counters["native_tool_calls"] += len(native_calls)
        counters["serialized_tool_calls"] += len(serialized_calls)
        counters["provider_errors"] += len(provider_errors)
        counters["tool_calls"] += len(calls)
        tool_names = [str(call["name"] or "") for call in calls]
        text_tool_intents = text_tool_intents_from_sharegpt(sharegpt_path)

        for call in calls:
            name = str(call["name"] or "")
            args = call["arguments"]
            if not call["closed"]:
                counters["partial_tool_call"] += 1
                add_sample(samples["partial_tool_call"], trial_dir, name, call["raw"])
            if args == {} and name != "ls":
                counters["empty_required_arguments"] += 1
                add_sample(samples["empty_required_arguments"], trial_dir, name, call["raw"])
            required = REQUIRED_ARGUMENTS.get(name, ())
            if required:
                missing = [key for key in required if not isinstance(args, dict) or key not in args]
                if missing:
                    counters["missing_required_argument"] += 1
                    add_sample(
                        samples["missing_required_argument"],
                        trial_dir,
                        name,
                        f"missing={','.join(missing)} raw={call['raw']}",
                    )

        validation_errors = validation_errors_from_trajectory(trajectory_path)
        counters["validation_errors"] += len(validation_errors)
        for tool_name in validation_errors:
            add_sample(samples["validation_error"], trial_dir, tool_name, "Validation failed")
        for error in provider_errors:
            add_sample(samples["provider_error"], trial_dir, "<provider>", error)
        counters["text_only_tool_intent"] += len(text_tool_intents)
        for intent in text_tool_intents:
            if intent.get("partial"):
                counters["partial_tool_call"] += 1
                add_sample(
                    samples["partial_tool_call"],
                    trial_dir,
                    "<text>",
                    str(intent.get("raw") or intent.get("detail") or ""),
                )
            add_sample(
                samples["text_only_tool_intent"],
                trial_dir,
                "<intent>",
                str(intent.get("detail") or ""),
            )

        tool_name_set = set(tool_names)
        if tool_name_set and tool_name_set <= {"ls"}:
            counters["only_ls"] += 1
            add_sample(samples["only_ls"], trial_dir, "ls", "only ls tool calls parsed")
        elif tool_name_set and not any(name in {"edit", "write"} for name in tool_name_set):
            counters["no_edit_or_write"] += 1
            add_sample(
                samples["no_edit_or_write"],
                trial_dir,
                ",".join(sorted(name for name in tool_name_set if name)) or "<missing>",
                "no edit/write tool calls parsed",
            )

    tool_calls = counters["tool_calls"]
    invalid_calls = counters["missing_required_argument"] + counters["partial_tool_call"]
    return {
        "job_dir": str(job_dir),
        "trials": counters["trials"],
        "tool_calls": tool_calls,
        "native_tool_calls": counters["native_tool_calls"],
        "serialized_tool_calls": counters["serialized_tool_calls"],
        "provider_errors": counters["provider_errors"],
        "missing_required_argument": counters["missing_required_argument"],
        "empty_required_arguments": counters["empty_required_arguments"],
        "partial_tool_call": counters["partial_tool_call"],
        "validation_errors": counters["validation_errors"],
        "text_only_tool_intent": counters["text_only_tool_intent"],
        "only_ls_trials": counters["only_ls"],
        "no_edit_or_write_trials": counters["no_edit_or_write"],
        "invalid_tool_call_rate": (invalid_calls / tool_calls) if tool_calls else 0.0,
        "samples": samples,
    }


def add_sample(samples: list[dict[str, str]], trial_dir: Path, tool_name: str, detail: str) -> None:
    if len(samples) >= 10:
        return
    samples.append(
        {
            "trial": trial_dir.name,
            "tool": tool_name or "<missing>",
            "detail": " ".join(detail.split())[:500],
        }
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Check Pi native tool-use traces for missing arguments, text-serialized tool calls, and validation errors."
    )
    parser.add_argument("--job-dir", type=Path, default=None)
    parser.add_argument("--out", type=Path, default=None)
    parser.add_argument("--max-validation-errors", type=int, default=0)
    parser.add_argument("--max-provider-errors", type=int, default=0)
    parser.add_argument("--max-missing-required", type=int, default=0)
    parser.add_argument("--max-partial-tool-calls", type=int, default=0)
    parser.add_argument(
        "--min-tool-calls",
        type=int,
        default=0,
        help="Require at least this many parsed tool calls across the job.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    job_dir = args.job_dir or latest_job_dir()
    report = check_job(job_dir)
    text = json.dumps(report, ensure_ascii=False, indent=2)
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(text)
    print(text)

    failed = False
    if report["validation_errors"] > args.max_validation_errors:
        failed = True
    if report["provider_errors"] > args.max_provider_errors:
        failed = True
    if report["missing_required_argument"] > args.max_missing_required:
        failed = True
    if report["partial_tool_call"] > args.max_partial_tool_calls:
        failed = True
    if report["tool_calls"] < args.min_tool_calls:
        failed = True
    if failed:
        sys.exit(1)


if __name__ == "__main__":
    main()
