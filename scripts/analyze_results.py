#!/usr/bin/env python
import argparse
import json
import re
import statistics
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any

try:
    import tiktoken
except ImportError:  # pragma: no cover - depends on the local analysis env
    tiktoken = None


ROOT = Path(__file__).resolve().parents[1]
_TOKEN_ENCODER = None


def _load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text())


def _token_count(value: Any) -> int:
    text = str(value or "")
    if not text:
        return 0
    if tiktoken is not None:
        global _TOKEN_ENCODER
        if _TOKEN_ENCODER is None:
            try:
                _TOKEN_ENCODER = tiktoken.encoding_for_model("gpt-4o")
            except KeyError:
                _TOKEN_ENCODER = tiktoken.get_encoding("cl100k_base")
        return len(_TOKEN_ENCODER.encode(text))
    return len(re.findall(r"\w+|[^\w\s]", text, flags=re.UNICODE))


def _latest_job_dir() -> Path:
    jobs_dir = ROOT / "jobs"
    result_paths = list(jobs_dir.glob("*/result.json"))
    if not result_paths:
        raise SystemExit(f"No Harbor result.json files found under {jobs_dir}")
    return max(result_paths, key=lambda path: path.stat().st_mtime).parent


def _duration_seconds(start: str | None, end: str | None) -> float | None:
    if not start or not end:
        return None
    try:
        return (datetime.fromisoformat(end) - datetime.fromisoformat(start)).total_seconds()
    except ValueError:
        return None


def _first_reward_value(rewards: dict[str, Any] | None) -> float | int | None:
    if not rewards:
        return None
    for key in ("resolved", "pass", "success", "reward"):
        if key in rewards:
            return rewards[key]
    return next(iter(rewards.values()))


