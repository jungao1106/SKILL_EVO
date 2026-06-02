import base64
import io
import json
import os
import shlex
import tarfile
import textwrap
from functools import lru_cache
from pathlib import Path, PurePosixPath
from typing import Any

from harbor.agents.installed.base import BaseInstalledAgent, with_prompt_template
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


ROOT = Path(__file__).resolve().parents[1]
PI_TASK_SKILLS_BASE = ROOT / "skills" / "tasks"
PI_SANDBOX_SKILLS_DIR = PurePosixPath("/tmp/pi-skills")
PI_SKILLS_INDEX_PATH = PurePosixPath(EnvironmentPaths.agent_dir / "pi-skills-index.json")
PI_SKILL_PACK_B64_PATH = PurePosixPath("/tmp/harbor-pi-skills.tar.gz.b64")
PI_SKILL_PACK_TAR_PATH = PurePosixPath("/tmp/harbor-pi-skills.tar.gz")
PI_SKILL_PACK_EXCLUDE_DIRS = {"benchmark-sharded-concurrency"}
PI_SKILL_PACK_CHUNK_SIZE = 24_000
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


PI_TASK_PREFIX_WITH_SKILLS = """Use Pi's available tools to inspect the repository, use task/stage skill memory as weak control hints, read one to three relevant task skill SKILL.md files if the system prompt lists a task skill pack, make a targeted source-code fix, and run a relevant verification command when practical. Your first assistant response must invoke an available Pi tool through the native tool interface; do not answer with a plan, repository summary, or serialized tool-call text.

BENCHMARK ISSUE:
"""


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


def _parse_skill_metadata(text: str) -> dict[str, str]:
    metadata: dict[str, str] = {}
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
        if key in {"name", "description"} and value.strip():
            metadata[key] = _unquote_yaml_scalar(value)
        if "name" in metadata and "description" in metadata:
            break
    return metadata


def _active_task_skills_root() -> Path:
    override = os.getenv("PI_TASK_STAGE_SKILLS_ROOT")
    if override:
        return Path(override).expanduser()
    version = active_version(load_memory(memory_path_from_env()))
    version_id = version.get("version_id") if version else None
    if isinstance(version_id, str) and version_id:
        return PI_TASK_SKILLS_BASE / version_id
    return PI_TASK_SKILLS_BASE


def _discover_pi_skills(skills_root: Path) -> list[dict[str, str]]:
    if not skills_root.exists():
        return []

    skills: list[dict[str, str]] = []
    for skill_md in sorted(skills_root.rglob("SKILL.md")):
        relative_dir = skill_md.parent.relative_to(skills_root)
        if relative_dir.parts and relative_dir.parts[0] in PI_SKILL_PACK_EXCLUDE_DIRS:
            continue

        metadata = _parse_skill_metadata(skill_md.read_text(errors="replace"))
        name = metadata.get("name") or relative_dir.name
        relative_path = relative_dir / "SKILL.md"
        sandbox_path = PI_SANDBOX_SKILLS_DIR / relative_path.as_posix()
        skills.append(
            {
                "name": name,
                "description": metadata.get("description", ""),
                "relative_path": relative_path.as_posix(),
                "path": sandbox_path.as_posix(),
            }
        )
    return skills


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


@lru_cache(maxsize=1)
def _pi_skill_pack() -> tuple[list[dict[str, str]], str, str]:
    skills_root = _active_task_skills_root()
    skills = _discover_pi_skills(skills_root)
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as archive:
        for path in _iter_pi_skill_pack_files(skills_root):
            relative_path = path.relative_to(skills_root)
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
    return skills, wrapped, str(skills_root)


