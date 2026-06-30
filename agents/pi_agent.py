import base64
import io
import json
import os
import re
import shlex
import tarfile
import tempfile
import textwrap
from pathlib import Path, PurePosixPath
from typing import Any

from harbor.agents.installed.base import (
    BaseInstalledAgent,
    NonZeroAgentExitCodeError,
    with_prompt_template,
)
from harbor.environments.base import BaseEnvironment
from harbor.models.agent.context import AgentContext
from harbor.models.trajectories import (
    Agent,
    FinalMetrics,
    Observation,
    ObservationResult,
    Step,
    ToolCall,
    Trajectory,
)
from harbor.models.trial.paths import EnvironmentPaths
from harbor.utils.trajectory_utils import format_trajectory_json

from agents.skill_harness_memory import (
    active_version,
    load_memory,
    memory_path_from_env,
    retrieve_task_memory,
)
from providers import (
    MACARON_ATTRIBUTION_HEADER_ENV,
    MACARON_ATTRIBUTION_HEADER_VALUE,
    ensure_reasoning_effort_none,
    ensure_macaron_attribution_header,
    is_macaron_base_url,
    requires_reasoning_effort_none,
)


ROOT = Path(__file__).resolve().parents[1]
PI_TASK_SKILLS_BASE = ROOT / "skills" / "accepted"
PI_SANDBOX_SKILLS_DIR = PurePosixPath("/tmp/pi-skills")
PI_SKILLS_INDEX_PATH = PurePosixPath(EnvironmentPaths.agent_dir / "pi-skills-index.json")
PI_SKILL_PACK_B64_PATH = PurePosixPath("/tmp/harbor-pi-skills.tar.gz.b64")
PI_SKILL_PACK_TAR_PATH = PurePosixPath("/tmp/harbor-pi-skills.tar.gz")
PI_SKILL_PACK_EXCLUDE_DIRS = {"benchmark-sharded-concurrency"}
PI_SKILL_PACK_CHUNK_SIZE = 24_000
PI_MAX_PROMPT_SKILLS = 8
PI_MACARON_PROXY_PORT = 18080
PI_REASONING_PROXY_PORT = PI_MACARON_PROXY_PORT
PI_RUNTIME_PATH_COMMAND = (
    "if [ -s ~/.nvm/nvm.sh ]; then . ~/.nvm/nvm.sh; fi; "
    'for HARBOR_PI_NODE_BIN in "$HOME"/.local/node-v*-linux-*/bin; do '
    'if [ -d "$HARBOR_PI_NODE_BIN" ]; then export PATH="$HARBOR_PI_NODE_BIN:$PATH"; fi; '
    "done"
)
PI_MIN_ACTIVE_SKILL_QUALITY = float(os.getenv("PI_MIN_ACTIVE_SKILL_QUALITY", "0.58"))
PI_BLOCKED_SKILL_RISKS = {
    "generic_fallback_skill",
    "private_or_sandbox_path",
    "unresolved_source_trace",
    "source_trace_exception",
}
PI_GENERIC_FALLBACK_SKILL_NAMES = {
    "task-recover-from-drift",
    "task-reproduce-from-issue",
    "task-validate-targeted",
    "task-edit-minimal-owner",
    "task-localize-high-signal-paths",
}
SANITIZED_ASSISTANT_CONTENT_MARKERS = (
    "</think>",
    "<think>",
)
FORBIDDEN_ASSISTANT_CONTENT_MARKERS = (
    "<tool_call>",
    "</tool_call>",
    "<arg_key>",
    "</arg_key>",
    "<arg_value>",
    "</arg_value>",
)


class ProviderTransientAgentError(RuntimeError):
    """Raised when Pi exits because the upstream model provider stayed unavailable."""


PI_SYSTEM_PROMPT = """You are Pi, a terminal-based software engineering agent running inside a Harbor SWE-Bench task sandbox.

Goal:
- Modify the repository in the current working directory so the benchmark issue is fixed.
- Prefer small, targeted changes. Do not change tests unless the task explicitly requires it.
- Inspect the repository before editing. Run relevant tests when practical.
- Leave the final state in the working tree; Harbor will run the verifier after you exit.
- This is not a repository research task. Never answer with a repository overview,
  research summary, README summary, or implementation plan as the final result.
- Continue with repository-specific tool calls: read/search the failing code, edit
  source files, and run targeted tests. A final answer is only allowed after you
  have attempted a source-code fix.

Operational constraints:
- You may use Pi's read, write, edit, bash, grep, find, and ls tools.
- Use the available Pi tools through Pi's native tool interface. Do not print or
  serialize tool calls as text.
- Your first assistant action after receiving the benchmark issue must inspect
  the repository with an available tool.
- When you need repository information or need to change files, invoke one
  appropriate tool rather than describing the action in prose.
- A plain-text plan before the first tool invocation is invalid. If you are
  about to explain what you will inspect, invoke the inspection tool instead.
- Do not ask the user for clarification during benchmark execution.
- Do not exfiltrate secrets or print environment variables containing API keys.
- Keep a concise final message summarizing changed files and verification commands.
"""


PI_TASK_PREFIX = """Use Pi's available tools to inspect the repository, make a targeted source-code fix, and run a relevant verification command when practical. Your first assistant response must invoke an available Pi tool through the native tool interface; do not answer with a plan, repository summary, or serialized tool-call text.

BENCHMARK ISSUE:
"""


PI_TASK_PREFIX_WITH_SKILLS = """Use Pi's available tools to inspect the repository, treat skill memory and task evidence as evidence-gated weak hints, make a targeted source-code fix, and run a relevant verification command when practical. Your first assistant response must invoke an available Pi tool through the native tool interface; do not answer with a plan, repository summary, or serialized tool-call text.

BENCHMARK ISSUE:
"""


PI_TERMINAL_BENCH_SYSTEM_PROMPT = """You are Pi, a terminal-based agent running inside a Harbor Terminal-Bench 2.0 task sandbox.

Goal:
- Solve the benchmark task in the current terminal environment.
- Inspect the provided files, scripts, services, and system state before acting.
- Make the necessary persistent changes in the sandbox so the verifier can check them after you exit.
- Run relevant checks when practical.
- This is not a repository research task. Never answer with an overview,
  research summary, README summary, or implementation plan as the final result.
- Continue with concrete tool calls until you have attempted the task.

Operational constraints:
- You may use Pi's read, write, edit, bash, grep, find, and ls tools.
- Use the available Pi tools through Pi's native tool interface. Do not print or
  serialize tool calls as text.
- Your first assistant action after receiving the benchmark task must inspect
  the environment with an available tool.
- When you need task information or need to change files, invoke one appropriate
  tool rather than describing the action in prose.
- A plain-text plan before the first tool invocation is invalid. If you are
  about to explain what you will inspect, invoke the inspection tool instead.
- Do not ask the user for clarification during benchmark execution.
- Do not exfiltrate secrets or print environment variables containing API keys.
- Keep a concise final message summarizing changed files, commands, or checks.
"""


PI_TERMINAL_BENCH_TASK_PREFIX = """Use Pi's available tools to inspect the task environment, solve the Terminal-Bench task, and run a relevant verification command when practical. Your first assistant response must invoke an available Pi tool through the native tool interface; do not answer with a plan, repository summary, or serialized tool-call text.

TERMINAL-BENCH TASK:
"""


PI_TERMINAL_BENCH_TASK_PREFIX_WITH_SKILLS = """Use Pi's available tools to inspect the task environment, treat skill memory as evidence-gated weak hints, solve the Terminal-Bench task, and run a relevant verification command when practical. Your first assistant response must invoke an available Pi tool through the native tool interface; do not answer with a plan, repository summary, or serialized tool-call text.

TERMINAL-BENCH TASK:
"""


PI_SWEBENCH_NO_DIFF_RESCUE_PROMPT = """HARNESS CORRECTIVE PROMPT:
Your previous attempt completed but did not change the repository diff, so it would fail this benchmark. Continue from scratch if needed, but do not conclude that the issue is already fixed or that no change is needed. Passing existing tests without a source-code diff is not sufficient. Inspect the relevant source, make a targeted source-code patch, and run a relevant verification command when practical. This is no-diff rescue attempt $HARBOR_NO_DIFF_RESCUE_ATTEMPT of $HARBOR_NO_DIFF_RESCUE_MAX."""


PI_TERMINAL_BENCH_NO_DIFF_RESCUE_PROMPT = """HARNESS CORRECTIVE PROMPT:
Your previous attempt completed but did not change the git repository diff. If this task requires repository edits, that would fail this benchmark. Continue from the original task, inspect the relevant files or state, make the needed persistent changes, and run a relevant verification command when practical. If the task is not a git-backed code task, focus on the required terminal-side outcome. This is no-diff rescue attempt $HARBOR_NO_DIFF_RESCUE_ATTEMPT of $HARBOR_NO_DIFF_RESCUE_MAX."""


def _normalize_benchmark_name(value: str | None) -> str:
    text = str(value or "").strip().lower().replace("_", "-")
    if not text:
        return "swe-bench"
    aliases = {
        "swebench": "swe-bench",
        "swebench-verified": "swe-bench",
        "swe-bench-verified": "swe-bench",
        "swegym": "swe-gym",
        "swe-gym": "swe-gym",
        "swe-gym-train": "swe-gym",
        "terminalbench": "terminal-bench",
        "terminal-bench2": "terminal-bench",
        "terminal-bench-2": "terminal-bench",
        "terminalbench2": "terminal-bench",
        "tb2": "terminal-bench",
        "tbench": "terminal-bench",
    }
    return aliases.get(text, text)


def _benchmark_system_prompt(benchmark_name: str | None) -> str:
    if _normalize_benchmark_name(benchmark_name) == "terminal-bench":
        return PI_TERMINAL_BENCH_SYSTEM_PROMPT
    return PI_SYSTEM_PROMPT


def _benchmark_task_prefix(benchmark_name: str | None, *, use_skills: bool) -> str:
    if _normalize_benchmark_name(benchmark_name) == "terminal-bench":
        return (
            PI_TERMINAL_BENCH_TASK_PREFIX_WITH_SKILLS
            if use_skills
            else PI_TERMINAL_BENCH_TASK_PREFIX
        )
    return PI_TASK_PREFIX_WITH_SKILLS if use_skills else PI_TASK_PREFIX


def _benchmark_sharegpt_source(benchmark_name: str | None) -> str:
    normalized = _normalize_benchmark_name(benchmark_name)
    if normalized == "terminal-bench":
        return "harbor-terminal-bench-2"
    if normalized == "swe-gym":
        return "harbor-swegym"
    return "harbor-swebench-verified"


def _benchmark_no_diff_rescue_prompt(benchmark_name: str | None) -> str:
    if _normalize_benchmark_name(benchmark_name) == "terminal-bench":
        return PI_TERMINAL_BENCH_NO_DIFF_RESCUE_PROMPT
    return PI_SWEBENCH_NO_DIFF_RESCUE_PROMPT


def _unquote_yaml_scalar(value: str) -> str:
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        value = value[1:-1]
    return value.strip()


