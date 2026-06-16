#!/usr/bin/env python
import argparse
import hashlib
import json
import os
import re
import statistics
import urllib.error
import urllib.request
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

try:
    from dotenv import load_dotenv
except ImportError:  # pragma: no cover - local env convenience only
    load_dotenv = None


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUT = ROOT / "analysis" / "skill_harness_memory.json"
DEFAULT_TASK_SKILL_DIR = ROOT / "analysis" / "task_skills"
DEFAULT_GENERATED_SKILL_DIR = ROOT / "skills" / "tasks"
SWE_AGENT_SKILLS_ROOT = ROOT / "swe_agent_skills"
SHARED_SKILLS_ROOT = ROOT / "skills" / "shared"
TASK_STAGE_SKILLS_ROOT = ROOT / "skills" / "tasks"
SWE_STAGES = ("reproduce", "localize", "edit", "validate", "recover")
SCRIPT_SUFFIXES = {".py", ".sh", ".bash"}
TEST_COMMAND_PATTERNS = (
    "pytest",
    "python -m",
    "tox",
    "npm test",
    "mvn test",
    "cargo test",
    "go test",
)
GENERIC_FALLBACK_SKILL_NAMES = {
    "task-recover-from-drift",
    "task-reproduce-from-issue",
    "task-validate-targeted",
    "task-edit-minimal-owner",
    "task-localize-high-signal-paths",
}
GENERIC_SKILL_PHRASES = (
    "inspect high-signal paths",
    "patch only the owner location",
    "run the nearest focused test",
    "stop the loop",
    "re-localize from the strongest evidence",
    "small diff preserving existing api",
    "behavior-owning module",
)
SOURCE_PATH_RE = re.compile(
    r"(?<![A-Za-z0-9_.-])(?:[A-Za-z0-9_.-]+/)+[A-Za-z0-9_.-]+"
    r"\.(?:py|pyi|js|ts|tsx|jsx|rst|txt|cfg|ini|toml|yml|yaml|json|md)"
)
PRIVATE_PATH_RE = re.compile(r"(/tmp/(?!pi-skills\b)|/root/|/home/|/opt/miniconda)")


def _load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(errors="replace"))


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _latest_job_dir() -> Path:
    result_paths = list((ROOT / "jobs").glob("*/result.json"))
    if not result_paths:
        raise SystemExit(f"No job result.json files found under {ROOT / 'jobs'}")
    return max(result_paths, key=lambda path: path.stat().st_mtime).parent


def _first_reward_value(rewards: dict[str, Any] | None) -> float | None:
    if not rewards:
        return None
    for key in ("reward", "resolved", "success", "pass"):
        if key in rewards:
            try:
                return float(rewards[key])
            except (TypeError, ValueError):
                return None
    try:
        return float(next(iter(rewards.values())))
    except (StopIteration, TypeError, ValueError):
        return None


def _entry_reward_value(entry: dict[str, Any]) -> float:
    try:
        return float(entry.get("reward"))
    except (TypeError, ValueError):
        return 0.0


def _repo_from_task_name(task_name: str | None) -> str:
    if not task_name:
        return ""
    leaf = task_name.split("/")[-1]
    return leaf.split("__", 1)[0]


def _issue_title(problem_text: str) -> str:
    lines = [line.strip() for line in problem_text.splitlines()]
    try:
        start = lines.index("BENCHMARK ISSUE:") + 1
    except ValueError:
        start = 0
    for line in lines[start:]:
        if line and not line.startswith("###") and not line.startswith("```"):
            return line[:240]
    return ""


def _normalize_path(path: str) -> str:
    path = path.strip()
    for prefix in ("/testbed/", "/workspace/"):
        if path.startswith(prefix):
            return path[len(prefix) :]
    return path


def _iter_tool_calls(trajectory: dict[str, Any]) -> list[dict[str, Any]]:
    calls: list[dict[str, Any]] = []
    for step in trajectory.get("steps") or []:
        for call in step.get("tool_calls") or []:
            if isinstance(call, dict):
                calls.append(call)
    return calls


def _selected_skill_from_path(path: str) -> dict[str, str] | None:
    if "/tmp/pi-skills/" not in path or not path.endswith("SKILL.md"):
        return None
    relative_path = path.split("/tmp/pi-skills/", 1)[1]
    parts = relative_path.split("/")
    name = parts[-2] if len(parts) >= 2 else relative_path
    return {
        "name": name,
        "path": path,
        "relative_path": relative_path,
    }


def _tool_signal(calls: list[dict[str, Any]]) -> dict[str, Any]:
    tool_counts = Counter()
    path_counts: Counter[str] = Counter()
    edited_paths: Counter[str] = Counter()
    selected_skills: list[dict[str, str]] = []
    selected_skill_keys: set[str] = set()
    test_commands: list[str] = []
    tool_sequence: list[dict[str, str]] = []

    for call in calls:
        name = str(call.get("function_name") or "unknown")
        tool_counts[name] += 1
        args = call.get("arguments") or {}
        if not isinstance(args, dict):
            continue
        if len(tool_sequence) < 24:
            tool_sequence.append(
                {
                    "tool": name,
                    "arguments": json.dumps(args, ensure_ascii=False)[:300],
                }
            )
        path = args.get("path") or args.get("file_path")
        if isinstance(path, str):
            skill = _selected_skill_from_path(path)
            if skill:
                key = skill["relative_path"]
                if key not in selected_skill_keys:
                    selected_skill_keys.add(key)
                    selected_skills.append(skill)
            elif "/tmp/pi-skills/" not in path:
                normalized = _normalize_path(path)
                path_counts[normalized] += 1
                if name in {"edit", "write"}:
                    edited_paths[normalized] += 1
        command = args.get("command")
        if isinstance(command, str) and any(pattern in command for pattern in TEST_COMMAND_PATTERNS):
            compact = " ".join(command.split())
            if compact not in test_commands:
                test_commands.append(compact[:260])

    return {
        "tool_counts": dict(tool_counts.most_common()),
        "selected_skill": selected_skills[0] if selected_skills else None,
        "selected_skills": selected_skills,
        "touched_paths": [path for path, _ in path_counts.most_common(12)],
        "edited_paths": [path for path, _ in edited_paths.most_common(12)],
        "test_commands": test_commands[:5],
        "tool_sequence": tool_sequence,
    }


def _keywords(*parts: Any) -> list[str]:
    text_parts: list[str] = []
    for part in parts:
        if isinstance(part, dict):
            text_parts.extend(str(value) for value in part.values())
        elif isinstance(part, list):
            text_parts.extend(str(value) for value in part)
        elif part is not None:
            text_parts.append(str(part))
    tokens = {
        token.lower()
        for token in re.findall(r"[A-Za-z_][A-Za-z0-9_]{2,}", "\n".join(text_parts))
    }
    stopwords = {
        "and",
        "are",
        "for",
        "from",
        "not",
        "the",
        "this",
        "that",
        "with",
        "using",
    }
    return sorted(token for token in tokens if token not in stopwords)[:80]


def _skill_frontmatter(text: str) -> dict[str, str]:
    metadata: dict[str, str] = {}
    lines = text.splitlines()
    in_frontmatter = lines[:1] == ["---"]
    iterable = lines[1:] if in_frontmatter else lines
    for line in iterable[:120]:
        line = line.strip()
        if in_frontmatter and line == "---":
            break
        if not line or line.startswith("#") or ":" not in line:
            continue
        key, value = line.split(":", 1)
        key = key.strip()
        if key in {"name", "description"}:
            metadata[key] = value.strip().strip("'\"")
    return metadata