def _pi_skills_prompt(skills: list[dict[str, str]]) -> str:
    if not skills:
        return ""

    lines = [
        "",
        "Task/stage skill pack:",
        f"- Read-only task-specific stage skill files are available under {PI_SANDBOX_SKILLS_DIR}.",
        f"- The skill index is saved at {PI_SKILLS_INDEX_PATH}.",
        "- These are previous task skills from evolution memory, not a global skill catalog.",
        "- After your first repository inspection and before your first source edit, read at least one and at most three listed task skill SKILL.md files with tool calls when they match the current stage.",
        "- Choose a compact set of the most relevant task/stage skills for the repository and issue.",
        "- After reading the relevant SKILL.md file(s), continue with the repository fix; do not spend extra turns reading unrelated skills.",
        "- Load referenced scripts, assets, or reference files only when they are directly useful.",
        "",
        "Available task/stage skills:",
    ]
    for skill in skills:
        lines.append(f"- {skill['name']}: {skill['path']}")
    return "\n".join(lines)


def _pi_skills_metadata(skills: list[dict[str, str]]) -> list[dict[str, str]]:
    return [
        {
            "name": skill["name"],
            "relative_path": skill["relative_path"],
            "path": skill["path"],
        }
        for skill in skills
    ]


def _pi_system_prompt(skills: list[dict[str, str]], memory_prompt: str = "") -> str:
    return (
        PI_SYSTEM_PROMPT.rstrip()
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


def _sanitize_pi_event(event: dict[str, Any]) -> dict[str, Any]:
    """Drop high-volume transient fields from Pi JSONL without changing semantics.

    The raw Pi event stream contains `message_update/thinking_delta` events where the
    `partial` field repeats the full accumulated thinking text on every delta. This
    can inflate a single trace from KBs to hundreds of MBs and stall downstream log
    collection. We keep the event type plus the incremental delta and discard only
    redundant transient payload.
    """

    event_type = event.get("type")
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
    return f"""python - <<'HARBOR_PI_SANITIZE_JSONL'
import json
import os
import tempfile

path = {python_path}
if not os.path.exists(path):
    raise SystemExit(0)

fd, temp_path = tempfile.mkstemp(prefix="harbor_pi_events_", suffix=".jsonl")
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
    return f"""python - <<'HARBOR_MACARON_EMPTY_RESPONSE_GUARD'
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
    return f"""python - <<'HARBOR_MACARON_ASSISTANT_CONTENT_GUARD'
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
  python - <<'HARBOR_NO_DIFF_METADATA'
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
HARBOR_PI_ATTEMPTS="${{HARBOR_PI_ATTEMPTS:-5}}"
HARBOR_PI_RETRY_DELAY="${{HARBOR_PI_RETRY_DELAY:-20}}"
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
  if [ "$HARBOR_PI_STATUS" -eq 0 ]; then
    break
  fi
  if ! grep -Eiq '429|rate limit|too many requests|Response 5[0-9][0-9]|status code 5[0-9][0-9]|\\b50[0-9]\\b|timeout|timed out|ECONNRESET|connection reset|temporarily unavailable|upstream' {shlex.quote(str(jsonl_path))} {shlex.quote(str(stderr_path))} 2>/dev/null; then
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

HARNESS CORRECTIVE PROMPT:
Your previous attempt completed but did not change the repository diff, so it would fail this benchmark. Continue from scratch if needed, but do not conclude that the issue is already fixed or that no change is needed. Passing existing tests without a source-code diff is not sufficient. Inspect the relevant source, make a targeted source-code patch, and run a relevant verification command when practical. This is no-diff rescue attempt $HARBOR_NO_DIFF_RESCUE_ATTEMPT of $HARBOR_NO_DIFF_RESCUE_MAX."
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

    @staticmethod
    def name() -> str:
        return "pi-agent"

    @property
    def _trajectory_path(self) -> PurePosixPath:
        return PurePosixPath(EnvironmentPaths.agent_dir / self._TRAJECTORY_FILENAME)

    def get_version_command(self) -> str | None:
        return "if [ -s ~/.nvm/nvm.sh ]; then . ~/.nvm/nvm.sh; fi; pi --version"

    def parse_version(self, stdout: str) -> str:
        for line in stdout.strip().splitlines():
            if line.strip():
                return line.strip()
        return stdout.strip()

    async def install(self, environment: BaseEnvironment) -> None:
        pi_check = await environment.exec(
            command=(
                "if [ -s ~/.nvm/nvm.sh ]; then . ~/.nvm/nvm.sh; fi; "
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
            command=(
                "if command -v apt-get >/dev/null 2>&1; then "
                "apt-get update && apt-get install -y curl ca-certificates git jq ripgrep; "
                "elif command -v apk >/dev/null 2>&1; then "
                "apk add --no-cache curl ca-certificates git jq ripgrep nodejs npm bash; "
                "elif command -v yum >/dev/null 2>&1; then "
                "yum install -y curl ca-certificates git jq ripgrep; "
                "fi"
            ),
            env={"DEBIAN_FRONTEND": "noninteractive"},
        )

        version_spec = f"@{self._version}" if self._version else "@latest"
        await self.exec_as_agent(
            environment,
            command=(
                "set -euo pipefail; "
                "if ! command -v node >/dev/null 2>&1 || ! command -v npm >/dev/null 2>&1; then "
                "  curl -o- https://raw.githubusercontent.com/nvm-sh/nvm/v0.40.2/install.sh | bash; "
                '  export NVM_DIR="$HOME/.nvm"; '
                '  . "$NVM_DIR/nvm.sh"; '
                "  nvm install 22; "
                "  nvm alias default 22; "
                "fi; "
                "if [ -s ~/.nvm/nvm.sh ]; then . ~/.nvm/nvm.sh; fi; "
                f"npm install -g @earendil-works/pi-coding-agent{version_spec}; "
                "pi --version"
            ),
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
        prefix = PI_TASK_PREFIX_WITH_SKILLS if use_skills else PI_TASK_PREFIX
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
        model = self.model_name or f"{self.provider_name}/{provider_model}"
        models_config = self._models_config(env)
        if self.use_skills:
            pi_skills, pi_skill_pack_b64, pi_skill_source_root = _pi_skill_pack()
        else:
            pi_skills, pi_skill_pack_b64, pi_skill_source_root = [], "", ""
        has_skill_pack = self.use_skills and bool(pi_skills)
        skill_harness_memory = {
            "enabled": False,
            "reason": "disabled",
            "prompt": "",
            "selected_entries": [],
        }
        if self.use_skills and _env_bool("PI_USE_SKILL_HARNESS_MEMORY", True):
            skill_harness_memory = retrieve_task_memory(instruction)
        effective_use_skills = self.use_skills and (
            has_skill_pack or bool(skill_harness_memory.get("prompt"))
        )
        pi_system_prompt = _pi_system_prompt(
            pi_skills,
            str(skill_harness_memory.get("prompt") or ""),
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
            "provider_api": self.provider_api,
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
        )
        command = (
            "set -euo pipefail\n"
            "mkdir -p /logs/agent ~/.pi/agent\n"
            f"{'' if has_skill_pack else _pi_no_skills_cleanup_command()}"
            "if [ -s ~/.nvm/nvm.sh ]; then . ~/.nvm/nvm.sh; fi\n"
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
            f"{no_diff_rescue_loop_command}"
            f"{cleanup_transient_logs_command}"
        )

        for setup_command in pi_skills_setup_commands:
            await self.exec_as_agent(
                environment,
                command="set -euo pipefail\nmkdir -p /logs/agent ~/.pi/agent\n" + setup_command,
                env=env,
            )
        await self.exec_as_agent(environment, command=command, env=env)

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
        system_prompt = str(metadata.get("system_prompt") or PI_SYSTEM_PROMPT)
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
            "source": "harbor-swebench-verified",
            "model": metadata.get("model") or self.model_name,
            "conversations": conversations,
            "metadata": {
                "agent": self.name(),
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
        if forbidden_hits:
            preview = "; ".join(
                f"event {item['event_index']}: {', '.join(item['markers'])}"
                for item in forbidden_hits[:5]
            )
            raise RuntimeError(
                "Pi trace contains forbidden assistant.content markers: " + preview
            )
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
            "pi_system_prompt": metadata.get("system_prompt") or PI_SYSTEM_PROMPT,
            "pi_tool_call_rounds": sharegpt["metadata"]["tool_call_rounds"],
            "pi_json_event_count": len(events),
            "sharegpt_trace_messages": sharegpt["metadata"]["trace_messages"],
            "sharegpt_trace_chars": sharegpt["metadata"]["trace_chars"],
        }