def _sharegpt_stats(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {
            "exists": False,
            "messages": 0,
            "chars": 0,
            "tokens": 0,
            "tool_rounds": 0,
        }
    data = _load_json(path)
    conversations = data.get("conversations") or []
    metadata = data.get("metadata") or {}
    trace_text = "\n".join(str(item.get("value", "")) for item in conversations)
    return {
        "exists": True,
        "messages": len(conversations),
        "chars": len(trace_text),
        "tokens": _token_count(trace_text),
        "tool_rounds": metadata.get("tool_call_rounds")
        or sum(
            1
            for item in conversations
            if item.get("from") == "gpt"
            and "<tool_call" in str(item.get("value", ""))
        ),
    }


def _trajectory_stats(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {
            "exists": False,
            "steps": 0,
            "agent_steps": 0,
            "tool_steps": 0,
            "tool_calls": [],
        }
    data = _load_json(path)
    steps = data.get("steps") or []
    tool_calls: list[dict[str, Any]] = []
    for step in steps:
        step_tool_calls = step.get("tool_calls") or []
        observation_results = (step.get("observation") or {}).get("results") or []
        observations_by_id = {
            result.get("source_call_id"): result
            for result in observation_results
            if result.get("source_call_id")
        }
        for call in step_tool_calls:
            if not isinstance(call, dict):
                continue
            arguments = call.get("arguments")
            observation = observations_by_id.get(call.get("tool_call_id")) or {}
            observation_content = observation.get("content")
            argument_text = (
                json.dumps(arguments, ensure_ascii=False)
                if arguments is not None
                else ""
            )
            observation_text = str(observation_content or "")
            is_error = bool(observation.get("is_error")) or (
                "[error]" in observation_text
                or "Validation failed for tool" in observation_text
            )
            tool_calls.append(
                {
                    "name": call.get("function_name") or "unknown",
                    "argument_chars": len(argument_text),
                    "argument_tokens": _token_count(argument_text),
                    "observation_chars": len(observation_text),
                    "observation_tokens": _token_count(observation_text),
                    "is_error": is_error,
                    "is_validation_error": "Validation failed for tool"
                    in observation_text,
                }
            )
    return {
        "exists": True,
        "steps": len(steps),
        "agent_steps": sum(1 for step in steps if step.get("source") == "agent"),
        "tool_steps": sum(1 for step in steps if step.get("tool_calls")),
        "tool_calls": tool_calls,
        "final_metrics": data.get("final_metrics") or {},
        "agent": data.get("agent") or {},
    }


def analyze(job_dir: Path) -> dict[str, Any]:
    result_path = job_dir / "result.json"
    if not result_path.exists():
        raise SystemExit(f"Missing {result_path}")

    job_result = _load_json(result_path)
    trial_paths = sorted(job_dir.glob("*/result.json"))
    trials: list[dict[str, Any]] = []
    rewards = []
    errors = Counter()
    reward_distribution: dict[str, Counter] = defaultdict(Counter)
    durations: list[float] = []
    trace_messages: list[int] = []
    trace_chars: list[int] = []
    trace_tokens: list[int] = []
    trajectory_steps: list[int] = []
    tool_rounds: list[int] = []
    tool_calls_per_trial: list[int] = []
    tool_argument_chars: list[int] = []
    tool_argument_tokens: list[int] = []
    tool_observation_chars: list[int] = []
    tool_observation_tokens: list[int] = []
    tool_error_count = 0
    tool_validation_error_count = 0
    tool_name_counts = Counter()
    tool_name_argument_chars: dict[str, list[int]] = defaultdict(list)
    tool_name_argument_tokens: dict[str, list[int]] = defaultdict(list)
    tool_name_observation_chars: dict[str, list[int]] = defaultdict(list)
    tool_name_observation_tokens: dict[str, list[int]] = defaultdict(list)
    input_tokens: list[int] = []
    output_tokens: list[int] = []
    cache_tokens: list[int] = []
    pi_system_prompts = Counter()

    for trial_result_path in trial_paths:
        if trial_result_path == result_path:
            continue
        trial_dir = trial_result_path.parent
        result = _load_json(trial_result_path)
        agent_dir = trial_dir / "agent"
        sharegpt_path = agent_dir / "sharegpt.json"
        trajectory_path = agent_dir / "trajectory.json"
        metadata_path = agent_dir / "pi-metadata.json"

        sharegpt = _sharegpt_stats(sharegpt_path)
        trajectory = _trajectory_stats(trajectory_path)
        metadata = _load_json(metadata_path) if metadata_path.exists() else {}

        verifier_rewards = (
            result.get("verifier_result", {}).get("rewards")
            if result.get("verifier_result")
            else None
        )
        reward_value = _first_reward_value(verifier_rewards)
        if reward_value is not None:
            rewards.append(float(reward_value))
        if verifier_rewards:
            for key, value in verifier_rewards.items():
                reward_distribution[key][value] += 1

        exception = result.get("exception_info")
        if exception:
            errors[exception.get("exception_type", "unknown")] += 1

        duration = _duration_seconds(result.get("started_at"), result.get("finished_at"))
        if duration is not None:
            durations.append(duration)

        trace_messages.append(sharegpt["messages"])
        trace_chars.append(sharegpt["chars"])
        trace_tokens.append(sharegpt["tokens"])
        trajectory_steps.append(trajectory["steps"])
        tool_rounds.append(int(sharegpt["tool_rounds"] or trajectory["tool_steps"] or 0))
        trajectory_tool_calls = trajectory.get("tool_calls") or []
        tool_calls_per_trial.append(len(trajectory_tool_calls))
        for tool_call in trajectory_tool_calls:
            tool_name = str(tool_call.get("name") or "unknown")
            arg_chars = int(tool_call.get("argument_chars") or 0)
            arg_tokens = int(tool_call.get("argument_tokens") or 0)
            obs_chars = int(tool_call.get("observation_chars") or 0)
            obs_tokens = int(tool_call.get("observation_tokens") or 0)
            tool_name_counts[tool_name] += 1
            tool_argument_chars.append(arg_chars)
            tool_argument_tokens.append(arg_tokens)
            tool_observation_chars.append(obs_chars)
            tool_observation_tokens.append(obs_tokens)
            tool_name_argument_chars[tool_name].append(arg_chars)
            tool_name_argument_tokens[tool_name].append(arg_tokens)
            tool_name_observation_chars[tool_name].append(obs_chars)
            tool_name_observation_tokens[tool_name].append(obs_tokens)
            if tool_call.get("is_error"):
                tool_error_count += 1
            if tool_call.get("is_validation_error"):
                tool_validation_error_count += 1

        agent_result = result.get("agent_result") or {}
        if agent_result.get("n_input_tokens") is not None:
            input_tokens.append(int(agent_result["n_input_tokens"]))
        if agent_result.get("n_output_tokens") is not None:
            output_tokens.append(int(agent_result["n_output_tokens"]))
        if agent_result.get("n_cache_tokens") is not None:
            cache_tokens.append(int(agent_result["n_cache_tokens"]))

        system_prompt = metadata.get("system_prompt")
        if system_prompt:
            pi_system_prompts[system_prompt] += 1

        trials.append(
            {
                "trial_name": result.get("trial_name"),
                "task_name": result.get("task_name"),
                "reward": verifier_rewards,
                "exception": exception.get("exception_type") if exception else None,
                "duration_sec": duration,
                "sharegpt_path": str(sharegpt_path),
                "trajectory_path": str(trajectory_path),
                "sharegpt_messages": sharegpt["messages"],
                "sharegpt_chars": sharegpt["chars"],
                "sharegpt_tokens": sharegpt["tokens"],
                "trajectory_steps": trajectory["steps"],
                "tool_rounds": tool_rounds[-1],
                "tool_calls": tool_calls_per_trial[-1],
                "input_tokens": agent_result.get("n_input_tokens"),
                "output_tokens": agent_result.get("n_output_tokens"),
                "cache_tokens": agent_result.get("n_cache_tokens"),
            }
        )

    n_trials = len(trials)
    n_errors = sum(errors.values())
    resolved = sum(1 for value in rewards if value == 1.0)

    def summary(values: list[int] | list[float]) -> dict[str, float | int | None]:
        if not values:
            return {"count": 0, "mean": None, "median": None, "min": None, "max": None}
        return {
            "count": len(values),
            "mean": statistics.fmean(values),
            "median": statistics.median(values),
            "min": min(values),
            "max": max(values),
        }

    return {
        "job_dir": str(job_dir),
        "harbor_job_result": job_result,
        "performance": {
            "n_trials": n_trials,
            "n_errors": n_errors,
            "completed_without_exception": n_trials - n_errors,
            "resolved_count_assuming_reward_1": resolved,
            "resolved_rate_assuming_reward_1": (resolved / n_trials) if n_trials else 0,
            "mean_reward": statistics.fmean(rewards) if rewards else None,
            "reward_distribution": {
                key: {str(k): v for k, v in counter.items()}
                for key, counter in reward_distribution.items()
            },
            "exception_distribution": dict(errors),
        },
        "trace_lengths": {
            "sharegpt_messages": summary(trace_messages),
            "sharegpt_chars": summary(trace_chars),
            "sharegpt_tokens": summary(trace_tokens),
            "atif_steps": summary(trajectory_steps),
        },
        "pi": {
            "system_prompt": pi_system_prompts.most_common(1)[0][0]
            if pi_system_prompts
            else None,
            "system_prompt_variants": len(pi_system_prompts),
            "tool_call_rounds": summary(tool_rounds),
            "tool_calls_per_trial": summary(tool_calls_per_trial),
            "tool_calls_total": sum(tool_calls_per_trial),
            "tool_error_count": tool_error_count,
            "tool_validation_error_count": tool_validation_error_count,
            "tool_name_counts": dict(tool_name_counts.most_common()),
            "tool_argument_chars": summary(tool_argument_chars),
            "tool_argument_tokens": summary(tool_argument_tokens),
            "tool_observation_chars": summary(tool_observation_chars),
            "tool_observation_tokens": summary(tool_observation_tokens),
            "tool_name_stats": {
                name: {
                    "count": count,
                    "argument_chars": summary(tool_name_argument_chars[name]),
                    "argument_tokens": summary(tool_name_argument_tokens[name]),
                    "observation_chars": summary(tool_name_observation_chars[name]),
                    "observation_tokens": summary(tool_name_observation_tokens[name]),
                }
                for name, count in tool_name_counts.most_common()
            },
        },
        "tokens": {
            "input": summary(input_tokens),
            "output": summary(output_tokens),
            "cache": summary(cache_tokens),
        },
        "timing": {
            "trial_duration_sec": summary(durations),
        },
        "trials": trials,
    }


def write_markdown(report: dict[str, Any], path: Path) -> None:
    perf = report["performance"]
    pi = report["pi"]
    trace = report["trace_lengths"]
    tokens = report["tokens"]
    timing = report["timing"]

    def stat_text(stats: dict[str, Any]) -> str:
        return (
            f"{stats['min']} / {stats['max']} / "
            f"{stats['mean']} / {stats['median']}"
        )

    lines = [
        "# SWE-Bench Verified Analysis",
        "",
        f"- Job dir: `{report['job_dir']}`",
        f"- Trials: {perf['n_trials']}",
        f"- Errors: {perf['n_errors']}",
        f"- Resolved count (reward == 1): {perf['resolved_count_assuming_reward_1']}",
        f"- Resolved rate: {perf['resolved_rate_assuming_reward_1']:.4f}",
        f"- Mean reward: {perf['mean_reward']}",
        "",
        "## Trace Lengths",
        "",
        f"- ShareGPT messages min/max/mean/median: {stat_text(trace['sharegpt_messages'])}",
        f"- ShareGPT chars min/max/mean/median: {stat_text(trace['sharegpt_chars'])}",
        f"- ShareGPT tokens min/max/mean/median: {stat_text(trace['sharegpt_tokens'])}",
        f"- ATIF steps min/max/mean/median: {stat_text(trace['atif_steps'])}",
        "",
        "## Pi",
        "",
        f"- System prompt variants: {pi['system_prompt_variants']}",
        f"- Tool call rounds min/max/mean/median: {stat_text(pi['tool_call_rounds'])}",
        f"- Tool calls total: {pi['tool_calls_total']}",
        f"- Tool calls per trial min/max/mean/median: {stat_text(pi['tool_calls_per_trial'])}",
        f"- Tool argument chars min/max/mean/median: {stat_text(pi['tool_argument_chars'])}",
        f"- Tool argument tokens min/max/mean/median: {stat_text(pi['tool_argument_tokens'])}",
        f"- Tool observation chars min/max/mean/median: {stat_text(pi['tool_observation_chars'])}",
        f"- Tool observation tokens min/max/mean/median: {stat_text(pi['tool_observation_tokens'])}",
        f"- Tool error count: {pi['tool_error_count']}",
        f"- Tool validation error count: {pi.get('tool_validation_error_count', 0)}",
        "",
        "### Tool Counts",
        "",
        "| Tool | Count | Arg tokens min/max/mean/median | Observation tokens min/max/mean/median |",
        "| --- | ---: | ---: | ---: |",
        *[
            "| {name} | {count} | {arg_tokens} | {obs_tokens} |".format(
                name=name,
                count=stats["count"],
                arg_tokens=stat_text(stats["argument_tokens"]),
                obs_tokens=stat_text(stats["observation_tokens"]),
            )
            for name, stats in pi["tool_name_stats"].items()
        ],
        "",
        "### Pi System Prompt",
        "",
        "```text",
        pi["system_prompt"] or "",
        "```",
        "",
        "## Tokens",
        "",
        f"- Input tokens min/max/mean/median: {stat_text(tokens['input'])}",
        f"- Output tokens min/max/mean/median: {stat_text(tokens['output'])}",
        f"- Cache tokens min/max/mean/median: {stat_text(tokens['cache'])}",
        "",
        "## Timing",
        "",
        f"- Trial seconds min/max/mean/median: {stat_text(timing['trial_duration_sec'])}",
        "",
        "## Exceptions",
        "",
        "```json",
        json.dumps(perf["exception_distribution"], indent=2),
        "```",
    ]
    path.write_text("\n".join(lines))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Analyze a Harbor SWE-Bench job.")
    parser.add_argument("--job-dir", type=Path, default=None)
    parser.add_argument("--out-dir", type=Path, default=ROOT / "analysis")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    job_dir = args.job_dir or _latest_job_dir()
    report = analyze(job_dir)
    args.out_dir.mkdir(parents=True, exist_ok=True)
    stem = job_dir.name
    json_path = args.out_dir / f"{stem}_analysis.json"
    md_path = args.out_dir / f"{stem}_analysis.md"
    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2))
    write_markdown(report, md_path)
    print(f"Wrote {json_path}")
    print(f"Wrote {md_path}")
    print(
        "performance: "
        f"trials={report['performance']['n_trials']} "
        f"errors={report['performance']['n_errors']} "
        f"resolved_rate={report['performance']['resolved_rate_assuming_reward_1']:.4f}"
    )


if __name__ == "__main__":
    main()