def _env_bool(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _parse_bool_metadata(value: str, default: bool = True) -> bool:
    text = str(value or "").strip().lower()
    if not text:
        return default
    return text in {"1", "true", "yes", "on"}


def _parse_list_metadata(value: str) -> list[str]:
    text = str(value or "").strip()
    if not text:
        return []
    try:
        parsed = json.loads(text)
    except ValueError:
        return [item.strip().strip("'\"") for item in text.strip("[]").split(",") if item.strip()]
    if isinstance(parsed, list):
        return [str(item) for item in parsed]
    return [str(parsed)]


def _parse_skill_metadata(text: str) -> dict[str, Any]:
    metadata: dict[str, Any] = {}
    in_frontmatter = text.startswith("---")
    lines = text.splitlines()
    iterable = lines[1:] if in_frontmatter else lines
    for raw_line in iterable[:180]:
        line = raw_line.strip()
        if in_frontmatter and line == "---":
            break
        if not line or line.startswith("#"):
            continue
        if line.startswith("- ") or ":" not in line:
            continue
        key, value = line.split(":", 1)
        key = key.strip()
        if key in {"name", "description", "quality_tier", "use_policy"} and value.strip():
            metadata[key] = _unquote_yaml_scalar(value)
        elif key == "active":
            metadata[key] = _parse_bool_metadata(value, default=True)
        elif key == "quality_score":
            try:
                metadata[key] = float(_unquote_yaml_scalar(value))
            except ValueError:
                metadata[key] = 0.0
        elif key == "risk_flags":
            metadata[key] = _parse_list_metadata(value)
    return metadata


def _skill_is_active(metadata: dict[str, Any]) -> bool:
    name = str(metadata.get("name") or "")
    if name in PI_GENERIC_FALLBACK_SKILL_NAMES:
        return False
    if metadata.get("active") is False:
        return False
    quality = metadata.get("quality_score")
    if isinstance(quality, (int, float)) and quality < PI_MIN_ACTIVE_SKILL_QUALITY:
        return False
    risks = {str(item) for item in (metadata.get("risk_flags") or [])}
    if risks & PI_BLOCKED_SKILL_RISKS:
        return False
    use_policy = str(metadata.get("use_policy") or "").strip().lower()
    if use_policy in {"memory-only", "disabled", "inactive"}:
        return False
    return True


def _active_task_skills_root() -> Path:
    roots = _active_task_skill_roots()
    return roots[0] if roots else PI_TASK_SKILLS_BASE


def _active_task_skill_roots() -> list[Path]:
    override = os.getenv("PI_SKILL_PACK_ROOT") or os.getenv("PI_TASK_STAGE_SKILLS_ROOT")
    memory = load_memory(memory_path_from_env())
    version = active_version(memory)
    version_id = version.get("version_id") if version else None
    if override:
        root = Path(override).expanduser()
    elif isinstance(version_id, str) and version_id:
        root = PI_TASK_SKILLS_BASE / version_id
    else:
        root = PI_TASK_SKILLS_BASE

    roots = [root]
    versions = memory.get("versions") if isinstance(memory, dict) else None
    if not isinstance(versions, dict) or not isinstance(version_id, str):
        return roots

    archive_root = root.parent if root.name == version_id else PI_TASK_SKILLS_BASE
    seen_roots = {root.resolve()}
    seen_versions: set[str] = set()
    current_id = version_id
    while current_id and current_id not in seen_versions:
        seen_versions.add(current_id)
        current = versions.get(current_id)
        if not isinstance(current, dict):
            break
        parent = current.get("parent_version")
        if not parent:
            break
        parent_id = str(parent)
        parent_root = archive_root / parent_id
        resolved_parent = parent_root.resolve()
        if resolved_parent not in seen_roots:
            roots.append(parent_root)
            seen_roots.add(resolved_parent)
        current_id = parent_id
    return roots


def _discover_pi_skills(skills_root: Path) -> list[dict[str, str]]:
    if not skills_root.exists():
        return []

    skills: list[dict[str, str]] = []
    for skill_md in sorted(skills_root.rglob("SKILL.md")):
        relative_dir = skill_md.parent.relative_to(skills_root)
        if relative_dir.parts and relative_dir.parts[0] in PI_SKILL_PACK_EXCLUDE_DIRS:
            continue

        metadata = _parse_skill_metadata(skill_md.read_text(errors="replace"))
        if not _skill_is_active(metadata):
            continue
        name = metadata.get("name") or relative_dir.name
        relative_path = relative_dir / "SKILL.md"
        sandbox_path = PI_SANDBOX_SKILLS_DIR / relative_path.as_posix()
        skills.append(
            {
                "name": name,
                "description": metadata.get("description", ""),
                "quality_score": metadata.get("quality_score"),
                "quality_tier": metadata.get("quality_tier", ""),
                "risk_flags": metadata.get("risk_flags", []),
                "use_policy": metadata.get("use_policy", "evidence-gated"),
                "relative_path": relative_path.as_posix(),
                "path": sandbox_path.as_posix(),
                "_root": str(skills_root),
            }
        )
    return skills


def _discover_pi_skills_from_roots(skills_roots: list[Path]) -> list[dict[str, str]]:
    skills: list[dict[str, str]] = []
    seen_relative_paths: set[str] = set()
    for skills_root in skills_roots:
        for skill in _discover_pi_skills(skills_root):
            relative_path = str(skill.get("relative_path") or "")
            if not relative_path or relative_path in seen_relative_paths:
                continue
            seen_relative_paths.add(relative_path)
            skills.append(skill)
    return skills


def _task_slug_from_instruction(instruction: str) -> str:
    patterns = (
        r"swe-bench/([A-Za-z0-9_.-]+__[A-Za-z0-9_.-]+-\d+)(?=__|$|[^A-Za-z0-9_.-])",
        r"(?<![A-Za-z0-9_.-])([A-Za-z0-9_.-]+__[A-Za-z0-9_.-]+-\d+)(?=__|$|[^A-Za-z0-9_.-])",
    )
    for pattern in patterns:
        match = re.search(pattern, instruction)
        if match:
            return match.group(1)
    return ""


def _repo_slug_from_instruction(instruction: str) -> str:
    task_slug = _task_slug_from_instruction(instruction)
    if "__" not in task_slug:
        return ""
    org, rest = task_slug.split("__", 1)
    repo = rest.rsplit("-", 1)[0]
    if repo:
        return f"{org}__{repo}"
    return org


def _repo_org_from_slug(repo_slug: str) -> str:
    if "__" in repo_slug:
        return repo_slug.split("__", 1)[0]
    return repo_slug


def _skill_retrieval_scopes() -> set[str]:
    raw = os.getenv("PI_SKILL_RETRIEVAL_SCOPE", "task")
    scopes = {
        item.strip().lower()
        for item in re.split(r"[,:\s]+", raw)
        if item.strip()
    }
    if "transfer" in scopes:
        scopes.update({"general", "failure"})
    if "all" in scopes:
        scopes.update({"general", "failure", "repo", "task"})
    return scopes or {"task"}


def _allow_exact_task_memory() -> bool:
    scopes = _skill_retrieval_scopes()
    return "task" in scopes


def _skill_scope_rank(skill: dict[str, str], task_slug: str, repo_slug: str) -> int | None:
    relative_path = str(skill.get("relative_path") or "")
    normalized = relative_path.strip("/")
    scopes = _skill_retrieval_scopes()

    if "task" in scopes and task_slug and task_slug in normalized:
        return 0

    if "repo" in scopes and repo_slug:
        repo_org = _repo_org_from_slug(repo_slug)
        repo_prefixes = (
            f"_repos/{repo_slug}/",
            f"repos/{repo_slug}/",
            f"_repos/{repo_org}/",
            f"repos/{repo_org}/",
        )
        if normalized.startswith(repo_prefixes):
            return 1

    if "failure" in scopes and normalized.startswith(("_failure_modes/", "failure_modes/")):
        return 2

    if "general" in scopes and normalized.startswith(("_general/", "general/")):
        return 3
    return None


def _filter_task_specific_skills(
    skills: list[dict[str, str]],
    instruction: str,
) -> list[dict[str, str]]:
    if not skills:
        return []
    task_slug = _task_slug_from_instruction(instruction)
    repo_slug = _repo_slug_from_instruction(instruction)

    ranked: list[tuple[int, str, dict[str, str]]] = []
    for skill in skills:
        rank = _skill_scope_rank(skill, task_slug, repo_slug)
        if rank is None:
            continue
        ranked.append((rank, str(skill.get("relative_path") or ""), skill))
    ranked.sort(key=lambda item: (item[0], item[1]))
    return [skill for _, _, skill in ranked[:PI_MAX_PROMPT_SKILLS]]


def _iter_pi_skill_pack_files(skills_root: Path) -> list[Path]:
    if not skills_root.exists():
        return []

    files: list[Path] = []
    for path in sorted(skills_root.rglob("*")):
        if path.is_symlink() or not path.is_file():
            continue
        relative_path = path.relative_to(skills_root)
        if relative_path.parts and relative_path.parts[0] in PI_SKILL_PACK_EXCLUDE_DIRS:
            continue
        if any(part.startswith(".") or part == "__pycache__" for part in relative_path.parts):
            continue
        if path.suffix in {".pyc", ".pyo"}:
            continue
        files.append(path)
    return files


def _iter_pi_skill_pack_files_for_skills(
    skills: list[dict[str, str]],
) -> list[tuple[Path, Path]]:
    if not skills:
        return []

    selected_dirs: list[tuple[Path, Path]] = []
    for skill in skills:
        relative_path = skill.get("relative_path")
        root = skill.get("_root")
        if not relative_path or not root:
            continue
        selected_dirs.append((Path(root), Path(relative_path).parent))

    files: list[tuple[Path, Path]] = []
    seen_relative_paths: set[str] = set()
    for skills_root, selected_dir in selected_dirs:
        for path in _iter_pi_skill_pack_files(skills_root):
            relative_path = path.relative_to(skills_root)
            if not (
                relative_path == selected_dir
                or relative_path.is_relative_to(selected_dir)
            ):
                continue
            relative_key = relative_path.as_posix()
            if relative_key in seen_relative_paths:
                continue
            seen_relative_paths.add(relative_key)
            files.append((path, relative_path))
    return files


def _pi_skill_pack(instruction: str) -> tuple[list[dict[str, str]], str, str, int, dict[str, str]]:
    skills_roots = _active_task_skill_roots()
    all_skills = _discover_pi_skills_from_roots(skills_roots)
    skills = _filter_task_specific_skills(all_skills, instruction)
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as archive:
        for path, relative_path in _iter_pi_skill_pack_files_for_skills(skills):
            info = archive.gettarinfo(str(path), arcname=relative_path.as_posix())
            info.uid = 0
            info.gid = 0
            info.uname = ""
            info.gname = ""
            info.mtime = 0
            with path.open("rb") as handle:
                archive.addfile(info, handle)
    encoded = base64.b64encode(buffer.getvalue()).decode("ascii")
    wrapped = "\n".join(textwrap.wrap(encoded, width=76))
    task_filter = {
        "task_slug": _task_slug_from_instruction(instruction),
        "repo_slug": _repo_slug_from_instruction(instruction),
        "max_prompt_skills": str(PI_MAX_PROMPT_SKILLS),
    }
    return (
        skills,
        wrapped,
        os.pathsep.join(str(root) for root in skills_roots),
        len(all_skills),
        task_filter,
    )


def _task_filter_text(instruction: str, environment: BaseEnvironment) -> str:
    parts = [instruction]
    for attr in ("environment_name", "session_id"):
        value = getattr(environment, attr, "")
        if value:
            parts.append(str(value))
    return "\n".join(parts)


def _pi_skills_prompt(skills: list[dict[str, str]]) -> str:
    if not skills:
        return ""

    lines = [
        "",
        "Skill pack:",
        f"- Read-only skill files are available under {PI_SANDBOX_SKILLS_DIR}.",
        f"- The skill index is saved at {PI_SKILLS_INDEX_PATH}.",
        "- These skills can include task-stage, repo-level, failure-mode, or general SWE process skills depending on the configured retrieval scope.",
        "- First inspect the current repository evidence. Read a listed SKILL.md only if that first inspection matches its applicability, owner path, error signal, or validation command.",
        "- You may read zero skills. If no listed skill matches concrete repository evidence, continue with the normal no-skill workflow.",
        "- If a skill's first concrete check does not match the current repository, ignore that skill and do not force its patch shape.",
        "- Read at most two skill files before the first edit; prefer the highest-quality evidence-gated skill over multiple generic hints.",
        "- Load referenced scripts, assets, or reference files only when they are directly useful.",
        "",
        "Available skills:",
    ]
    for skill in skills:
        quality = skill.get("quality_score")
        quality_text = f", quality={quality:.2f}" if isinstance(quality, (int, float)) else ""
        lines.append(f"- {skill['name']}: {skill['path']} ({skill.get('use_policy', 'evidence-gated')}{quality_text})")
    return "\n".join(lines)


def _pi_skills_metadata(skills: list[dict[str, str]]) -> list[dict[str, str]]:
    return [
        {
            "name": skill["name"],
            "relative_path": skill["relative_path"],
            "path": skill["path"],
            "quality_score": skill.get("quality_score"),
            "quality_tier": skill.get("quality_tier"),
            "risk_flags": skill.get("risk_flags", []),
            "use_policy": skill.get("use_policy"),
        }
        for skill in skills
    ]


def _pi_system_prompt(
    skills: list[dict[str, str]],
    memory_prompt: str = "",
    base_prompt: str | None = None,
) -> str:
    return (
        (base_prompt or PI_SYSTEM_PROMPT).rstrip()
        + "\n"
        + _pi_skills_prompt(skills)
        + memory_prompt
        + "\n"
    )


def _pi_skills_index_text(skills: list[dict[str, str]]) -> str:
    return json.dumps(
        {
            "skills_dir": PI_SANDBOX_SKILLS_DIR.as_posix(),
            "excluded": sorted(PI_SKILL_PACK_EXCLUDE_DIRS),
            "skills": skills,
        },
        ensure_ascii=False,
        indent=2,
    )


def _pi_skills_setup_commands(skills: list[dict[str, str]], skill_pack_b64: str) -> list[str]:
    skills_index = _pi_skills_index_text(skills)
    if not skill_pack_b64:
        return [
            f"rm -rf {shlex.quote(str(PI_SANDBOX_SKILLS_DIR))}\n"
            f"mkdir -p {shlex.quote(str(PI_SANDBOX_SKILLS_DIR))}\n"
            f"cat > {shlex.quote(str(PI_SKILLS_INDEX_PATH))} <<'HARBOR_PI_SKILLS_INDEX_EOF'\n"
            f"{skills_index}\n"
            "HARBOR_PI_SKILLS_INDEX_EOF\n"
        ]

    commands = [
        f"rm -rf {shlex.quote(str(PI_SANDBOX_SKILLS_DIR))} "
        f"{shlex.quote(str(PI_SKILL_PACK_B64_PATH))} "
        f"{shlex.quote(str(PI_SKILL_PACK_TAR_PATH))}\n"
        f"mkdir -p {shlex.quote(str(PI_SANDBOX_SKILLS_DIR))}\n"
        f": > {shlex.quote(str(PI_SKILL_PACK_B64_PATH))}\n"
    ]
    for index, start in enumerate(range(0, len(skill_pack_b64), PI_SKILL_PACK_CHUNK_SIZE), start=1):
        chunk = skill_pack_b64[start : start + PI_SKILL_PACK_CHUNK_SIZE]
        commands.append(
            f"cat >> {shlex.quote(str(PI_SKILL_PACK_B64_PATH))} <<'HARBOR_PI_SKILLS_PACK_{index}_EOF'\n"
            f"{chunk}\n"
            f"HARBOR_PI_SKILLS_PACK_{index}_EOF\n"
        )
    commands.append(
        "python - <<'HARBOR_PI_SKILLS_UNPACK'\n"
        "import base64\n"
        "import os\n"
        "import shutil\n"
        "import tarfile\n"
        f"skills_dir = {json.dumps(str(PI_SANDBOX_SKILLS_DIR))}\n"
        f"b64_path = {json.dumps(str(PI_SKILL_PACK_B64_PATH))}\n"
        f"tar_path = {json.dumps(str(PI_SKILL_PACK_TAR_PATH))}\n"
        "shutil.rmtree(skills_dir, ignore_errors=True)\n"
        "if not os.path.isdir(skills_dir):\n"
        "    os.makedirs(skills_dir)\n"
        "with open(b64_path, 'rb') as src:\n"
        "    packed = base64.b64decode(src.read())\n"
        "with open(tar_path, 'wb') as dst:\n"
        "    dst.write(packed)\n"
        "with tarfile.open(tar_path, 'r:gz') as archive:\n"
        "    for member in archive.getmembers():\n"
        "        name = member.name\n"
        "        parts = name.split('/')\n"
        "        if (not member.isfile()) or name.startswith('/') or '..' in parts:\n"
        "            raise RuntimeError('unsafe skill pack path: ' + name)\n"
        "    archive.extractall(skills_dir)\n"
        "for path in (tar_path, b64_path):\n"
        "    try:\n"
        "        os.remove(path)\n"
        "    except OSError:\n"
        "        pass\n"
        "HARBOR_PI_SKILLS_UNPACK\n"
        f"cat > {shlex.quote(str(PI_SKILLS_INDEX_PATH))} <<'HARBOR_PI_SKILLS_INDEX_EOF'\n"
        f"{skills_index}\n"
        "HARBOR_PI_SKILLS_INDEX_EOF\n"
    )
    return commands


def _pi_no_skills_cleanup_command() -> str:
    return (
        f"rm -rf {shlex.quote(str(PI_SANDBOX_SKILLS_DIR))} "
        f"{shlex.quote(str(PI_SKILL_PACK_B64_PATH))} "
        f"{shlex.quote(str(PI_SKILL_PACK_TAR_PATH))} "
        f"{shlex.quote(str(PI_SKILLS_INDEX_PATH))}\n"
    )


def _json_default(value: Any) -> str:
    return str(value)


def _compact(value: Any, limit: int = 6000) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        text = value
    else:
        text = json.dumps(value, ensure_ascii=False, default=_json_default)
    if len(text) <= limit:
        return text
    return text[:limit] + "\n... [truncated]"


def _numeric_value(value: Any) -> int | float:
    if value is None:
        return 0
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, (int, float)):
        return value
    if isinstance(value, str):
        try:
            return float(value)
        except ValueError:
            return 0
    return 0