def _relative_to_root(path: Path) -> str:
    try:
        return path.relative_to(ROOT).as_posix()
    except ValueError:
        return path.as_posix()


def _script_language(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix == ".py":
        return "python"
    if suffix in {".sh", ".bash"}:
        return "shell"
    return suffix.lstrip(".") or "text"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _selected_skill_local_path(skill: dict[str, Any]) -> Path | None:
    relative_path = skill.get("relative_path")
    if not isinstance(relative_path, str) or not relative_path:
        return None

    relative = Path(relative_path)
    candidates = [
        SWE_AGENT_SKILLS_ROOT / relative,
        SHARED_SKILLS_ROOT / relative,
    ]
    if TASK_STAGE_SKILLS_ROOT.exists():
        for version_root in sorted(TASK_STAGE_SKILLS_ROOT.glob("*")):
            if version_root.is_dir():
                candidates.append(version_root / relative)

    for candidate in candidates:
        if candidate.exists() and candidate.name == "SKILL.md":
            return candidate
    return None


def load_task_skill_resources(
    *,
    entry: dict[str, Any],
    max_chars_per_skill: int,
    max_total_chars: int,
) -> dict[str, Any]:
    """Load only resources from skills touched by this task trace.

    This deliberately avoids a global catalog. A task-stage memory card should
    be grounded in the task's own rollout plus the SKILL.md/script files the
    agent actually read.
    """

    selected_skills = entry.get("selected_skills") or []
    if not selected_skills and entry.get("selected_skill"):
        selected_skills = [entry["selected_skill"]]

    resources: list[dict[str, Any]] = []
    seen_paths: set[str] = set()
    total_chars = 0
    for selected_skill in selected_skills:
        if not isinstance(selected_skill, dict):
            continue
        skill_md = _selected_skill_local_path(selected_skill)
        if skill_md is None:
            resources.append(
                {
                    "name": selected_skill.get("name") or "unknown",
                    "path": selected_skill.get("path"),
                    "relative_path": selected_skill.get("relative_path"),
                    "missing_local_copy": True,
                    "scripts": [],
                }
            )
            continue

        key = _relative_to_root(skill_md)
        if key in seen_paths:
            continue
        seen_paths.add(key)

        text = skill_md.read_text(errors="replace")
        metadata = _skill_frontmatter(text)
        remaining = max(0, max_total_chars - total_chars)
        excerpt = text[: min(max_chars_per_skill, remaining)]
        total_chars += len(excerpt)

        scripts: list[dict[str, Any]] = []
        for script_path in sorted(skill_md.parent.rglob("*")):
            if not script_path.is_file() or script_path.suffix.lower() not in SCRIPT_SUFFIXES:
                continue
            remaining = max(0, max_total_chars - total_chars)
            if remaining <= 0:
                break
            script_text = script_path.read_text(errors="replace")
            script_excerpt = script_text[: min(max_chars_per_skill, remaining)]
            total_chars += len(script_excerpt)
            scripts.append(
                {
                    "source_path": _relative_to_root(script_path),
                    "relative_to_skill": script_path.relative_to(skill_md.parent).as_posix(),
                    "language": _script_language(script_path),
                    "sha256": _sha256(script_path),
                    "excerpt": script_excerpt,
                }
            )

        resources.append(
            {
                "name": metadata.get("name") or selected_skill.get("name") or skill_md.parent.name,
                "description": metadata.get("description", ""),
                "source_path": key,
                "relative_path": selected_skill.get("relative_path"),
                "skill_md_excerpt": excerpt,
                "scripts": scripts,
            }
        )
        if total_chars >= max_total_chars:
            break

    return {
        "mode": "trace_selected_resources",
        "resources": resources,
        "total_chars": total_chars,
    }


def _trial_entry(job_dir: Path, trial_result_path: Path) -> dict[str, Any] | None:
    trial_dir = trial_result_path.parent
    result = _load_json(trial_result_path)
    task_name = result.get("task_name") or result.get("trial_name")
    agent_dir = trial_dir / "agent"
    problem_path = agent_dir / "problem_statement.md"
    metadata_path = agent_dir / "pi-metadata.json"
    trajectory_path = agent_dir / "trajectory.json"
    problem_text = problem_path.read_text(errors="replace") if problem_path.exists() else ""
    metadata = _load_json(metadata_path) if metadata_path.exists() else {}
    trajectory = _load_json(trajectory_path) if trajectory_path.exists() else {}
    verifier_rewards = (
        result.get("verifier_result", {}).get("rewards")
        if result.get("verifier_result")
        else None
    )
    reward = _first_reward_value(verifier_rewards)
    exception = result.get("exception_info") or {}
    calls = _iter_tool_calls(trajectory)
    signal = _tool_signal(calls)
    repo = _repo_from_task_name(task_name)
    title = _issue_title(problem_text)
    selected_skill = signal["selected_skill"]
    selected_skills = signal["selected_skills"]
    skill_names = [skill["name"] for skill in selected_skills] or ["none"]
    status = "resolved" if reward == 1.0 else "unresolved"
    summary = (
        f"{status} trace for {repo or 'unknown repo'}; selected skills={', '.join(skill_names)}; "
        f"high-signal paths={', '.join(signal['touched_paths'][:4]) or 'none'}; "
        f"verification={'; '.join(signal['test_commands'][:2]) or 'none'}"
    )
    entry = {
        "kind": "swe_task_stage_skills",
        "entry_id": f"{job_dir.name}:{trial_dir.name}",
        "task_skill_id": trial_dir.name,
        "source_job": job_dir.name,
        "trial_dir": str(trial_dir.relative_to(ROOT)),
        "task_name": task_name,
        "repo": repo,
        "issue_title": title,
        "reward": reward,
        "exception": exception.get("exception_type") if exception else None,
        "provider": metadata.get("provider"),
        "provider_model": metadata.get("provider_model"),
        "provider_base_url": metadata.get("provider_base_url"),
        "provider_api": metadata.get("provider_api"),
        "api_key_env": metadata.get("api_key_env"),
        "use_skills": metadata.get("use_skills"),
        "selected_skill": selected_skill,
        "selected_skills": selected_skills,
        "tool_counts": signal["tool_counts"],
        "touched_paths": signal["touched_paths"],
        "edited_paths": signal["edited_paths"],
        "test_commands": signal["test_commands"],
        "tool_sequence": signal["tool_sequence"],
        "problem_excerpt": problem_text[:1800],
        "summary": summary,
        "summary_source": "heuristic",
        "keywords": _keywords(
            task_name,
            repo,
            title,
            selected_skill,
            selected_skills,
            signal["touched_paths"],
            signal["edited_paths"],
            signal["test_commands"],
        ),
    }
    return entry


def _extract_json_object(text: str) -> dict[str, Any] | None:
    text = text.strip()
    if not text:
        return None
    try:
        value = json.loads(text)
        return value if isinstance(value, dict) else None
    except ValueError:
        pass
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end <= start:
        return None
    try:
        value = json.loads(text[start : end + 1])
    except ValueError:
        return None
    return value if isinstance(value, dict) else None


def _string_list(value: Any, limit: int = 5) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    if isinstance(value, list):
        return [str(item) for item in value[:limit]]
    return [str(value)]


def _responses_text(body: dict[str, Any]) -> str:
    if isinstance(body.get("output_text"), str):
        return body["output_text"]
    parts: list[str] = []
    for item in body.get("output") or []:
        for content in item.get("content") or []:
            text = content.get("text")
            if isinstance(text, str):
                parts.append(text)
    return "\n".join(parts)


def _call_openai_compatible(
    *,
    base_url: str,
    api_key: str,
    model: str,
    provider_api: str,
    prompt: str,
    max_tokens: int,
) -> str:
    base_url = base_url.rstrip("/")
    if provider_api == "openai-responses":
        payload = {
            "model": model,
            "input": [{"role": "user", "content": prompt}],
            "max_output_tokens": max_tokens,
        }
        path = "/responses"
    else:
        payload = {
            "model": model,
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": max_tokens,
            "temperature": 0,
            "response_format": {"type": "json_object"},
        }
        path = "/chat/completions"
    request = urllib.request.Request(
        base_url + path,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=240) as response:
        body = json.loads(response.read().decode("utf-8", errors="replace"))
    if provider_api == "openai-responses":
        return _responses_text(body)
    choices = body.get("choices") or []
    if not choices:
        return ""
    message = choices[0].get("message") or {}
    return str(message.get("content") or "")


def _summary_prompt(entry: dict[str, Any], skill_resources: dict[str, Any]) -> str:
    evidence = {
        "task_name": entry.get("task_name"),
        "repo": entry.get("repo"),
        "issue_title": entry.get("issue_title"),
        "reward": entry.get("reward"),
        "exception": entry.get("exception"),
        "selected_skill": entry.get("selected_skill"),
        "selected_skills": entry.get("selected_skills"),
        "touched_paths": entry.get("touched_paths"),
        "edited_paths": entry.get("edited_paths"),
        "test_commands": entry.get("test_commands"),
        "tool_counts": entry.get("tool_counts"),
        "tool_sequence": entry.get("tool_sequence"),
        "problem_excerpt": entry.get("problem_excerpt"),
    }
    return (
        "You summarize one SWE-Bench Pi-agent trace into one task-scoped stage skill card. "
        "Return only one valid JSON object. Do not wrap it in markdown. Do not add prose before or after it. "
        "There are no global skills. For this one task, organize which skills would be usable "
        "inside each SWE phase: reproduce, localize, edit, validate, recover. Ground your answer "
        "in the trace evidence and only the skill resources that this trace actually touched. "
        "If those touched skills include Python or shell scripts, distill the script into concise "
        "script_resources: what the script does, when to use it, command shape, and the relevant "
        "functions or checks. Only emit distilled_scripts when a short reusable .py or .sh helper "
        "should be bundled and the content is complete, syntax-valid, and directly runnable; otherwise "
        "leave it empty. The most important output is controllable "
        "flow: during meaningless rollout, failed search, repeated test failure, or debugging drift, "
        "tell the future agent when it should do what next and what evidence moves it to the next phase. "
        "Return strict JSON with keys: summary, task_stage_skills, harness_hints, avoid, "
        "retrieval_keywords, confidence. task_stage_skills must be a list of objects with keys "
        "stage, skills, control_points. stage must be one of reproduce, localize, edit, validate, recover. "
        "Each skill must have keys name, trigger, actions, evidence_to_collect, stop_condition, "
        "source_skill_paths, script_resources, distilled_scripts. script_resources objects have keys "
        "source_path, language, purpose, when_to_use, command_hint, relevant_functions. distilled_scripts "
        "objects have keys filename, language, purpose, content and should be short, safe helper code only. "
        "control_points objects have keys trigger, action, evidence_to_collect, stop_condition. "
        "Keep lists short and reusable. Do not collapse this into a global multi-task summary. "
        "Do not include secrets or provider credentials.\n\nTRACE-SELECTED SKILL RESOURCES:\n"
        + json.dumps(skill_resources, ensure_ascii=False, indent=2)
        + "\n\nTRACE EVIDENCE:\n"
        + json.dumps(evidence, ensure_ascii=False, indent=2)
    )


def _stage_name(value: Any) -> str:
    stage = str(value or "").strip().lower()
    return stage if stage in SWE_STAGES else "recover"


def _safe_resource_filename(value: Any, language: str) -> str:
    raw = str(value or "").strip() or "helper"
    filename = re.sub(r"[^A-Za-z0-9_.-]+", "_", raw).strip("._")
    if not filename:
        filename = "helper"
    suffix = ".py" if language == "python" else ".sh" if language == "shell" else ""
    if suffix and not filename.endswith(suffix):
        filename += suffix
    return filename[:120]


def _normalize_script_resources(value: Any, limit: int = 5) -> list[dict[str, Any]]:
    resources: list[dict[str, Any]] = []
    if not isinstance(value, list):
        return resources
    for item in value[:limit]:
        if not isinstance(item, dict):
            continue
        resources.append(
            {
                "source_path": str(item.get("source_path") or ""),
                "language": str(item.get("language") or ""),
                "purpose": str(item.get("purpose") or ""),
                "when_to_use": str(item.get("when_to_use") or ""),
                "command_hint": str(item.get("command_hint") or ""),
                "relevant_functions": _string_list(item.get("relevant_functions"), limit=8),
            }
        )
    return resources


def _normalize_distilled_scripts(value: Any, limit: int = 3) -> list[dict[str, Any]]:
    scripts: list[dict[str, Any]] = []
    if not isinstance(value, list):
        return scripts
    for item in value[:limit]:
        if not isinstance(item, dict):
            continue
        language = str(item.get("language") or "").strip().lower()
        if language in {"bash", "sh"}:
            language = "shell"
        if language not in {"python", "shell"}:
            continue
        content = str(item.get("content") or "").strip()
        if not content:
            continue
        if language == "python":
            try:
                compile(content, str(item.get("filename") or "<distilled-script>"), "exec")
            except SyntaxError:
                continue
        scripts.append(
            {
                "filename": _safe_resource_filename(item.get("filename"), language),
                "language": language,
                "purpose": str(item.get("purpose") or ""),
                "content": content[:6000],
            }
        )
    return scripts


def _normalize_task_stage_skills(value: Any) -> list[dict[str, Any]]:
    stages: list[dict[str, Any]] = []
    if not isinstance(value, list):
        return stages
    for stage_item in value:
        if not isinstance(stage_item, dict):
            continue
        skills: list[dict[str, Any]] = []
        for skill_item in stage_item.get("skills") or []:
            if not isinstance(skill_item, dict):
                continue
            skills.append(
                {
                    "name": str(skill_item.get("name") or ""),
                    "trigger": str(skill_item.get("trigger") or ""),
                    "actions": _string_list(skill_item.get("actions"), limit=8),
                    "evidence_to_collect": str(skill_item.get("evidence_to_collect") or ""),
                    "stop_condition": str(skill_item.get("stop_condition") or ""),
                    "source_skill_paths": _string_list(
                        skill_item.get("source_skill_paths"), limit=8
                    ),
                    "script_resources": _normalize_script_resources(
                        skill_item.get("script_resources"), limit=6
                    ),
                    "distilled_scripts": _normalize_distilled_scripts(
                        skill_item.get("distilled_scripts"), limit=3
                    ),
                }
            )
        control_points: list[dict[str, str]] = []
        for point in stage_item.get("control_points") or []:
            if not isinstance(point, dict):
                continue
            control_points.append(
                {
                    "trigger": str(point.get("trigger") or ""),
                    "action": str(point.get("action") or ""),
                    "evidence_to_collect": str(point.get("evidence_to_collect") or ""),
                    "stop_condition": str(point.get("stop_condition") or ""),
                }
            )
        if skills or control_points:
            stages.append(
                {
                    "stage": _stage_name(stage_item.get("stage")),
                    "skills": skills[:8],
                    "control_points": control_points[:8],
                }
            )
    return stages


def _fallback_task_stage_skills(entry: dict[str, Any]) -> list[dict[str, Any]]:
    title = entry.get("issue_title") or entry.get("summary") or "the task behavior is unclear"
    tests = entry.get("test_commands") or []
    paths = entry.get("touched_paths") or []
    edited = entry.get("edited_paths") or []
    return [
        {
            "stage": "reproduce",
            "skills": [
                {
                    "name": "task-reproduce-from-issue",
                    "trigger": str(title),
                    "actions": ["Create or run the smallest command that exposes the reported behavior."],
                    "evidence_to_collect": "Failing output, exception, assertion, or minimal reproduction observation.",
                    "stop_condition": "The failure is observable or the issue has a clearly testable code path.",
                    "source_skill_paths": [],
                    "script_resources": [],
                    "distilled_scripts": [],
                }
            ],
            "control_points": [],
        },
        {
            "stage": "localize",
            "skills": [
                {
                    "name": "task-localize-high-signal-paths",
                    "trigger": "The reproduction points to a behavior-owning module.",
                    "actions": [f"Inspect high-signal paths: {', '.join(paths[:4]) or 'none'}"],
                    "evidence_to_collect": "The function or branch that owns the incorrect behavior.",
                    "stop_condition": "One small source location explains the observed failure.",
                    "source_skill_paths": [],
                    "script_resources": [],
                    "distilled_scripts": [],
                }
            ],
            "control_points": [],
        },
        {
            "stage": "edit",
            "skills": [
                {
                    "name": "task-edit-minimal-owner",
                    "trigger": "A narrow owner location is identified.",
                    "actions": [f"Patch only the owner location: {', '.join(edited[:3]) or 'not yet known'}"],
                    "evidence_to_collect": "A small diff preserving existing API and style.",
                    "stop_condition": "The diff addresses the reproduced behavior without broad rewrites.",
                    "source_skill_paths": [],
                    "script_resources": [],
                    "distilled_scripts": [],
                }
            ],
            "control_points": [],
        },
        {
            "stage": "validate",
            "skills": [
                {
                    "name": "task-validate-targeted",
                    "trigger": "A patch is applied.",
                    "actions": tests[:2] or ["Run the nearest focused test or reproduction command."],
                    "evidence_to_collect": "Passing focused command or clear remaining failure.",
                    "stop_condition": "The reproduction passes and no nearby regression is visible.",
                    "source_skill_paths": [],
                    "script_resources": [],
                    "distilled_scripts": [],
                }
            ],
            "control_points": [],
        },
        {
            "stage": "recover",
            "skills": [
                {
                    "name": "task-recover-from-drift",
                    "trigger": "Search, editing, or validation repeats without new evidence.",
                    "actions": ["Stop the loop, restate the failing observation, and re-localize from the strongest evidence."],
                    "evidence_to_collect": "The last command that changed the hypothesis or invalidated it.",
                    "stop_condition": "A new concrete owner hypothesis is available or the previous edit is reverted.",
                    "source_skill_paths": [],
                    "script_resources": [],
                    "distilled_scripts": [],
                }
            ],
            "control_points": [],
        },
    ]


def _ensure_all_stages(
    task_stage_skills: list[dict[str, Any]],
    entry: dict[str, Any],
) -> list[dict[str, Any]]:
    def _substantial_skill(skill: Any) -> bool:
        if not isinstance(skill, dict):
            return False
        actions = skill.get("actions") or []
        action_text = " ".join(str(action) for action in actions)
        evidence = str(skill.get("evidence_to_collect") or "")
        stop = str(skill.get("stop_condition") or "")
        trigger = str(skill.get("trigger") or "")
        return len(action_text.strip()) >= 12 and len(trigger + evidence + stop) >= 30

    by_stage: dict[str, dict[str, Any]] = {}
    for stage_item in task_stage_skills:
        if not isinstance(stage_item, dict):
            continue
        stage = _stage_name(stage_item.get("stage"))
        existing = by_stage.setdefault(
            stage,
            {"stage": stage, "skills": [], "control_points": []},
        )
        existing["skills"].extend(stage_item.get("skills") or [])
        existing["control_points"].extend(stage_item.get("control_points") or [])

    fallbacks = {
        _stage_name(fallback.get("stage")): fallback
        for fallback in _fallback_task_stage_skills(entry)
    }
    for stage, fallback in fallbacks.items():
        stage = _stage_name(fallback.get("stage"))
        by_stage.setdefault(stage, fallback)
        if not any(_substantial_skill(skill) for skill in by_stage[stage].get("skills") or []):
            by_stage[stage] = fallback

    return [by_stage[stage] for stage in SWE_STAGES if stage in by_stage]


def summarize_with_backbone(
    entries: list[dict[str, Any]],
    *,
    max_entries: int,
    max_tokens: int,
    max_chars_per_skill: int,
    max_total_chars: int,
) -> dict[str, Any]:
    summarized = 0
    errors: list[str] = []
    model_config: dict[str, Any] | None = None
    resource_counts: Counter[str] = Counter()
    selected_entries = entries if max_entries <= 0 else entries[:max_entries]
    for entry in selected_entries:
        base_url = str(entry.get("provider_base_url") or "")
        model = str(entry.get("provider_model") or "")
        provider_api = str(entry.get("provider_api") or "openai-completions")
        api_key_env = str(entry.get("api_key_env") or "")
        api_key = os.getenv(api_key_env) if api_key_env else None
        if not base_url or not model or not api_key:
            errors.append(
                f"{entry.get('entry_id')}: missing base_url/model/api key env"
            )
            continue
        model_config = {
            "base_url": base_url,
            "model": model,
            "provider_api": provider_api,
            "api_key_env": api_key_env,
        }
        skill_resources = load_task_skill_resources(
            entry=entry,
            max_chars_per_skill=max_chars_per_skill,
            max_total_chars=max_total_chars,
        )
        entry["selected_skill_resources"] = [
            {
                "name": resource.get("name"),
                "source_path": resource.get("source_path"),
                "relative_path": resource.get("relative_path"),
                "scripts": [
                    {
                        "source_path": script.get("source_path"),
                        "language": script.get("language"),
                        "sha256": script.get("sha256"),
                    }
                    for script in (resource.get("scripts") or [])
                ],
                "missing_local_copy": resource.get("missing_local_copy", False),
            }
            for resource in (skill_resources.get("resources") or [])
        ]
        resource_counts["skills"] += len(skill_resources.get("resources") or [])
        resource_counts["scripts"] += sum(
            len(resource.get("scripts") or [])
            for resource in (skill_resources.get("resources") or [])
            if isinstance(resource, dict)
        )
        try:
            text = _call_openai_compatible(
                base_url=base_url,
                api_key=api_key,
                model=model,
                provider_api=provider_api,
                prompt=_summary_prompt(entry, skill_resources),
                max_tokens=max_tokens,
            )
            parsed = _extract_json_object(text)
        except (OSError, urllib.error.URLError, urllib.error.HTTPError, ValueError) as exc:
            errors.append(f"{entry.get('entry_id')}: {type(exc).__name__}: {exc}")
            continue
        if not parsed:
            errors.append(f"{entry.get('entry_id')}: non_json_summary")
            continue
        entry["summary"] = str(parsed.get("summary") or entry["summary"])
        entry["summary_source"] = "backbone"
        task_stage_skills = _normalize_task_stage_skills(
            parsed.get("task_stage_skills") or parsed.get("stage_skills")
        )
        entry["task_stage_skills"] = _ensure_all_stages(task_stage_skills, entry)
        entry["harness_hints"] = _string_list(parsed.get("harness_hints"), limit=5)
        entry["avoid"] = _string_list(parsed.get("avoid"), limit=5)
        entry["summary_confidence"] = parsed.get("confidence")
        entry["keywords"] = sorted(
            set(entry.get("keywords") or [])
            | {str(item).lower() for item in (parsed.get("retrieval_keywords") or [])}
        )[:100]
        summarized += 1
    return {
        "enabled": True,
        "mode": "backbone",
        "model_config": model_config,
        "requested_entries": len(selected_entries),
        "summarized_entries": summarized,
        "skill_resources": {
            "mode": "trace_selected_resources",
            "skills_seen": resource_counts["skills"],
            "scripts_seen": resource_counts["scripts"],
            "max_chars_per_skill": max_chars_per_skill,
            "max_total_chars": max_total_chars,
        },
        "errors": errors[:20],
    }


def _trial_result_paths(job_dir: Path) -> list[Path]:
    paths = sorted(job_dir.glob("*/result.json"))
    return [path for path in paths if path.parent != job_dir]


def _aggregate(entries: list[dict[str, Any]]) -> dict[str, Any]:
    by_repo: dict[str, Any] = {}
    by_stage: dict[str, Any] = {}
    repo_entries: dict[str, list[dict[str, Any]]] = defaultdict(list)
    stage_entries: dict[str, list[dict[str, Any]]] = defaultdict(list)
    rewards = []
    for entry in entries:
        repo_entries[str(entry.get("repo") or "unknown")].append(entry)
        for stage_item in _entry_task_stage_skills(entry):
            if not isinstance(stage_item, dict):
                continue
            stage_entries[str(stage_item.get("stage") or "unknown")].append(entry)
        if entry.get("reward") is not None:
            rewards.append(float(entry["reward"]))

    for repo, repo_items in sorted(repo_entries.items()):
        stage_skills: dict[str, Counter[str]] = defaultdict(Counter)
        paths = Counter()
        for entry in repo_items:
            for stage_item in _entry_task_stage_skills(entry):
                if not isinstance(stage_item, dict):
                    continue
                stage = str(stage_item.get("stage") or "unknown")
                for stage_skill in stage_item.get("skills") or []:
                    if not isinstance(stage_skill, dict):
                        continue
                    stage_skills[stage][str(stage_skill.get("name") or "unknown")] += 1
            paths.update(entry.get("touched_paths") or [])
        by_repo[repo] = {
            "trials": len(repo_items),
            "resolved": sum(1 for entry in repo_items if entry.get("reward") == 1.0),
            "stage_skills": {
                stage: dict(counter.most_common())
                for stage, counter in sorted(stage_skills.items())
            },
            "common_paths": [path for path, _ in paths.most_common(10)],
        }

    for stage, stage_items in sorted(stage_entries.items()):
        repos = Counter(str(entry.get("repo") or "unknown") for entry in stage_items)
        by_stage[stage] = {
            "trials": len(stage_items),
            "resolved": sum(1 for entry in stage_items if entry.get("reward") == 1.0),
            "repos": dict(repos.most_common()),
        }

    return {
        "trials": len(entries),
        "mean_reward": statistics.fmean(rewards) if rewards else None,
        "resolved": sum(1 for entry in entries if entry.get("reward") == 1.0),
        "by_repo": by_repo,
        "by_stage": by_stage,
    }


def _empty_memory() -> dict[str, Any]:
    return {
        "schema_version": 2,
        "active_version": None,
        "versions": {},
    }


def _load_memory(path: Path) -> dict[str, Any]:
    if not path.exists():
        return _empty_memory()
    data = _load_json(path)
    if not isinstance(data, dict) or not isinstance(data.get("versions"), dict):
        return _empty_memory()
    data["schema_version"] = max(2, int(data.get("schema_version") or 1))
    data.setdefault("active_version", None)
    return data


def _next_version_id(memory: dict[str, Any]) -> str:
    max_seen = 0
    for version_id in (memory.get("versions") or {}):
        match = re.fullmatch(r"v(\d+)", str(version_id))
        if match:
            max_seen = max(max_seen, int(match.group(1)))
    return f"v{max_seen + 1:04d}"


def build_version(
    *,
    job_dirs: list[Path],
    version_id: str,
    parent_version: str | None,
    max_trials: int | None,
) -> dict[str, Any]:
    entries: list[dict[str, Any]] = []
    for job_dir in job_dirs:
        for trial_result_path in _trial_result_paths(job_dir):
            if max_trials is not None and len(entries) >= max_trials:
                break
            entry = _trial_entry(job_dir, trial_result_path)
            if entry is not None:
                entries.append(entry)
    return {
        "version_id": version_id,
        "parent_version": parent_version,
        "created_at": _utc_now(),
        "source_jobs": [str(job_dir.relative_to(ROOT)) for job_dir in job_dirs],
        "entries": entries,
        "aggregates": _aggregate(entries),
    }


def write_markdown(memory: dict[str, Any], path: Path) -> None:
    lines = [
        "# Skill/Harness Evolution Memory",
        "",
        f"- Active version: `{memory.get('active_version')}`",
        f"- Schema version: `{memory.get('schema_version')}`",
        "",
        "## Versions",
        "",
        "| Version | Parent | Created | Trials | Resolved | Mean Reward | Source Jobs |",
        "| --- | --- | --- | ---: | ---: | ---: | --- |",
    ]
    for version_id, version in sorted((memory.get("versions") or {}).items()):
        aggregates = version.get("aggregates") or {}
        lines.append(
            "| {version} | {parent} | {created} | {trials} | {resolved} | {mean_reward} | {source_jobs} |".format(
                version=version_id,
                parent=version.get("parent_version") or "",
                created=version.get("created_at") or "",
                trials=aggregates.get("trials", 0),
                resolved=aggregates.get("resolved", 0),
                mean_reward=aggregates.get("mean_reward"),
                source_jobs=", ".join(version.get("source_jobs") or []),
            )
        )
    active = (memory.get("versions") or {}).get(memory.get("active_version")) or {}
    if active:
        summary_backbone = active.get("summary_backbone") or {}
        lines.extend(
            [
                "",
                "## Active Version Summary Model",
                "",
                f"- Enabled: `{summary_backbone.get('enabled')}`",
                f"- Mode: `{summary_backbone.get('mode')}`",
                f"- Model config: `{json.dumps(summary_backbone.get('model_config') or {}, ensure_ascii=False)}`",
                f"- Summarized entries: `{summary_backbone.get('summarized_entries', 0)}`",
                f"- Errors: `{len(summary_backbone.get('errors') or [])}`",
            ]
        )
        lines.extend(["", "## Active Version Repo Summary", ""])
        by_repo = (active.get("aggregates") or {}).get("by_repo") or {}
        for repo, stats in sorted(by_repo.items()):
            lines.extend(
                [
                    f"### {repo}",
                    "",
                    f"- Trials: {stats.get('trials', 0)}",
                    f"- Resolved: {stats.get('resolved', 0)}",
                    f"- Stage skills: `{json.dumps(stats.get('stage_skills') or {}, ensure_ascii=False)}`",
                    f"- Common paths: {', '.join(stats.get('common_paths') or [])}",
                    "",
                ]
            )
    path.write_text("\n".join(lines) + "\n")


def _safe_card_name(entry: dict[str, Any]) -> str:
    base = str(entry.get("task_skill_id") or entry.get("entry_id") or "task")
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", base).strip("_")[:180] or "task"


def write_task_skill_cards(
    version: dict[str, Any],
    *,
    output_root: Path,
    clean: bool,
) -> Path:
    version_id = str(version.get("version_id") or "unknown")
    cards_dir = output_root / version_id
    if clean and cards_dir.exists():
        for path in sorted(cards_dir.rglob("*"), reverse=True):
            if path.is_file():
                path.unlink()
            elif path.is_dir():
                path.rmdir()
    cards_dir.mkdir(parents=True, exist_ok=True)
    for entry in version.get("entries") or []:
        if not isinstance(entry, dict):
            continue
        repo = str(entry.get("repo") or "unknown")
        repo_dir = cards_dir / re.sub(r"[^A-Za-z0-9_.-]+", "_", repo)
        repo_dir.mkdir(parents=True, exist_ok=True)
        card = {
            "schema_version": 2,
            "version_id": version_id,
            "parent_version": version.get("parent_version"),
            "kind": "swe_task_stage_skills",
            "entry": entry,
        }
        (repo_dir / f"{_safe_card_name(entry)}.json").write_text(
            json.dumps(card, ensure_ascii=False, indent=2) + "\n"
        )
    return cards_dir


def skill_quality_metadata(
    *,
    entry: dict[str, Any],
    stage: str,
    stage_skill: dict[str, Any],
) -> dict[str, Any]:
    name = str(stage_skill.get("name") or f"{stage}-task-skill").strip()
    trigger = str(stage_skill.get("trigger") or "")
    actions = [str(action) for action in (stage_skill.get("actions") or []) if action]
    evidence = str(stage_skill.get("evidence_to_collect") or "")
    stop = str(stage_skill.get("stop_condition") or "")
    source_skill_paths = [str(path) for path in (stage_skill.get("source_skill_paths") or [])]
    script_resources = stage_skill.get("script_resources") or []
    distilled_scripts = stage_skill.get("distilled_scripts") or []
    entry_paths = [str(path) for path in (entry.get("touched_paths") or []) + (entry.get("edited_paths") or [])]
    entry_tests = [str(command) for command in (entry.get("test_commands") or [])]
    combined = "\n".join(
        [name, trigger, *actions, evidence, stop, *source_skill_paths, *entry_paths, *entry_tests]
    )
    lowered = combined.lower()

    risk_flags: list[str] = []
    useful_signals: list[str] = []
    if name in GENERIC_FALLBACK_SKILL_NAMES:
        risk_flags.append("generic_fallback_skill")
    if any(phrase in lowered for phrase in GENERIC_SKILL_PHRASES):
        risk_flags.append("generic_workflow_language")
    if PRIVATE_PATH_RE.search(combined):
        risk_flags.append("private_or_sandbox_path")

    source_paths = sorted(set(SOURCE_PATH_RE.findall(combined)))
    if source_paths:
        useful_signals.append("repository_relative_paths")
    elif stage in {"localize", "edit"}:
        risk_flags.append("missing_owner_path")

    has_test_command = bool(entry_tests) or any(pattern in lowered for pattern in TEST_COMMAND_PATTERNS)
    if has_test_command:
        useful_signals.append("focused_validation")
    elif stage in {"reproduce", "validate"}:
        risk_flags.append("missing_validation_command")

    if len(" ".join(actions).strip()) < 24:
        risk_flags.append("thin_actions")
    if len(stop.strip()) < 18:
        risk_flags.append("missing_abort_or_stop_condition")
    if _entry_reward_value(entry) < 1.0:
        risk_flags.append("unresolved_source_trace")
    if entry.get("exception"):
        risk_flags.append("source_trace_exception")
    if len(source_paths) > 6:
        risk_flags.append("over_specific_path_list")
    risk_flags = sorted(set(risk_flags))

    score = 0.15
    if _entry_reward_value(entry) >= 1.0:
        score += 0.22
    if source_paths:
        score += min(len(source_paths), 3) * 0.08
    if has_test_command:
        score += 0.16
    if len(trigger.strip()) >= 24:
        score += 0.08
    if len(evidence.strip()) >= 24:
        score += 0.08
    if len(stop.strip()) >= 18:
        score += 0.08
    if name and name not in GENERIC_FALLBACK_SKILL_NAMES and not name.startswith("task-"):
        score += 0.12
    if script_resources or distilled_scripts:
        score += 0.06
    if "generic_fallback_skill" in risk_flags:
        score -= 0.30
    if "generic_workflow_language" in risk_flags:
        score -= 0.12
    if "private_or_sandbox_path" in risk_flags:
        score -= 0.20
    if "missing_owner_path" in risk_flags:
        score -= 0.16
    if "missing_validation_command" in risk_flags:
        score -= 0.10
    if "unresolved_source_trace" in risk_flags:
        score -= 0.25
    if "source_trace_exception" in risk_flags:
        score -= 0.15
    score = max(0.0, min(1.0, score))

    active = (
        score >= 0.58
        and "generic_fallback_skill" not in risk_flags
        and "private_or_sandbox_path" not in risk_flags
        and "unresolved_source_trace" not in risk_flags
    )
    if score >= 0.76:
        tier = "high"
    elif score >= 0.58:
        tier = "medium"
    else:
        tier = "low"
    applicability_bits = []
    if source_paths:
        applicability_bits.append("First confirm repository evidence around: " + ", ".join(source_paths[:4]) + ".")
    if has_test_command:
        applicability_bits.append("Prefer this skill only when the issue can be checked with the recorded focused reproduction or validation command.")
    if trigger:
        applicability_bits.append("The observed behavior should match the trigger: " + trigger[:240])
    if not applicability_bits:
        applicability_bits.append("Use only as background memory; do not follow it unless current repository evidence independently matches.")
    return {
        "active": active,
        "score": round(score, 3),
        "quality_score": round(score, 3),
        "tier": tier,
        "quality_tier": tier,
        "risk_flags": risk_flags,
        "useful_signals": useful_signals,
        "source_paths": source_paths[:8],
        "use_policy": "evidence-gated" if active else "memory-only",
        "applicability": " ".join(applicability_bits),
    }


def _skill_md_text(
    *,
    entry: dict[str, Any],
    stage: str,
    stage_skill: dict[str, Any],
    control_points: list[dict[str, Any]],
    version: dict[str, Any],
) -> str:
    name = str(stage_skill.get("name") or f"{stage}-task-skill")
    quality = stage_skill.get("quality_metadata")
    if not isinstance(quality, dict):
        quality = skill_quality_metadata(entry=entry, stage=stage, stage_skill=stage_skill)
    description = (
        f"Task-specific {stage} skill from {entry.get('task_name')}. "
        f"Use when {stage_skill.get('trigger') or entry.get('issue_title') or 'a similar stage appears'}."
    )
    source_skill_paths = stage_skill.get("source_skill_paths") or []
    actions = stage_skill.get("actions") or []
    script_resources = stage_skill.get("script_resources") or []
    distilled_scripts = stage_skill.get("distilled_scripts") or []
    lines = [
        "---",
        f"name: {name}",
        "description: " + json.dumps(description, ensure_ascii=False),
        f"active: {'true' if quality['active'] else 'false'}",
        f"quality_score: {quality['score']:.2f}",
        f"quality_tier: {quality['tier']}",
        "risk_flags: " + json.dumps(quality["risk_flags"], ensure_ascii=False),
        "use_policy: " + json.dumps(quality["use_policy"], ensure_ascii=False),
        "---",
        "",
        f"# {name}",
        "",
        f"- Memory version: `{version.get('version_id')}`",
        f"- Parent version: `{version.get('parent_version') or ''}`",
        f"- Source task: `{entry.get('task_name')}`",
        f"- Source job: `{entry.get('source_job')}`",
        f"- Stage: `{stage}`",
        f"- Reward: `{entry.get('reward')}`",
        f"- Active: `{str(quality['active']).lower()}`",
        f"- Quality score: `{quality['score']:.2f}` (`{quality['tier']}`)",
        f"- Risk flags: `{', '.join(quality['risk_flags']) or 'none'}`",
        "",
        "## Applicability",
        "",
        str(quality["applicability"]),
        "",
        "## Trigger",
        "",
        str(stage_skill.get("trigger") or ""),
        "",
        "## Actions",
        "",
        *[f"{index}. {action}" for index, action in enumerate(actions, start=1)],
        "",
        "## Evidence",
        "",
        str(stage_skill.get("evidence_to_collect") or ""),
        "",
        "## Stop Condition",
        "",
        str(stage_skill.get("stop_condition") or ""),
        "",
        "## Abort Conditions",
        "",
        "Ignore this skill if the first concrete repository check does not match its applicability, owner paths, or validation evidence.",
        "",
        "## Source Skill Files",
        "",
        *[f"- {path}" for path in source_skill_paths],
        "",
        "## Script Resources",
        "",
    ]
    if script_resources:
        for resource in script_resources:
            if not isinstance(resource, dict):
                continue
            lines.extend(
                [
                    f"### {resource.get('source_path') or 'script'}",
                    "",
                    f"- Language: {resource.get('language') or ''}",
                    f"- Purpose: {resource.get('purpose') or ''}",
                    f"- When to use: {resource.get('when_to_use') or ''}",
                    f"- Command: `{resource.get('command_hint') or ''}`",
                    f"- Relevant functions: {', '.join(resource.get('relevant_functions') or [])}",
                    "",
                ]
            )
    else:
        lines.extend(["- none", ""])
    if distilled_scripts:
        lines.extend(["## Bundled Distilled Scripts", ""])
        for script in distilled_scripts:
            if isinstance(script, dict):
                lines.append(
                    f"- `scripts/{script.get('filename')}`: {script.get('purpose') or ''}"
                )
        lines.append("")
    lines.extend(
        [
        "",
        "## Control Points",
        "",
        ]
    )
    for point in control_points:
        if not isinstance(point, dict):
            continue
        lines.extend(
            [
                f"- Trigger: {point.get('trigger') or ''}",
                f"- Action: {point.get('action') or ''}",
                f"- Evidence: {point.get('evidence_to_collect') or ''}",
                f"- Stop: {point.get('stop_condition') or ''}",
                "",
            ]
        )
    lines.extend(
        [
            "## Harness Notes",
            "",
            *[f"- {hint}" for hint in (entry.get("harness_hints") or [])],
            "",
            "## Avoid",
            "",
            *[f"- {item}" for item in (entry.get("avoid") or [])],
            "",
        ]
    )
    return "\n".join(lines)


def _stage_slug(stage: str) -> str:
    return stage if stage in SWE_STAGES else "recover"


def _write_distilled_scripts(skill_dir: Path, stage_skill: dict[str, Any]) -> None:
    scripts = stage_skill.get("distilled_scripts") or []
    if not scripts:
        return
    scripts_dir = skill_dir / "scripts"
    scripts_dir.mkdir(parents=True, exist_ok=True)
    for script in scripts:
        if not isinstance(script, dict):
            continue
        language = str(script.get("language") or "").lower()
        filename = _safe_resource_filename(script.get("filename"), language)
        content = str(script.get("content") or "").strip()
        if not content:
            continue
        path = scripts_dir / filename
        path.write_text(content.rstrip() + "\n")
        if language == "shell":
            path.chmod(0o755)


def _entry_task_stage_skills(entry: dict[str, Any]) -> list[dict[str, Any]]:
    task_stage_skills = entry.get("task_stage_skills")
    if isinstance(task_stage_skills, list) and task_stage_skills:
        return _ensure_all_stages(task_stage_skills, entry)

    old_generated = entry.get("generated_skills") or []
    old_control = entry.get("control_points") or []
    if old_generated or old_control:
        by_stage: dict[str, dict[str, Any]] = {}
        for point in old_control:
            if not isinstance(point, dict):
                continue
            stage = _stage_name(point.get("phase"))
            by_stage.setdefault(stage, {"stage": stage, "skills": [], "control_points": []})
            by_stage[stage]["control_points"].append(
                {
                    "trigger": str(point.get("trigger") or ""),
                    "action": str(point.get("action") or ""),
                    "evidence_to_collect": str(point.get("evidence_to_collect") or ""),
                    "stop_condition": str(point.get("stop_condition") or ""),
                }
            )
        for skill in old_generated:
            if not isinstance(skill, dict):
                continue
            target_stages = list(by_stage) or ["edit"]
            for stage in target_stages[:1]:
                by_stage.setdefault(stage, {"stage": stage, "skills": [], "control_points": []})
                by_stage[stage]["skills"].append(
                    {
                        "name": str(skill.get("name") or f"{stage}-task-skill"),
                        "trigger": str(skill.get("trigger") or ""),
                        "actions": _string_list(skill.get("actions"), limit=8),
                        "evidence_to_collect": "",
                        "stop_condition": str(skill.get("stop_condition") or ""),
                        "source_skill_paths": _string_list(skill.get("source_skills"), limit=8),
                        "script_resources": [],
                        "distilled_scripts": [],
                    }
                )
        return _ensure_all_stages(list(by_stage.values()), entry)

    return _fallback_task_stage_skills(entry)


def annotate_entry_skill_quality(entry: dict[str, Any]) -> None:
    for stage_item in _entry_task_stage_skills(entry):
        if not isinstance(stage_item, dict):
            continue
        stage = _stage_slug(str(stage_item.get("stage") or "recover"))
        for stage_skill in stage_item.get("skills") or []:
            if not isinstance(stage_skill, dict):
                continue
            quality = skill_quality_metadata(
                entry=entry,
                stage=stage,
                stage_skill=stage_skill,
            )
            stage_skill["active"] = quality["active"]
            stage_skill["quality_score"] = quality["quality_score"]
            stage_skill["quality_tier"] = quality["quality_tier"]
            stage_skill["risk_flags"] = quality["risk_flags"]
            stage_skill["use_policy"] = quality["use_policy"]
            stage_skill["applicability"] = quality["applicability"]
            stage_skill["quality_metadata"] = quality


def annotate_version_skill_quality(version: dict[str, Any]) -> None:
    for entry in version.get("entries") or []:
        if isinstance(entry, dict):
            annotate_entry_skill_quality(entry)
    version["skill_quality_policy"] = {
        "min_active_score": 0.58,
        "blocked_risks": [
            "generic_fallback_skill",
            "private_or_sandbox_path",
            "unresolved_source_trace",
            "source_trace_exception",
        ],
        "generic_fallback_skill_names": sorted(GENERIC_FALLBACK_SKILL_NAMES),
    }


def write_generated_skill_files(
    version: dict[str, Any],
    *,
    output_root: Path,
    clean: bool,
) -> Path:
    version_id = str(version.get("version_id") or "unknown")
    root = output_root / version_id
    if clean and root.exists():
        for path in sorted(root.rglob("*"), reverse=True):
            if path.is_file():
                path.unlink()
            elif path.is_dir():
                path.rmdir()
    root.mkdir(parents=True, exist_ok=True)
    for entry in version.get("entries") or []:
        if not isinstance(entry, dict):
            continue
        repo = re.sub(r"[^A-Za-z0-9_.-]+", "_", str(entry.get("repo") or "unknown"))
        task = _safe_card_name(entry)
        for stage_item in _entry_task_stage_skills(entry):
            if not isinstance(stage_item, dict):
                continue
            stage = _stage_slug(str(stage_item.get("stage") or "recover"))
            control_points = stage_item.get("control_points") or []
            for stage_skill in stage_item.get("skills") or []:
                if not isinstance(stage_skill, dict):
                    continue
                skill_slug = re.sub(
                    r"[^A-Za-z0-9_.-]+",
                    "_",
                    str(stage_skill.get("name") or f"{stage}-task-skill"),
                ).strip("_")[:120] or f"{stage}-task-skill"
                skill_dir = root / repo / task / stage / skill_slug
                skill_dir.mkdir(parents=True, exist_ok=True)
                (skill_dir / "SKILL.md").write_text(
                    _skill_md_text(
                        entry=entry,
                        stage=stage,
                        stage_skill=stage_skill,
                        control_points=control_points,
                        version=version,
                    )
                )
                _write_distilled_scripts(skill_dir, stage_skill)
    return root


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Build a versioned skill/harness memory from Pi trace jobs."
    )
    parser.add_argument("--job-dir", action="append", type=Path, default=None)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--version-id", default=None)
    parser.add_argument("--parent-version", default=None)
    parser.add_argument("--no-activate", action="store_true")
    parser.add_argument("--max-trials", type=int, default=None)
    parser.add_argument("--md-out", type=Path, default=None)
    parser.add_argument("--task-skill-dir", type=Path, default=DEFAULT_TASK_SKILL_DIR)
    parser.add_argument("--no-task-skill-cards", action="store_true")
    parser.add_argument(
        "--generated-skill-dir",
        type=Path,
        default=DEFAULT_GENERATED_SKILL_DIR,
        help="Directory for generated task/stage skill folders.",
    )
    parser.add_argument("--no-generated-skill-files", action="store_true")
    parser.add_argument(
        "--append-generated-skill-files",
        action="store_true",
        help=(
            "Append generated SKILL.md files into an existing version directory "
            "instead of clearing the whole version first. Use this for sharded "
            "runs where multiple processes write different task-specific skills "
            "into one iteration version."
        ),
    )
    parser.add_argument(
        "--summarize-with-backbone",
        action="store_true",
        help="Ask the same provider/model recorded in the trace metadata to produce compact memory summaries.",
    )
    parser.add_argument(
        "--llm-max-entries",
        type=int,
        default=0,
        help="Maximum entries to summarize with the backbone model in one update. Use 0 for all entries.",
    )
    parser.add_argument(
        "--llm-max-tokens",
        type=int,
        default=2600,
        help="Maximum output tokens for each backbone summary call.",
    )
    parser.add_argument(
        "--skill-resource-max-chars",
        "--skill-catalog-max-chars",
        dest="skill_resource_max_chars",
        type=int,
        default=1200,
        help="Maximum characters to include from each trace-selected SKILL.md or script.",
    )
    parser.add_argument(
        "--skill-resource-max-total-chars",
        "--skill-catalog-max-total-chars",
        dest="skill_resource_max_total_chars",
        type=int,
        default=18000,
        help="Maximum total characters of trace-selected skill/script resources per summary.",
    )
    parser.add_argument(
        "--env-file",
        action="append",
        type=Path,
        default=None,
        help="Optional .env file containing the same provider API key env vars used by the traces.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if load_dotenv is not None:
        default_env = ROOT / ".env"
        if default_env.exists():
            load_dotenv(default_env, override=False)
        for env_file in args.env_file or []:
            load_dotenv(env_file.expanduser(), override=False)
    job_dirs = [path.expanduser().resolve() for path in (args.job_dir or [_latest_job_dir()])]
    for job_dir in job_dirs:
        if not (job_dir / "result.json").exists():
            raise SystemExit(f"Missing job result.json: {job_dir / 'result.json'}")

    out = args.out.expanduser()
    memory = _load_memory(out)
    version_id = args.version_id or _next_version_id(memory)
    if version_id in memory["versions"]:
        raise SystemExit(f"Version already exists: {version_id}")
    parent_version = args.parent_version
    if parent_version is None:
        parent_version = memory.get("active_version")

    version = build_version(
        job_dirs=job_dirs,
        version_id=version_id,
        parent_version=parent_version,
        max_trials=args.max_trials,
    )
    if args.summarize_with_backbone:
        version["summary_backbone"] = summarize_with_backbone(
            version["entries"],
            max_entries=args.llm_max_entries,
            max_tokens=args.llm_max_tokens,
            max_chars_per_skill=args.skill_resource_max_chars,
            max_total_chars=args.skill_resource_max_total_chars,
        )
        version["skill_resource_policy"] = {
            "mode": "trace_selected_resources",
            "script_suffixes": sorted(SCRIPT_SUFFIXES),
            "max_chars_per_skill": args.skill_resource_max_chars,
            "max_total_chars": args.skill_resource_max_total_chars,
        }
        version["aggregates"] = _aggregate(version["entries"])
    else:
        version["summary_backbone"] = {
            "enabled": False,
            "mode": "heuristic",
            "reason": "not_requested",
        }
    annotate_version_skill_quality(version)
    version["aggregates"] = _aggregate(version["entries"])
    memory["versions"][version_id] = version
    if not args.no_activate:
        memory["active_version"] = version_id

    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(memory, ensure_ascii=False, indent=2) + "\n")
    md_out = args.md_out or out.with_suffix(".md")
    write_markdown(memory, md_out)
    cards_dir = None
    if not args.no_task_skill_cards:
        cards_dir = write_task_skill_cards(
            version,
            output_root=args.task_skill_dir.expanduser(),
            clean=True,
        )
    generated_dir = None
    if not args.no_generated_skill_files:
        generated_dir = write_generated_skill_files(
            version,
            output_root=args.generated_skill_dir.expanduser(),
            clean=not args.append_generated_skill_files,
        )
    print(f"Wrote {out}")
    print(f"Wrote {md_out}")
    if cards_dir is not None:
        print(f"Wrote task skill cards under {cards_dir}")
    if generated_dir is not None:
        print(f"Wrote generated task/stage SKILL.md files under {generated_dir}")
    print(
        "version={version} parent={parent} entries={entries} active={active}".format(
            version=version_id,
            parent=parent_version,
            entries=len(version["entries"]),
            active=memory.get("active_version"),
        )
    )


if __name__ == "__main__":
    main()