def _int_value(*values: Any) -> int:
    for value in values:
        numeric = _numeric_value(value)
        if numeric:
            return int(numeric)
    return 0


def _cost_value(value: Any) -> float:
    if isinstance(value, dict):
        return float(_numeric_value(value.get("total")))
    return float(_numeric_value(value))


def _message_text(message: dict[str, Any] | None) -> str:
    if not isinstance(message, dict):
        return ""
    content = message.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict):
                text = item.get("text") or item.get("content")
                if text is not None:
                    parts.append(str(text))
        return "".join(parts)
    text = message.get("text")
    return str(text) if text is not None else ""


def _sanitize_assistant_content_text(text: str) -> str:
    if not text:
        return text
    text = re.sub(r"(?is)<think>\s*.*?</think>\s*", "", text)
    text = re.sub(r"(?i)</?think>", "", text)
    return text


def _sanitize_message_content(value: Any) -> Any:
    if isinstance(value, str):
        return _sanitize_assistant_content_text(value)
    if isinstance(value, list):
        sanitized_items: list[Any] = []
        for item in value:
            if isinstance(item, str):
                sanitized_items.append(_sanitize_assistant_content_text(item))
            elif isinstance(item, dict):
                sanitized_item = dict(item)
                for key in ("text", "content", "output", "value"):
                    if isinstance(sanitized_item.get(key), str):
                        sanitized_item[key] = _sanitize_assistant_content_text(
                            sanitized_item[key]
                        )
                sanitized_items.append(sanitized_item)
            else:
                sanitized_items.append(item)
        return sanitized_items
    return value


def _forbidden_assistant_content_event_hits(
    events: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    hits: list[dict[str, Any]] = []
    for index, event in enumerate(events):
        if event.get("type") != "message_end":
            continue
        message = event.get("message")
        if not isinstance(message, dict) or message.get("role") != "assistant":
            continue
        text = _message_text(message)
        markers = [
            marker
            for marker in FORBIDDEN_ASSISTANT_CONTENT_MARKERS
            if marker in text
        ]
        if markers:
            hits.append({"event_index": index, "markers": markers})
    return hits


def _safe_shell_json(value: dict[str, Any]) -> str:
    return shlex.quote(json.dumps(value, ensure_ascii=False, indent=2))


def _local_script_file(content: str, *, prefix: str) -> Path:
    handle = tempfile.NamedTemporaryFile(
        "w",
        encoding="utf-8",
        prefix=prefix,
        suffix=".sh",
        delete=False,
    )
    with handle:
        handle.write(content)
        if not content.endswith("\n"):
            handle.write("\n")
    return Path(handle.name)


def _reasoning_proxy_base_url(base_url: str) -> str:
    match = re.match(
        r"^[a-zA-Z][a-zA-Z0-9+.-]*://[^/]+(?P<path>/.*)?$",
        base_url.rstrip(),
    )
    path = (match.group("path") if match else "") or ""
    return f"http://127.0.0.1:{PI_REASONING_PROXY_PORT}{path.rstrip('/')}"


def _reasoning_effort_none_proxy_command(base_url: str, api_key_env: str) -> str:
    proxy_script = r'''
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


HOST = "127.0.0.1"
PORT = int(os.environ.get("HARBOR_REASONING_PROXY_PORT", "18080"))
UPSTREAM_BASE = os.environ["HARBOR_REASONING_UPSTREAM_BASE"].rstrip("/")
UPSTREAM_API_KEY = os.environ.get("HARBOR_REASONING_UPSTREAM_API_KEY", "")
ATTRIBUTION_HEADER = os.environ.get("CLAUDE_CODE_ATTRIBUTION_HEADER", "0")
SEND_ATTRIBUTION_HEADER = os.environ.get("HARBOR_REASONING_PROXY_SEND_ATTRIBUTION", "") == "1"
UPSTREAM_PATH_PREFIX = urllib.parse.urlsplit(UPSTREAM_BASE).path.rstrip("/")
HOP_BY_HOP_HEADERS = {
    "accept-encoding",
    "connection",
    "content-length",
    "host",
    "keep-alive",
    "proxy-authenticate",
    "proxy-authorization",
    "te",
    "trailer",
    "transfer-encoding",
    "upgrade",
}


def upstream_url(request_path: str) -> str:
    parsed = urllib.parse.urlsplit(request_path)
    path = parsed.path or "/"
    if UPSTREAM_PATH_PREFIX and (
        path == UPSTREAM_PATH_PREFIX or path.startswith(UPSTREAM_PATH_PREFIX + "/")
    ):
        path = path[len(UPSTREAM_PATH_PREFIX):] or "/"
    url = UPSTREAM_BASE + path
    if parsed.query:
        url += "?" + parsed.query
    return url


def rewrite_body(path: str, body: bytes, content_type: str):
    if not path.endswith("/chat/completions"):
        return body, False
    if "application/json" not in content_type.lower():
        return body, False
    try:
        payload = json.loads(body.decode("utf-8"))
    except Exception:
        return body, False
    if isinstance(payload, dict):
        payload["reasoning_effort"] = "none"
        payload["enable_thinking"] = False
        body = json.dumps(
            payload, ensure_ascii=False, separators=(",", ":")
        ).encode("utf-8")
        return body, True
    return body, False


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.0"

    def log_message(self, fmt, *args):
        sys.stderr.write("[harbor-reasoning-proxy] " + (fmt % args) + "\n")

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "authorization,content-type")
        self.send_header("Access-Control-Allow-Methods", "GET,POST,OPTIONS")
        self.end_headers()

    def do_GET(self):
        self._proxy()

    def do_POST(self):
        self._proxy()

    def _proxy(self):
        body = b""
        injected_reasoning_effort = False
        if self.command in {"POST", "PUT", "PATCH"}:
            try:
                length = int(self.headers.get("content-length") or "0")
            except ValueError:
                length = 0
            body = self.rfile.read(length) if length else b""
            body, injected_reasoning_effort = rewrite_body(
                urllib.parse.urlsplit(self.path).path,
                body,
                self.headers.get("content-type", ""),
            )

        headers = {
            key: value
            for key, value in self.headers.items()
            if key.lower() not in HOP_BY_HOP_HEADERS
        }
        if UPSTREAM_API_KEY:
            headers["Authorization"] = "Bearer " + UPSTREAM_API_KEY
        if SEND_ATTRIBUTION_HEADER:
            headers["CLAUDE_CODE_ATTRIBUTION_HEADER"] = ATTRIBUTION_HEADER
        if body:
            headers["Content-Length"] = str(len(body))

        request = urllib.request.Request(
            upstream_url(self.path),
            data=body if self.command in {"POST", "PUT", "PATCH"} else None,
            headers=headers,
            method=self.command,
        )
        sys.stderr.write(
            "[harbor-reasoning-proxy] "
            f"{self.command} {urllib.parse.urlsplit(self.path).path} "
            f"reasoning_effort_none={str(injected_reasoning_effort).lower()}\n"
        )
        try:
            with urllib.request.urlopen(request, timeout=900) as response:
                self.send_response(response.status)
                content_type = response.headers.get("content-type", "")
                for key, value in response.headers.items():
                    if key.lower() in HOP_BY_HOP_HEADERS:
                        continue
                    self.send_header(key, value)
                self.end_headers()
                if "text/event-stream" in content_type.lower():
                    while True:
                        line = response.readline()
                        if not line:
                            break
                        self.wfile.write(line)
                        self.wfile.flush()
                else:
                    while True:
                        chunk = response.read(65536)
                        if not chunk:
                            break
                        self.wfile.write(chunk)
                        self.wfile.flush()
        except urllib.error.HTTPError as exc:
            payload = exc.read()
            self.send_response(exc.code)
            for key, value in exc.headers.items():
                if key.lower() in HOP_BY_HOP_HEADERS:
                    continue
                self.send_header(key, value)
            self.end_headers()
            self.wfile.write(payload)
        except Exception as exc:
            payload = json.dumps({"error": str(exc)}, ensure_ascii=False).encode("utf-8")
            self.send_response(502)
            self.send_header("content-type", "application/json")
            self.send_header("content-length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)


server = ThreadingHTTPServer((HOST, PORT), Handler)
sys.stderr.write(
    f"[harbor-reasoning-proxy] listening on {HOST}:{PORT}, upstream={UPSTREAM_BASE}\n"
)
server.serve_forever()
'''.strip()
    proxy_log = "/tmp/harbor-openai-reasoning-proxy.log"
    send_attribution = "1" if is_macaron_base_url(base_url) else "0"
    parts = [
        "cat > /tmp/harbor-openai-reasoning-proxy.py <<'HARBOR_REASONING_PROXY_PY'\n"
        f"{proxy_script}\n"
        "HARBOR_REASONING_PROXY_PY\n",
        f"export HARBOR_REASONING_UPSTREAM_BASE={shlex.quote(base_url.rstrip('/'))}\n",
        f"export HARBOR_REASONING_UPSTREAM_API_KEY=\"${{{api_key_env}}}\"\n",
        f"export HARBOR_REASONING_PROXY_PORT={PI_REASONING_PROXY_PORT}\n",
        f"export HARBOR_REASONING_PROXY_SEND_ATTRIBUTION={send_attribution}\n",
    ]
    if is_macaron_base_url(base_url):
        parts.append(
            f"export {MACARON_ATTRIBUTION_HEADER_ENV}="
            f"{shlex.quote(MACARON_ATTRIBUTION_HEADER_VALUE)}\n"
        )
    parts.extend(
        [
        'if [ -z "${HARBOR_PYTHON_BIN:-}" ]; then\n'
        "  echo '[harbor] python3/python unavailable; reasoning proxy cannot start' >&2\n"
        "  exit 98\n"
        "fi\n",
        '"$HARBOR_PYTHON_BIN" /tmp/harbor-openai-reasoning-proxy.py '
        f"> {proxy_log} 2>&1 &\n",
        "HARBOR_REASONING_PROXY_PID=$!\n",
        "export HARBOR_REASONING_PROXY_PID\n",
        "trap 'cp "
        f"{proxy_log} "
        "/logs/agent/openai-reasoning-proxy.log 2>/dev/null || true; "
        "kill \"${HARBOR_REASONING_PROXY_PID:-}\" 2>/dev/null || true' EXIT\n",
        "HARBOR_REASONING_PROXY_READY=0\n",
        "for HARBOR_REASONING_PROXY_WAIT in $(seq 1 100); do\n",
        "  if \"$HARBOR_PYTHON_BIN\" - <<'HARBOR_REASONING_PROXY_READY_PY'\n",
        "import os, socket\n",
        "with socket.create_connection(('127.0.0.1', int(os.environ['HARBOR_REASONING_PROXY_PORT'])), timeout=0.2):\n",
        "    pass\n",
        "HARBOR_REASONING_PROXY_READY_PY\n",
        "  then HARBOR_REASONING_PROXY_READY=1; break; fi\n",
        "  if ! kill -0 \"$HARBOR_REASONING_PROXY_PID\" 2>/dev/null; then\n",
        f"    cat {proxy_log} >&2 || true\n",
        "    exit 98\n",
        "  fi\n",
        "  sleep 0.1\n",
        "done\n",
        "if [ \"$HARBOR_REASONING_PROXY_READY\" != \"1\" ]; then\n",
        f"  cat {proxy_log} >&2 || true\n",
        "  exit 98\n",
        "fi\n",
        ]
    )
    return "".join(parts)


def _sanitize_pi_event(event: dict[str, Any]) -> dict[str, Any]:
    """Drop high-volume transient fields from Pi JSONL without changing semantics.

    The raw Pi event stream contains `message_update/thinking_delta` events where the
    `partial` field repeats the full accumulated thinking text on every delta. This
    can inflate a single trace from KBs to hundreds of MBs and stall downstream log
    collection. We keep the event type plus the incremental delta and discard only
    redundant transient payload.
    """

    event_type = event.get("type")
    if event_type == "message_end":
        message = event.get("message")
        if isinstance(message, dict) and message.get("role") == "assistant":
            sanitized = dict(event)
            sanitized_message = dict(message)
            if "content" in sanitized_message:
                sanitized_message["content"] = _sanitize_message_content(
                    sanitized_message["content"]
                )
            if isinstance(sanitized_message.get("text"), str):
                sanitized_message["text"] = _sanitize_assistant_content_text(
                    sanitized_message["text"]
                )
            sanitized["message"] = sanitized_message
            return sanitized

    if event_type != "message_update":
        return event

    assistant_message_event = event.get("assistantMessageEvent")
    if not isinstance(assistant_message_event, dict):
        return event

    subtype = assistant_message_event.get("type")
    if subtype not in {"thinking_delta", "output_text_delta"}:
        return event

    sanitized = dict(event)
    sanitized_ame = dict(assistant_message_event)
    sanitized_ame.pop("partial", None)
    usage = sanitized_ame.get("usage")
    if isinstance(usage, dict):
        usage = dict(usage)
        if not any(
            _numeric_value(usage.get(key))
            for key in (
                "prompt_tokens",
                "completion_tokens",
                "total_tokens",
                "input_tokens",
                "output_tokens",
                "input",
                "output",
                "totalTokens",
            )
        ):
            sanitized_ame.pop("usage", None)
    sanitized["assistantMessageEvent"] = sanitized_ame
    return sanitized


def _sanitize_pi_jsonl_command(jsonl_path: PurePosixPath) -> str:
    python_path = json.dumps(str(jsonl_path))
    sanitized_markers_json = json.dumps(SANITIZED_ASSISTANT_CONTENT_MARKERS)
    return f"""${{HARBOR_PYTHON_BIN:-python3}} - <<'HARBOR_PI_SANITIZE_JSONL'
import json
import os
import re
import tempfile

path = {python_path}
sanitized_markers = tuple({sanitized_markers_json})
if not os.path.exists(path):
    raise SystemExit(0)

def sanitize_assistant_text(text):
    if not isinstance(text, str) or not text:
        return text
    text = re.sub(r"(?is)<think>\\s*.*?</think>\\s*", "", text)
    text = re.sub(r"(?i)</?think>", "", text)
    return text

def sanitize_content(value):
    if isinstance(value, str):
        return sanitize_assistant_text(value)
    if isinstance(value, list):
        out = []
        for item in value:
            if isinstance(item, str):
                out.append(sanitize_assistant_text(item))
            elif isinstance(item, dict):
                clean = dict(item)
                for key in ("text", "content", "output", "value"):
                    if isinstance(clean.get(key), str):
                        clean[key] = sanitize_assistant_text(clean[key])
                out.append(clean)
            else:
                out.append(item)
        return out
    return value

temp_dir = os.path.dirname(path) or "."
fd, temp_path = tempfile.mkstemp(
    prefix="harbor_pi_events_",
    suffix=".jsonl",
    dir=temp_dir,
)
os.close(fd)

try:
    with open(path, "rb") as src, open(temp_path, "w", encoding="utf-8") as dst:
        for raw_line in src:
            try:
                line = raw_line.decode("utf-8", "replace").strip()
            except Exception:
                continue
            if not line:
                continue
            try:
                event = json.loads(line)
            except ValueError:
                dst.write(line + "\\n")
                continue

            if (
                event.get("type") == "message_end"
                and isinstance(event.get("message"), dict)
                and event["message"].get("role") == "assistant"
            ):
                message = dict(event["message"])
                content = message.get("content")
                text = message.get("text")
                if any(marker in str(content) or marker in str(text) for marker in sanitized_markers):
                    if "content" in message:
                        message["content"] = sanitize_content(message["content"])
                    if isinstance(message.get("text"), str):
                        message["text"] = sanitize_assistant_text(message["text"])
                    event = dict(event)
                    event["message"] = message

            if (
                event.get("type") == "message_update"
                and isinstance(event.get("assistantMessageEvent"), dict)
                and event["assistantMessageEvent"].get("type") in {{"thinking_delta", "output_text_delta"}}
            ):
                ame = dict(event["assistantMessageEvent"])
                ame.pop("partial", None)
                usage = ame.get("usage")
                if isinstance(usage, dict):
                    if not any(
                        float(usage.get(key) or 0)
                        for key in (
                            "prompt_tokens",
                            "completion_tokens",
                            "total_tokens",
                            "input_tokens",
                            "output_tokens",
                            "input",
                            "output",
                            "totalTokens",
                        )
                    ):
                        ame.pop("usage", None)
                event = dict(event)
                event["assistantMessageEvent"] = ame

            dst.write(json.dumps(event, ensure_ascii=False) + "\\n")
    os.replace(temp_path, path)
finally:
    if os.path.exists(temp_path):
        try:
            os.remove(temp_path)
        except OSError:
            pass
HARBOR_PI_SANITIZE_JSONL
"""


def _no_tool_call_guard_command(jsonl_path: PurePosixPath) -> str:
    python_path = json.dumps(str(jsonl_path))
    return f"""${{HARBOR_PYTHON_BIN:-python3}} - <<'HARBOR_MACARON_EMPTY_RESPONSE_GUARD'
import json
import sys

path = {python_path}
tool_calls = 0
assistant_messages = []
try:
    handle = open(path, "rb")
except IOError:
    handle = None

if handle is not None:
    try:
        for line in handle:
            if not isinstance(line, str):
                line = line.decode("utf-8", "replace")
            line = line.strip()
            if not line:
                continue
            try:
                event = json.loads(line)
            except ValueError:
                pass
            else:
                if event.get("type") == "tool_execution_start":
                    tool_calls += 1
                elif (
                    event.get("type") == "message_end"
                    and isinstance(event.get("message"), dict)
                    and event["message"].get("role") == "assistant"
                ):
                    assistant_messages.append(event.get("message") or {{}})
    finally:
        handle.close()

def message_text(message):
    content = message.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for item in content:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict):
                text = item.get("text") or item.get("content")
                if text is not None:
                    parts.append(str(text))
        return "".join(parts)
    return str(message.get("text") or "")

assistant_errors = []
for message in assistant_messages:
    error = message.get("errorMessage") or message.get("error")
    if message.get("stopReason") == "error" or error:
        if isinstance(error, dict):
            error = json.dumps(error, ensure_ascii=False)
        assistant_errors.append(str(error or "unknown provider error"))

nonempty_assistant = sum(1 for message in assistant_messages if message_text(message).strip())
total_tokens = 0
for message in assistant_messages:
    usage = message.get("usage") or {{}}
    for key in ("total_tokens", "totalTokens", "total"):
        value = usage.get(key)
        if isinstance(value, (int, float)):
            total_tokens += value
        elif isinstance(value, str):
            try:
                total_tokens += float(value)
            except ValueError:
                pass

if assistant_messages and tool_calls == 0:
    if assistant_errors:
        reason = "hit provider error before any tool call: " + "; ".join(assistant_errors[:3])
    elif nonempty_assistant == 0 and total_tokens == 0:
        reason = "returned an empty assistant response with zero token usage"
    else:
        reason = "finished without issuing any Pi tool calls"
    sys.stderr.write(
        "Pi agent {{0}}; failing instead of silently verifying an "
        "unchanged workspace.\\n".format(reason)
    )
    sys.exit(86)
HARBOR_MACARON_EMPTY_RESPONSE_GUARD
"""


def _forbidden_assistant_content_guard_command(jsonl_path: PurePosixPath) -> str:
    python_path = json.dumps(str(jsonl_path))
    markers_json = json.dumps(FORBIDDEN_ASSISTANT_CONTENT_MARKERS)
    return f"""${{HARBOR_PYTHON_BIN:-python3}} - <<'HARBOR_MACARON_ASSISTANT_CONTENT_GUARD'
import json
import sys

path = {python_path}
markers = tuple({markers_json})
bad = []
try:
    handle = open(path, "rb")
except IOError:
    handle = None

def message_text(message):
    content = message.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for item in content:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict):
                text = item.get("text") or item.get("content") or item.get("output")
                if text is not None:
                    parts.append(str(text))
        return "".join(parts)
    return str(message.get("text") or message.get("value") or "")

if handle is not None:
    try:
        for line_number, line in enumerate(handle, 1):
            if not isinstance(line, str):
                line = line.decode("utf-8", "replace")
            line = line.strip()
            if not line:
                continue
            try:
                event = json.loads(line)
            except ValueError:
                continue
            if event.get("type") != "message_end":
                continue
            message = event.get("message")
            if not isinstance(message, dict) or message.get("role") != "assistant":
                continue
            text = message_text(message)
            hits = [marker for marker in markers if marker in text]
            if hits:
                bad.append((line_number, hits))
    finally:
        handle.close()

if bad:
    previews = []
    for line_number, hits in bad[:5]:
        previews.append("line %s: %s" % (line_number, ", ".join(hits)))
    sys.stderr.write(
        "Pi agent emitted forbidden assistant.content markers; rejecting trace: "
        + "; ".join(previews)
        + "\\n"
    )
    sys.exit(87)
HARBOR_MACARON_ASSISTANT_CONTENT_GUARD
"""


def _workspace_fingerprint_command() -> str:
    return """HARBOR_WORKSPACE_DIFF_BEFORE=""
if command -v git >/dev/null 2>&1 && git rev-parse --is-inside-work-tree >/dev/null 2>&1; then
  HARBOR_WORKSPACE_DIFF_BEFORE="$(mktemp /tmp/harbor_workspace_before.XXXXXX)"
  { git diff --binary; git diff --cached --binary; } > "$HARBOR_WORKSPACE_DIFF_BEFORE"
fi
"""


def _workspace_change_guard_command() -> str:
    return """if command -v git >/dev/null 2>&1 && git rev-parse --is-inside-work-tree >/dev/null 2>&1; then
  if [ -n "${HARBOR_WORKSPACE_DIFF_BEFORE:-}" ] && [ -f "$HARBOR_WORKSPACE_DIFF_BEFORE" ]; then
    HARBOR_WORKSPACE_DIFF_AFTER="$(mktemp /tmp/harbor_workspace_after.XXXXXX)"
    { git diff --binary; git diff --cached --binary; } > "$HARBOR_WORKSPACE_DIFF_AFTER"
    if cmp -s "$HARBOR_WORKSPACE_DIFF_BEFORE" "$HARBOR_WORKSPACE_DIFF_AFTER"; then
      echo "Pi agent finished without changing the repository diff; failing instead of verifying an unchanged workspace." >&2
      exit 87
    fi
  fi
fi
"""


def _workspace_change_check_function_command() -> str:
    return """harbor_workspace_has_changed() {
  if ! command -v git >/dev/null 2>&1 || ! git rev-parse --is-inside-work-tree >/dev/null 2>&1; then
    return 0
  fi
  if [ -z "${HARBOR_WORKSPACE_DIFF_BEFORE:-}" ] || [ ! -f "$HARBOR_WORKSPACE_DIFF_BEFORE" ]; then
    return 0
  fi
  HARBOR_WORKSPACE_DIFF_AFTER="$(mktemp /tmp/harbor_workspace_after.XXXXXX)"
  { git diff --binary; git diff --cached --binary; } > "$HARBOR_WORKSPACE_DIFF_AFTER"
  if cmp -s "$HARBOR_WORKSPACE_DIFF_BEFORE" "$HARBOR_WORKSPACE_DIFF_AFTER"; then
    return 1
  fi
  return 0
}
"""


def _no_diff_metadata_update_function_command(metadata_path: PurePosixPath) -> str:
    python_path = json.dumps(str(metadata_path))
    return f"""harbor_update_no_diff_metadata() {{
  HARBOR_NO_DIFF_RESCUED_VALUE="${{1:-false}}" \\
  HARBOR_NO_DIFF_ATTEMPTS_VALUE="${{2:-0}}" \\
  HARBOR_NO_DIFF_FAILED_VALUE="${{3:-false}}" \\
  HARBOR_NO_DIFF_MAX_ATTEMPTS_VALUE="${{HARBOR_NO_DIFF_RESCUE_MAX:-2}}" \\
  ${{HARBOR_PYTHON_BIN:-python3}} - <<'HARBOR_NO_DIFF_METADATA'
import json
import os
from pathlib import Path

path = Path({python_path})
try:
    data = json.loads(path.read_text()) if path.exists() else {{}}
except Exception:
    data = {{}}

def as_bool(value):
    return str(value).strip().lower() in {{"1", "true", "yes", "on"}}

def as_int(value, default=0):
    try:
        return int(str(value).strip())
    except Exception:
        return default

attempts = as_int(os.environ.get("HARBOR_NO_DIFF_ATTEMPTS_VALUE"), 0)
max_attempts = as_int(os.environ.get("HARBOR_NO_DIFF_MAX_ATTEMPTS_VALUE"), 2)
rescued = as_bool(os.environ.get("HARBOR_NO_DIFF_RESCUED_VALUE"))
failed = as_bool(os.environ.get("HARBOR_NO_DIFF_FAILED_VALUE"))

data["no_diff_rescue_enabled"] = True
data["no_diff_rescue_max_attempts"] = max_attempts
data["no_diff_rescue_attempts"] = attempts
data["no_diff_rescued"] = rescued
data["no_diff_rescue_failed"] = failed

path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\\n")
HARBOR_NO_DIFF_METADATA
}}
"""


def _pi_retry_wrapper_command(
    jsonl_path: PurePosixPath,
    stderr_path: PurePosixPath,
    pi_command: str,
) -> str:
    script = f"""harbor_run_pi_with_provider_retry() {{
set +e
harbor_pi_provider_error_seen() {{
  ${{HARBOR_PYTHON_BIN:-python3}} - "$1" "$2" <<'HARBOR_PI_PROVIDER_ERROR_CHECK'
import json
import re
import sys

jsonl_path, stderr_path = sys.argv[1:3]
patterns = re.compile(
    r"Connection error|provider error|401 status code|429|rate limit|too many requests|"
    r"Response 5[0-9][0-9]|status code 5[0-9][0-9]|\\b50[0-9]\\b|timeout|"
    r"timed out|ECONNRESET|connection reset|temporarily unavailable|upstream",
    re.I,
)

try:
    with open(jsonl_path, "rb") as handle:
        for raw_line in handle:
            try:
                event = json.loads(raw_line.decode("utf-8", "replace"))
            except Exception:
                continue
            message = event.get("message")
            if not isinstance(message, dict) or message.get("role") != "assistant":
                continue
            error = message.get("errorMessage") or message.get("error")
            if message.get("stopReason") == "error":
                raise SystemExit(0)
            if error and patterns.search(json.dumps(error, ensure_ascii=False)):
                raise SystemExit(0)
except FileNotFoundError:
    pass

try:
    with open(stderr_path, "r", encoding="utf-8", errors="replace") as handle:
        stderr_text = handle.read()
except FileNotFoundError:
    stderr_text = ""
if patterns.search(stderr_text):
    raise SystemExit(0)
raise SystemExit(1)
HARBOR_PI_PROVIDER_ERROR_CHECK
}}
HARBOR_PI_ATTEMPTS="${{HARBOR_PI_ATTEMPTS:-8}}"
HARBOR_PI_RETRY_DELAY="${{HARBOR_PI_RETRY_DELAY:-45}}"
HARBOR_PI_STATUS=1
HARBOR_PI_ATTEMPT=1
while [ "$HARBOR_PI_ATTEMPT" -le "$HARBOR_PI_ATTEMPTS" ]; do
  if [ "$HARBOR_PI_ATTEMPT" -gt 1 ]; then
    echo "[harbor] retrying Pi after transient provider error attempt $HARBOR_PI_ATTEMPT/$HARBOR_PI_ATTEMPTS" >&2
    sleep "$HARBOR_PI_RETRY_DELAY"
  fi
  : > {shlex.quote(str(jsonl_path))}
  : > {shlex.quote(str(stderr_path))}
  {pi_command}
  HARBOR_PI_STATUS=$?
  if [ "$HARBOR_PI_STATUS" -eq 0 ] && harbor_pi_provider_error_seen {shlex.quote(str(jsonl_path))} {shlex.quote(str(stderr_path))}; then
    HARBOR_PI_STATUS=86
  fi
  if [ "$HARBOR_PI_STATUS" -eq 0 ]; then
    break
  fi
  if ! harbor_pi_provider_error_seen {shlex.quote(str(jsonl_path))} {shlex.quote(str(stderr_path))}; then
    break
  fi
  HARBOR_PI_ATTEMPT=$((HARBOR_PI_ATTEMPT + 1))
done
set -e
if [ "$HARBOR_PI_STATUS" -ne 0 ]; then
  return "$HARBOR_PI_STATUS"
fi
return 0
}}
"""
    return script


def _no_diff_rescue_loop_command(
    jsonl_path: PurePosixPath,
    stderr_path: PurePosixPath,
    sanitize_jsonl_command: str,
    forbidden_assistant_content_guard_command: str,
    no_tool_call_guard_command: str,
    rescue_prompt: str,
) -> str:
    jsonl_path_q = shlex.quote(str(jsonl_path))
    stderr_path_q = shlex.quote(str(stderr_path))
    return f"""HARBOR_PI_ORIGINAL_PROMPT="$PROMPT"
HARBOR_NO_DIFF_RESCUE_MAX="${{HARBOR_NO_DIFF_RESCUE_MAX:-2}}"
HARBOR_NO_DIFF_RESCUE_ATTEMPT=0
while true; do
  harbor_run_pi_with_provider_retry || exit "$?"
{sanitize_jsonl_command}
{forbidden_assistant_content_guard_command}
{no_tool_call_guard_command}
  if harbor_workspace_has_changed; then
    if [ "$HARBOR_NO_DIFF_RESCUE_ATTEMPT" -gt 0 ]; then
      harbor_update_no_diff_metadata true "$HARBOR_NO_DIFF_RESCUE_ATTEMPT" false
    else
      harbor_update_no_diff_metadata false 0 false
    fi
    break
  fi
  HARBOR_NO_DIFF_COPY_INDEX=$((HARBOR_NO_DIFF_RESCUE_ATTEMPT + 1))
  cp {jsonl_path_q} "/logs/agent/no-diff-attempt-${{HARBOR_NO_DIFF_COPY_INDEX}}.pi-events.jsonl" 2>/dev/null || true
  cp {stderr_path_q} "/logs/agent/no-diff-attempt-${{HARBOR_NO_DIFF_COPY_INDEX}}.pi-stderr.txt" 2>/dev/null || true
  if [ "$HARBOR_NO_DIFF_RESCUE_ATTEMPT" -ge "$HARBOR_NO_DIFF_RESCUE_MAX" ]; then
    harbor_update_no_diff_metadata false "$HARBOR_NO_DIFF_RESCUE_ATTEMPT" true
    echo "Pi agent finished without changing the repository diff after $HARBOR_NO_DIFF_RESCUE_ATTEMPT no-diff rescue attempt(s); failing instead of verifying an unchanged workspace." >&2
    exit 87
  fi
  HARBOR_NO_DIFF_RESCUE_ATTEMPT=$((HARBOR_NO_DIFF_RESCUE_ATTEMPT + 1))
  echo "[harbor] Pi produced no repository diff; starting no-diff rescue attempt $HARBOR_NO_DIFF_RESCUE_ATTEMPT/$HARBOR_NO_DIFF_RESCUE_MAX" >&2
  PROMPT="${{HARBOR_PI_ORIGINAL_PROMPT}}

{rescue_prompt}"
done
"""


class PiAgent(BaseInstalledAgent):
    """Harbor installed-agent adapter that runs Pi against OpenAI-compatible models."""

    SUPPORTS_ATIF = True

    _JSONL_FILENAME = "pi-events.jsonl"
    _STDERR_FILENAME = "pi-stderr.txt"
    _SHAREGPT_FILENAME = "sharegpt.json"
    _TRAJECTORY_FILENAME = "trajectory.json"
    _METADATA_FILENAME = "pi-metadata.json"
    _MODELS_FILENAME = "models.json"
    _SYSTEM_PROMPT_FILENAME = "pi-system-prompt.md"
    _INSTRUCTION_FILENAME = "problem_statement.md"
    _TRACE_GUARD_FILENAME = "pi-trace-guard.json"

    def __init__(
        self,
        logs_dir: Path,
        provider_name: str = "openai",
        api_key_env: str = "OPENAI_COMPAT_API_KEY",
        base_url_env: str = "OPENAI_COMPAT_BASE_URL",
        model_env: str = "OPENAI_COMPAT_MODEL",
        provider_api: str = "openai-completions",
        model_context_window: int = 128000,
        model_max_tokens: int = 32000,
        thinking: str = "off",
        tools: str = "read,write,edit,bash,grep,find,ls",
        openai_compat: dict[str, Any] | None = None,
        auth_header: bool = True,
        model_reasoning: bool = False,
        default_api_key: str | None = None,
        result_only: bool = False,
        use_skills: bool = False,
        benchmark_name: str = "swe-bench",
        require_workspace_change: bool = True,
        *args: Any,
        **kwargs: Any,
    ):
        super().__init__(logs_dir=logs_dir, *args, **kwargs)
        self.provider_name = provider_name
        self.api_key_env = api_key_env
        self.base_url_env = base_url_env
        self.model_env = model_env
        self.provider_api = provider_api
        self.model_context_window = model_context_window
        self.model_max_tokens = model_max_tokens
        self.thinking = thinking
        self.tools = tools
        self.openai_compat = openai_compat
        self.auth_header = auth_header
        self.model_reasoning = model_reasoning
        self.default_api_key = default_api_key
        self.result_only = result_only
        self.use_skills = use_skills
        self.benchmark_name = _normalize_benchmark_name(benchmark_name)
        self.require_workspace_change = require_workspace_change

    @staticmethod
    def name() -> str:
        return "pi-agent"

    @property
    def _trajectory_path(self) -> PurePosixPath:
        return PurePosixPath(EnvironmentPaths.agent_dir / self._TRAJECTORY_FILENAME)

    def get_version_command(self) -> str | None:
        return PI_RUNTIME_PATH_COMMAND + "; pi --version"

    def parse_version(self, stdout: str | None) -> str:
        if not stdout:
            return ""
        for line in stdout.strip().splitlines():
            if line.strip():
                return line.strip()
        return stdout.strip()

    def _install_system_dependencies_command(self) -> str:
        if self.benchmark_name == "terminal-bench":
            return "true"
        return """
set -euo pipefail
if command -v apt-get >/dev/null 2>&1; then
  export DEBIAN_FRONTEND="${DEBIAN_FRONTEND:-noninteractive}"
  for HARBOR_APT_ATTEMPT in $(seq 1 24); do
    if apt-get -o DPkg::Lock::Timeout=300 update \
      && apt-get -o DPkg::Lock::Timeout=300 install -y curl ca-certificates git jq ripgrep; then
      exit 0
    fi
    HARBOR_APT_STATUS=$?
    if [ "$HARBOR_APT_ATTEMPT" -ge 24 ]; then
      exit "$HARBOR_APT_STATUS"
    fi
    echo "[harbor] apt-get install failed or dpkg lock is busy; retrying $HARBOR_APT_ATTEMPT/24" >&2
    sleep 5
  done
elif command -v apk >/dev/null 2>&1; then
  apk add --no-cache curl ca-certificates git jq ripgrep nodejs npm bash
elif command -v yum >/dev/null 2>&1; then
  yum install -y curl ca-certificates git jq ripgrep
fi
""".strip()

    def _install_node_and_pi_command(self, version_spec: str) -> str:
        return (
            "set -euo pipefail; "
            f"{PI_RUNTIME_PATH_COMMAND}; "
            "NODE_MAJOR=$(node -p 'process.versions.node.split(\".\")[0]' 2>/dev/null || echo 0); "
            "if ! command -v node >/dev/null 2>&1 || ! command -v npm >/dev/null 2>&1 || [ \"$NODE_MAJOR\" -lt 22 ]; then "
            "  NODE_VERSION=${HARBOR_PI_NODE_VERSION:-22.19.0}; "
            "  NODE_ARCH=$(uname -m); "
            "  case \"$NODE_ARCH\" in "
            "    x86_64|amd64) NODE_ARCH=x64 ;; "
            "    aarch64|arm64) NODE_ARCH=arm64 ;; "
            "    *) echo \"unsupported node arch: $NODE_ARCH\" >&2; exit 1 ;; "
            "  esac; "
            "  NODE_DIR=\"$HOME/.local/node-v$NODE_VERSION-linux-$NODE_ARCH\"; "
            "  if [ ! -x \"$NODE_DIR/bin/node\" ]; then "
            "    mkdir -p \"$HOME/.local\"; "
            "    NODE_TGZ=\"/tmp/node-v$NODE_VERSION-linux-$NODE_ARCH.tar.gz\"; "
            "    NODE_URL=\"https://nodejs.org/dist/v$NODE_VERSION/node-v$NODE_VERSION-linux-$NODE_ARCH.tar.gz\"; "
            "    if command -v curl >/dev/null 2>&1; then "
            "      curl -fsSL \"$NODE_URL\" -o \"$NODE_TGZ\"; "
            "    else "
            "      NODE_URL=\"$NODE_URL\" NODE_TGZ=\"$NODE_TGZ\" python3 - <<'HARBOR_PI_DOWNLOAD_NODE'\n"
            "import os\n"
            "import urllib.request\n"
            "urllib.request.urlretrieve(os.environ['NODE_URL'], os.environ['NODE_TGZ'])\n"
            "HARBOR_PI_DOWNLOAD_NODE\n"
            "    fi; "
            "    rm -rf \"$NODE_DIR\"; "
            "    NODE_TGZ=\"$NODE_TGZ\" NODE_HOME=\"$HOME/.local\" python3 - <<'HARBOR_PI_EXTRACT_NODE'\n"
            "import os\n"
            "import tarfile\n"
            "with tarfile.open(os.environ['NODE_TGZ'], 'r:gz') as archive:\n"
            "    archive.extractall(os.environ['NODE_HOME'])\n"
            "HARBOR_PI_EXTRACT_NODE\n"
            "    rm -f \"$NODE_TGZ\"; "
            "  fi; "
            "  export PATH=\"$NODE_DIR/bin:$PATH\"; "
            "fi; "
            "npm install -g @earendil-works/pi-coding-agent"
            f"{version_spec}; "
            "pi --version"
        )

    async def install(self, environment: BaseEnvironment) -> None:
        pi_check = await environment.exec(
            command=(
                PI_RUNTIME_PATH_COMMAND
                + "; "
                "command -v pi >/dev/null 2>&1 && pi --version"
            )
        )
        if pi_check.return_code == 0:
            parsed_version = self.parse_version(pi_check.stdout)
            if parsed_version:
                self._version = parsed_version
                await self.exec_as_root(
                    environment,
                    command=(
                        "set -e; "
                        "for bin in node npm npx pi; do "
                        '  BIN_PATH="$(command -v "$bin" 2>/dev/null || true)"; '
                        '  if [ -n "$BIN_PATH" ] && [ "$BIN_PATH" != "/usr/local/bin/$bin" ]; then '
                        '    ln -sf "$BIN_PATH" "/usr/local/bin/$bin"; '
                        "  fi; "
                        "done"
                    ),
                )
                return

        await self.exec_as_root(
            environment,
            command=self._install_system_dependencies_command(),
            env={"DEBIAN_FRONTEND": "noninteractive"},
        )

        version_spec = f"@{self._version}" if self._version else "@latest"
        await self.exec_as_agent(
            environment,
            command=self._install_node_and_pi_command(version_spec),
        )

        await self.exec_as_root(
            environment,
            command=(
                "set -e; "
                "for bin in node npm npx pi; do "
                '  BIN_PATH="$(command -v "$bin" 2>/dev/null || true)"; '
                '  if [ -n "$BIN_PATH" ] && [ "$BIN_PATH" != "/usr/local/bin/$bin" ]; then '
                '    ln -sf "$BIN_PATH" "/usr/local/bin/$bin"; '
                "  fi; "
                "done"
            ),
        )

    def _required_env(self) -> dict[str, str]:
        required = [self.base_url_env, self.model_env]
        if self.default_api_key is None:
            required.insert(0, self.api_key_env)
        missing = [name for name in required if not os.environ.get(name)]
        if missing:
            raise ValueError(
                "Missing required environment variables for PiAgent: "
                + ", ".join(missing)
            )
        env = {name: os.environ[name] for name in required}
        env[self.api_key_env] = os.environ.get(self.api_key_env) or self.default_api_key or ""
        ensure_macaron_attribution_header(env[self.base_url_env], env)
        env_prefix = (
            self.model_env[: -len("_MODEL")]
            if self.model_env.endswith("_MODEL")
            else "OPENAI_COMPAT"
        )
        ensure_reasoning_effort_none(
            env[self.base_url_env],
            env,
            env_prefix=env_prefix,
        )
        return env

    def _models_config(self, env: dict[str, str]) -> dict[str, Any]:
        openai_compat: dict[str, Any] = {
            "supportsStore": False,
            "supportsDeveloperRole": False,
            "supportsReasoningEffort": False,
            "supportsUsageInStreaming": False,
            "maxTokensField": "max_tokens",
            "requiresToolResultName": False,
            "requiresAssistantAfterToolResult": False,
            "requiresThinkingAsText": False,
            "requiresReasoningContentOnAssistantMessages": False,
            "supportsStrictMode": False,
            "supportsLongCacheRetention": False,
        }
        if self.openai_compat:
            openai_compat.update(self.openai_compat)
        return {
            "providers": {
                self.provider_name: {
                    "baseUrl": env[self.base_url_env],
                    "api": self.provider_api,
                    "apiKey": self.api_key_env,
                    "authHeader": self.auth_header,
                    "compat": openai_compat,
                    "models": [
                        {
                            "id": env[self.model_env],
                            "name": env[self.model_env],
                            "reasoning": self.model_reasoning,
                            "input": ["text"],
                            "contextWindow": self.model_context_window,
                            "maxTokens": self.model_max_tokens,
                            "cost": {
                                "input": 0,
                                "output": 0,
                                "cacheRead": 0,
                                "cacheWrite": 0,
                            },
                            "compat": openai_compat,
                        }
                    ],
                }
            }
        }

    def _effective_instruction(self, instruction: str, *, use_skills: bool = False) -> str:
        # Keep provider behavior aligned: Pi receives the same task prompt shape
        # for Novita, Tinker, and other OpenAI-compatible backends.
        prefix = _benchmark_task_prefix(self.benchmark_name, use_skills=use_skills)
        return prefix + instruction

    @with_prompt_template
    async def run(
        self,
        instruction: str,
        environment: BaseEnvironment,
        context: AgentContext,
    ) -> None:
        env = self._required_env()
        provider_model = env[self.model_env]
        provider_base_url = env[self.base_url_env]
        use_reasoning_proxy = requires_reasoning_effort_none(provider_base_url)
        effective_provider_base_url = (
            _reasoning_proxy_base_url(provider_base_url)
            if use_reasoning_proxy
            else provider_base_url
        )
        models_env = dict(env)
        models_env[self.base_url_env] = effective_provider_base_url
        model = self.model_name or f"{self.provider_name}/{provider_model}"
        models_config = self._models_config(models_env)
        task_filter_text = _task_filter_text(instruction, environment)
        if self.use_skills:
            (
                pi_skills,
                pi_skill_pack_b64,
                pi_skill_source_root,
                all_pi_skills_count,
                skill_retrieval_filter,
            ) = _pi_skill_pack(task_filter_text)
        else:
            pi_skills = []
            pi_skill_pack_b64 = ""
            pi_skill_source_root = ""
            all_pi_skills_count = 0
            skill_retrieval_filter = {
                "task_slug": "",
                "repo_slug": "",
                "max_prompt_skills": str(PI_MAX_PROMPT_SKILLS),
            }
        has_skill_pack = self.use_skills and bool(pi_skills)
        skill_harness_memory = {
            "enabled": False,
            "reason": "disabled",
            "prompt": "",
            "selected_entries": [],
        }
        if (
            self.use_skills
            and _env_bool("PI_USE_SKILL_HARNESS_MEMORY", True)
            and _allow_exact_task_memory()
        ):
            skill_harness_memory = retrieve_task_memory(task_filter_text)
        effective_use_skills = self.use_skills and (
            has_skill_pack or bool(skill_harness_memory.get("prompt"))
        )
        base_system_prompt = _benchmark_system_prompt(self.benchmark_name)
        pi_system_prompt = _pi_system_prompt(
            pi_skills,
            str(skill_harness_memory.get("prompt") or ""),
            base_prompt=base_system_prompt,
        )
        pi_skills_metadata = _pi_skills_metadata(pi_skills)
        pi_skills_setup_commands = (
            _pi_skills_setup_commands(pi_skills, pi_skill_pack_b64)
            if has_skill_pack
            else []
        )
        effective_instruction = self._effective_instruction(
            instruction,
            use_skills=effective_use_skills,
        )
        metadata = {
            "agent": self.name(),
            "pi_version": self._version,
            "provider": self.provider_name,
            "model": model,
            "provider_model": provider_model,
            "provider_base_url": provider_base_url,
            "effective_provider_base_url": effective_provider_base_url,
            "provider_api": self.provider_api,
            "reasoning_effort_none_proxy": use_reasoning_proxy,
            "enable_thinking_false_proxy": use_reasoning_proxy,
            "macaron_reasoning_proxy": (
                use_reasoning_proxy and is_macaron_base_url(provider_base_url)
            ),
            "benchmark_name": self.benchmark_name,
            "require_workspace_change": self.require_workspace_change,
            "api_key_env": self.api_key_env,
            "base_url_env": self.base_url_env,
            "model_env": self.model_env,
            "auth_header": self.auth_header,
            "model_reasoning": self.model_reasoning,
            "thinking": self.thinking,
            "tools": self.tools,
            "system_prompt": pi_system_prompt,
            "skills_dir": PI_SANDBOX_SKILLS_DIR.as_posix(),
            "skills_index_path": PI_SKILLS_INDEX_PATH.as_posix(),
            "skills_source_root": pi_skill_source_root,
            "use_skills": effective_use_skills,
            "skills_count": len(pi_skills),
            "all_skills_count": all_pi_skills_count,
            "skill_retrieval_filter": skill_retrieval_filter,
            "skills": pi_skills_metadata,
            "skill_harness_memory": {
                key: value
                for key, value in skill_harness_memory.items()
                if key != "prompt"
            },
            "pi_tool_protocol": "native",
        }

        instruction_path = PurePosixPath(
            EnvironmentPaths.agent_dir / self._INSTRUCTION_FILENAME
        )
        system_prompt_path = PurePosixPath(
            EnvironmentPaths.agent_dir / self._SYSTEM_PROMPT_FILENAME
        )
        models_path = PurePosixPath(EnvironmentPaths.agent_dir / self._MODELS_FILENAME)
        metadata_path = PurePosixPath(
            EnvironmentPaths.agent_dir / self._METADATA_FILENAME
        )
        jsonl_path = PurePosixPath(EnvironmentPaths.agent_dir / self._JSONL_FILENAME)
        stderr_path = PurePosixPath(EnvironmentPaths.agent_dir / self._STDERR_FILENAME)
        if self.result_only:
            jsonl_path = PurePosixPath("/tmp/harbor-pi-events.jsonl")
            stderr_path = PurePosixPath("/tmp/harbor-pi-stderr.txt")
        sanitize_jsonl_command = _sanitize_pi_jsonl_command(jsonl_path)
        no_tool_call_guard_command = _no_tool_call_guard_command(jsonl_path)
        workspace_fingerprint_command = _workspace_fingerprint_command()
        workspace_change_check_function_command = (
            _workspace_change_check_function_command()
        )
        no_diff_metadata_update_function_command = (
            _no_diff_metadata_update_function_command(metadata_path)
        )
        reasoning_proxy_command = (
            _reasoning_effort_none_proxy_command(provider_base_url, self.api_key_env)
            if use_reasoning_proxy
            else ""
        )
        cleanup_transient_logs_command = ""
        if self.result_only:
            cleanup_transient_logs_command = (
                f"rm -f {shlex.quote(str(jsonl_path))} {shlex.quote(str(stderr_path))}\n"
            )
        forbidden_assistant_content_guard_command = (
            _forbidden_assistant_content_guard_command(jsonl_path)
        )

        heredoc = "HARBOR_PI_PROMPT_EOF"
        system_heredoc = "HARBOR_PI_SYSTEM_EOF"
        pi_command = (
            "pi --print --no-session --mode json --no-context-files --no-extensions --no-skills "
            f"--tools {shlex.quote(self.tools)} "
            f"--provider {shlex.quote(self.provider_name)} "
            f"--model {shlex.quote(model)} "
            f"--api-key \"${{{self.api_key_env}}}\" "
            f"--thinking {shlex.quote(self.thinking)} "
            "--system-prompt "
            f"{shlex.quote(pi_system_prompt)} "
            '"$PROMPT" '
            f"> {shlex.quote(str(jsonl_path))} "
            f"2> >(tee {shlex.quote(str(stderr_path))} >&2)"
        )
        pi_retry_command = _pi_retry_wrapper_command(
            jsonl_path,
            stderr_path,
            pi_command,
        )
        no_diff_rescue_loop_command = _no_diff_rescue_loop_command(
            jsonl_path,
            stderr_path,
            sanitize_jsonl_command,
            forbidden_assistant_content_guard_command,
            no_tool_call_guard_command,
            _benchmark_no_diff_rescue_prompt(self.benchmark_name),
        )
        run_pi_command = no_diff_rescue_loop_command
        if not self.require_workspace_change:
            run_pi_command = (
                "harbor_run_pi_with_provider_retry || exit \"$?\"\n"
                "if [ -n \"$HARBOR_PYTHON_BIN\" ]; then\n"
                f"{sanitize_jsonl_command}"
                f"{forbidden_assistant_content_guard_command}"
                f"{no_tool_call_guard_command}"
                "  harbor_update_no_diff_metadata false 0 false\n"
                "else\n"
                "  echo '[harbor] python3/python unavailable; skipping Pi trace post-run guards' >&2\n"
                "fi\n"
            )
        command = (
            "set -euo pipefail\n"
            "HARBOR_PYTHON_BIN=\"$(command -v python3 || command -v python || true)\"\n"
            "export HARBOR_PYTHON_BIN\n"
            "mkdir -p /logs/agent ~/.pi/agent\n"
            f"{reasoning_proxy_command}"
            f"{'' if has_skill_pack else _pi_no_skills_cleanup_command()}"
            f"{PI_RUNTIME_PATH_COMMAND}\n"
            f"cat > {shlex.quote(str(instruction_path))} <<'{heredoc}'\n"
            f"{effective_instruction}\n"
            f"{heredoc}\n"
            f"cat > {shlex.quote(str(system_prompt_path))} <<'{system_heredoc}'\n"
            f"{pi_system_prompt}\n"
            f"{system_heredoc}\n"
            f"printf '%s\\n' {_safe_shell_json(models_config)} > ~/.pi/agent/models.json\n"
            f"cp ~/.pi/agent/models.json {shlex.quote(str(models_path))}\n"
            f"printf '%s\\n' {_safe_shell_json(metadata)} > {shlex.quote(str(metadata_path))}\n"
            "export PI_SKIP_VERSION_CHECK=1\n"
            "export PI_TELEMETRY=0\n"
            "PROMPT=$(cat " + shlex.quote(str(instruction_path)) + ")\n"
            f"{workspace_fingerprint_command}"
            f"{workspace_change_check_function_command}"
            f"{no_diff_metadata_update_function_command}"
            f"{pi_retry_command}"
            f"{run_pi_command}"
            f"{cleanup_transient_logs_command}"
        )

        uploaded_scripts: list[Path] = []
        try:
            for index, setup_command in enumerate(pi_skills_setup_commands):
                setup_script = _local_script_file(
                    "set -euo pipefail\nmkdir -p /logs/agent ~/.pi/agent\n"
                    + setup_command,
                    prefix=f"harbor-pi-skills-{index}-",
                )
                uploaded_scripts.append(setup_script)
                sandbox_setup_path = f"/tmp/{setup_script.name}"
                await environment.upload_file(setup_script, sandbox_setup_path)
                await self.exec_as_agent(
                    environment,
                    command=f"bash {shlex.quote(sandbox_setup_path)}",
                    env=env,
                )

            run_script = _local_script_file(command, prefix="harbor-pi-run-")
            uploaded_scripts.append(run_script)
            sandbox_run_path = f"/tmp/{run_script.name}"
            await environment.upload_file(run_script, sandbox_run_path)
            try:
                await self.exec_as_agent(
                    environment,
                    command=f"bash {shlex.quote(sandbox_run_path)}",
                    env=env,
                )
            except NonZeroAgentExitCodeError as exc:
                if "exit 86" in str(exc) or "exit code 86" in str(exc):
                    raise ProviderTransientAgentError(
                        "Pi failed after exhausting transient provider retries"
                    ) from exc
                raise
        finally:
            for script_path in uploaded_scripts:
                try:
                    script_path.unlink()
                except OSError:
                    pass

    def _jsonl_events(self) -> list[dict[str, Any]]:
        path = self.logs_dir / self._JSONL_FILENAME
        events: list[dict[str, Any]] = []
        if not path.exists():
            return events
        for line in path.read_text(errors="replace").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            events.append(_sanitize_pi_event(event))
        return events

    def _metadata(self) -> dict[str, Any]:
        path = self.logs_dir / self._METADATA_FILENAME
        if not path.exists():
            return {}
        try:
            return json.loads(path.read_text())
        except json.JSONDecodeError:
            return {}

    def _instruction_text(self) -> str:
        path = self.logs_dir / self._INSTRUCTION_FILENAME
        if path.exists():
            return path.read_text(errors="replace")
        return ""

    def _extract_usage(self, event: dict[str, Any]) -> dict[str, Any]:
        candidates = [
            event.get("usage"),
            event.get("message", {}).get("usage")
            if isinstance(event.get("message"), dict)
            else None,
            event.get("assistantMessageEvent", {}).get("usage")
            if isinstance(event.get("assistantMessageEvent"), dict)
            else None,
        ]
        usage: dict[str, Any] = {}
        for candidate in candidates:
            if isinstance(candidate, dict):
                usage.update(candidate)
        return usage

    def _convert_to_trajectory(
        self, events: list[dict[str, Any]], metadata: dict[str, Any]
    ) -> Trajectory | None:
        session = next((event for event in events if event.get("type") == "session"), {})
        session_id = str(session.get("id") or "pi-session")
        model_name = str(metadata.get("model") or self.model_name or "")
        version = str(metadata.get("pi_version") or self._version or "unknown")

        steps: list[Step] = []
        step_id = 1
        instruction = self._instruction_text()
        if instruction:
            steps.append(
                Step(
                    step_id=step_id,
                    source="user",
                    message=instruction,
                )
            )
            step_id += 1

        pending_tool_steps: dict[str, Step] = {}
        total_prompt_tokens = 0
        total_completion_tokens = 0
        total_cached_tokens = 0
        total_cost = 0.0

        for event in events:
            event_type = event.get("type")
            usage = self._extract_usage(event)
            total_prompt_tokens += _int_value(
                usage.get("prompt_tokens")
                or usage.get("input_tokens")
                or usage.get("inputTokens")
                or usage.get("input")
                or 0
            )
            total_completion_tokens += _int_value(
                usage.get("completion_tokens")
                or usage.get("output_tokens")
                or usage.get("outputTokens")
                or usage.get("output")
                or 0
            )
            total_cached_tokens += _int_value(
                usage.get("cached_tokens")
                or usage.get("cache_read_input_tokens")
                or usage.get("cachedInputTokens")
                or usage.get("cacheRead")
                or 0
            )
            total_cost += _cost_value(usage.get("cost_usd") or usage.get("cost"))

            if event_type == "message_end":
                message = event.get("message")
                if not isinstance(message, dict):
                    continue
                role = message.get("role")
                if role not in ("assistant", "user", "system"):
                    continue
                text = _message_text(message)
                if not text:
                    continue
                source = "agent" if role == "assistant" else role
                kwargs: dict[str, Any] = {
                    "step_id": step_id,
                    "source": source,
                    "message": text,
                    "timestamp": event.get("timestamp"),
                }
                if source == "agent":
                    kwargs["model_name"] = model_name or None
                steps.append(Step(**kwargs))
                step_id += 1

            elif event_type == "tool_execution_start":
                call_id = str(event.get("toolCallId") or f"tool-{step_id}")
                tool_name = str(event.get("toolName") or "tool")
                args = event.get("args")
                if not isinstance(args, dict):
                    args = {"value": args}
                step = Step(
                    step_id=step_id,
                    source="agent",
                    message=f"Tool call: {tool_name}",
                    model_name=model_name or None,
                    timestamp=event.get("timestamp"),
                    tool_calls=[
                        ToolCall(
                            tool_call_id=call_id,
                            function_name=tool_name,
                            arguments=args,
                        )
                    ],
                )
                steps.append(step)
                pending_tool_steps[call_id] = step
                step_id += 1

            elif event_type == "tool_execution_end":
                call_id = str(event.get("toolCallId") or "")
                step = pending_tool_steps.get(call_id)
                if step is None:
                    continue
                result = event.get("result")
                is_error = bool(event.get("isError"))
                output = _compact(result)
                if is_error:
                    output = "[error]\n" + output
                observation_result = ObservationResult(
                    source_call_id=call_id,
                    content=output,
                )
                if step.observation is None:
                    step.observation = Observation(results=[observation_result])
                else:
                    step.observation.results.append(observation_result)

        if not steps:
            return None

        final_metrics = FinalMetrics(
            total_prompt_tokens=total_prompt_tokens or None,
            total_completion_tokens=total_completion_tokens or None,
            total_cached_tokens=total_cached_tokens or None,
            total_cost_usd=total_cost or None,
            total_steps=len(steps),
            extra={
                "tool_call_rounds": sum(1 for step in steps if step.tool_calls),
                "json_event_count": len(events),
            },
        )

        return Trajectory(
            schema_version="ATIF-v1.6",
            session_id=session_id,
            agent=Agent(
                name=self.name(),
                version=version,
                model_name=model_name or None,
                extra={
                    "provider": metadata.get("provider"),
                    "provider_base_url": metadata.get("provider_base_url"),
                    "provider_model": metadata.get("provider_model"),
                    "thinking": metadata.get("thinking"),
                    "tools": metadata.get("tools"),
                    "system_prompt": metadata.get("system_prompt"),
                },
            ),
            steps=steps,
            final_metrics=final_metrics,
        )

    def _convert_to_sharegpt(
        self,
        events: list[dict[str, Any]],
        metadata: dict[str, Any],
        trajectory: Trajectory | None,
    ) -> dict[str, Any]:
        conversations: list[dict[str, str]] = []
        benchmark_name = str(metadata.get("benchmark_name") or self.benchmark_name)
        system_prompt = str(
            metadata.get("system_prompt") or _benchmark_system_prompt(benchmark_name)
        )
        conversations.append({"from": "system", "value": system_prompt})

        instruction = self._instruction_text()
        if instruction:
            conversations.append({"from": "human", "value": instruction})

        for event in events:
            event_type = event.get("type")
            if event_type == "message_end":
                message = event.get("message")
                if not isinstance(message, dict):
                    continue
                role = message.get("role")
                text = _message_text(message)
                if not text:
                    continue
                if role == "assistant":
                    conversations.append({"from": "gpt", "value": text})
                elif role == "user":
                    conversations.append({"from": "human", "value": text})
                elif role == "system":
                    conversations.append({"from": "system", "value": text})
            elif event_type == "tool_execution_start":
                tool_name = event.get("toolName") or "tool"
                tool_call_id = event.get("toolCallId") or ""
                args = _compact(event.get("args"))
                conversations.append(
                    {
                        "from": "gpt",
                        "value": f"<tool_call name=\"{tool_name}\" id=\"{tool_call_id}\">\n{args}\n</tool_call>",
                    }
                )
            elif event_type == "tool_execution_end":
                tool_name = event.get("toolName") or "tool"
                tool_call_id = event.get("toolCallId") or ""
                result = _compact(event.get("result"))
                if event.get("isError"):
                    result = "[error]\n" + result
                conversations.append(
                    {
                        "from": "tool",
                        "value": f"<tool_result name=\"{tool_name}\" id=\"{tool_call_id}\">\n{result}\n</tool_result>",
                    }
                )

        trace_chars = sum(len(item["value"]) for item in conversations)
        return {
            "id": trajectory.session_id if trajectory else "pi-session",
            "source": _benchmark_sharegpt_source(benchmark_name),
            "model": metadata.get("model") or self.model_name,
            "conversations": conversations,
            "metadata": {
                "agent": self.name(),
                "benchmark_name": _normalize_benchmark_name(benchmark_name),
                "provider": metadata.get("provider"),
                "provider_model": metadata.get("provider_model"),
                "trace_messages": len(conversations),
                "trace_chars": trace_chars,
                "tool_call_rounds": sum(
                    1
                    for event in events
                    if event.get("type") == "tool_execution_start"
                ),
                "json_event_count": len(events),
            },
        }

    def populate_context_post_run(self, context: AgentContext) -> None:
        if self.result_only:
            context.metadata = {"result_only": True}
            return

        events = self._jsonl_events()
        metadata = self._metadata()
        forbidden_hits = _forbidden_assistant_content_event_hits(events)
        trace_guard_metadata: dict[str, Any] = {}
        if forbidden_hits:
            preview = "; ".join(
                f"event {item['event_index']}: {', '.join(item['markers'])}"
                for item in forbidden_hits[:5]
            )
            guard_payload = {
                "pi_trace_rejected": True,
                "reason": "forbidden_assistant_content_markers",
                "preview": preview,
                "hits": forbidden_hits,
            }
            guard_path = self.logs_dir / self._TRACE_GUARD_FILENAME
            guard_path.write_text(json.dumps(guard_payload, ensure_ascii=False, indent=2))
            trace_guard_metadata = {
                "pi_trace_rejected": True,
                "pi_trace_reject_reason": "forbidden_assistant_content_markers",
                "pi_trace_reject_preview": preview,
                "pi_trace_guard_path": str(guard_path),
                "pi_forbidden_assistant_content_hit_count": len(forbidden_hits),
            }
            context.metadata = {
                **trace_guard_metadata,
                "pi_system_prompt": metadata.get("system_prompt")
                or _benchmark_system_prompt(
                    metadata.get("benchmark_name") or self.benchmark_name
                ),
                "benchmark_name": metadata.get("benchmark_name") or self.benchmark_name,
                "pi_json_event_count": len(events),
            }
            return
        trajectory = self._convert_to_trajectory(events, metadata)

        if trajectory is not None:
            trajectory_path = self.logs_dir / self._TRAJECTORY_FILENAME
            trajectory_path.write_text(format_trajectory_json(trajectory.to_json_dict()))
            if trajectory.final_metrics:
                context.n_input_tokens = trajectory.final_metrics.total_prompt_tokens
                context.n_cache_tokens = trajectory.final_metrics.total_cached_tokens
                context.n_output_tokens = (
                    trajectory.final_metrics.total_completion_tokens
                )
                context.cost_usd = trajectory.final_metrics.total_cost_usd

        sharegpt = self._convert_to_sharegpt(events, metadata, trajectory)
        (self.logs_dir / self._SHAREGPT_FILENAME).write_text(
            json.dumps(sharegpt, ensure_ascii=False, indent=2)
        )

        context.metadata = {
            "sharegpt_path": str(self.logs_dir / self._SHAREGPT_FILENAME),
            "trajectory_path": str(self.logs_dir / self._TRAJECTORY_FILENAME),
            "pi_system_prompt": metadata.get("system_prompt")
            or _benchmark_system_prompt(metadata.get("benchmark_name") or self.benchmark_name),
            "benchmark_name": metadata.get("benchmark_name") or self.benchmark_name,
            "pi_tool_call_rounds": sharegpt["metadata"]["tool_call_rounds"],
            "pi_json_event_count": len(events),
            "sharegpt_trace_messages": sharegpt["metadata"]["trace_messages"],
            "sharegpt_trace_chars": sharegpt["metadata"]["trace_chars"],
            **trace_guard_metadata,
        }
