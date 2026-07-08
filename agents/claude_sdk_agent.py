import json
import os
import shlex
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

from agents.skill_harness_memory import retrieve_task_memory


CLAUDE_SYSTEM_PROMPT = """You are Claude Agent, a terminal-based software engineering agent running inside a Harbor benchmark task sandbox.

Goal:
- Modify the repository in the current working directory so the benchmark issue is fixed.
- Prefer small, targeted source changes. Do not change tests unless the task explicitly requires it.
- Inspect the repository before editing. Run relevant tests when practical.
- Leave the final state in the working tree; Harbor will run the verifier after you exit.
- Do not answer with only a repository overview, research summary, README summary, or implementation plan.

Operational constraints:
- Use the available Claude Code tools to inspect, edit, and test the repository.
- Your first assistant action after receiving the benchmark issue should inspect the repository.
- Do not ask the user for clarification during benchmark execution.
- Do not exfiltrate secrets or print environment variables containing API keys.
- Keep the final message concise and include changed files and verification commands.
"""


CLAUDE_TASK_PREFIX = """Use Claude Agent's available tools to inspect the repository, make a targeted source-code fix, and run a relevant verification command when practical.

BENCHMARK ISSUE:
"""


CLAUDE_TASK_PREFIX_WITH_SKILLS = """Use Claude Agent's available tools to inspect the repository, treat skill memory and task evidence as evidence-gated weak hints, make a targeted source-code fix, and run a relevant verification command when practical.

BENCHMARK ISSUE:
"""


REMOTE_VENV = PurePosixPath("/tmp/harbor-claude-agent-venv")
REMOTE_PYTHON = REMOTE_VENV / "bin/python"


RUNNER_SCRIPT = r'''
import asyncio
import copy
import json
import os
from dataclasses import asdict, is_dataclass
from pathlib import Path
from typing import Any

from claude_agent_sdk import ClaudeAgentOptions, ClaudeSDKClient


def _repair_provider_content_blocks(data: Any) -> Any:
    if not isinstance(data, dict):
        return data
    message = data.get("message")
    if not isinstance(message, dict):
        return data
    content = message.get("content")
    if not isinstance(content, list):
        return data

    repaired = data
    changed = False
    def mutable_block(index: int) -> dict[str, Any]:
        nonlocal repaired, message, content, changed
        if not changed:
            repaired = copy.deepcopy(data)
            message = repaired.get("message", {})
            content = message.get("content", [])
            changed = True
        return content[index]

    for index, block in enumerate(content):
        if not isinstance(block, dict):
            continue
        if block.get("type") in {"tool_use", "server_tool_use"}:
            repaired_block = None
            if not block.get("id"):
                repaired_block = mutable_block(index)
                base = (
                    message.get("id")
                    or repaired.get("uuid")
                    or repaired.get("session_id")
                    or "missing-message-id"
                )
                repaired_block["id"] = f"{base}-synthetic-tool-{index}"
            if not block.get("name"):
                repaired_block = repaired_block or mutable_block(index)
                repaired_block["name"] = "unknown_tool"
            if "input" not in block or block.get("input") is None:
                repaired_block = repaired_block or mutable_block(index)
                repaired_block["input"] = {}
        elif block.get("type") == "thinking" and "signature" not in block:
            mutable_block(index)["signature"] = ""
        elif block.get("type") == "tool_result" and "tool_use_id" not in block:
            repaired_block = mutable_block(index)
            base = (
                message.get("id")
                or repaired.get("uuid")
                or repaired.get("session_id")
                or "missing-message-id"
            )
            repaired_block["tool_use_id"] = f"{base}-synthetic-tool-result-{index}"
    return repaired


def _install_message_parser_compat() -> None:
    import claude_agent_sdk._internal.message_parser as message_parser

    original = message_parser.parse_message
    if getattr(original, "_skills_evo_missing_tool_id_patch", False):
        return

    def parse_message_with_missing_tool_id_compat(data: dict[str, Any]) -> Any:
        return original(_repair_provider_content_blocks(data))

    parse_message_with_missing_tool_id_compat._skills_evo_missing_tool_id_patch = True
    message_parser.parse_message = parse_message_with_missing_tool_id_compat


def _to_plain(value: Any) -> Any:
    if is_dataclass(value):
        data = asdict(value)
        data["sdk_message_class"] = value.__class__.__name__
        return _to_plain(data)
    if isinstance(value, dict):
        return {
            str(key): _to_plain(item)
            for key, item in value.items()
            if item is not None
        }
    if isinstance(value, list):
        return [_to_plain(item) for item in value]
    return value


def _optional_int(name: str) -> int | None:
    raw = os.environ.get(name, "").strip()
    return int(raw) if raw else None


def _optional_float(name: str) -> float | None:
    raw = os.environ.get(name, "").strip()
    return float(raw) if raw else None


def _disable_thinking_for_model(model: str) -> bool:
    normalized = model.lower().replace("_", "-")
    return "glm-5.2" in normalized or "glm5.2" in normalized


async def main() -> int:
    _install_message_parser_compat()

    instruction_path = Path(os.environ["HARBOR_CLAUDE_INSTRUCTION_PATH"])
    jsonl_path = Path(os.environ["HARBOR_CLAUDE_JSONL_PATH"])
    result_path = Path(os.environ["HARBOR_CLAUDE_RESULT_PATH"])
    prompt = instruction_path.read_text(encoding="utf-8", errors="replace")

    provider_model = os.environ["ANTHROPIC_MODEL"]
    sdk_env = {
        "ANTHROPIC_AUTH_TOKEN": os.environ["ANTHROPIC_AUTH_TOKEN"],
        "ANTHROPIC_BASE_URL": os.environ["ANTHROPIC_BASE_URL"],
        "ANTHROPIC_MODEL": provider_model,
        "ANTHROPIC_DEFAULT_SONNET_MODEL": provider_model,
        "ANTHROPIC_DEFAULT_OPUS_MODEL": provider_model,
        "ANTHROPIC_DEFAULT_HAIKU_MODEL": provider_model,
        "ANTHROPIC_SMALL_FAST_MODEL": provider_model,
        "CLAUDE_CODE_SUBAGENT_MODEL": provider_model,
        "CLAUDE_CONFIG_DIR": os.environ["CLAUDE_CONFIG_DIR"],
        "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1",
        "DISABLE_TELEMETRY": "1",
        "IS_SANDBOX": "1",
        # Always disable the Claude Code attribution header; external override wins.
        "CLAUDE_CODE_ATTRIBUTION_HEADER": os.environ.get(
            "CLAUDE_CODE_ATTRIBUTION_HEADER", "0"
        ),
    }
    if os.environ.get("CLAUDE_CODE_MAX_OUTPUT_TOKENS"):
        sdk_env["CLAUDE_CODE_MAX_OUTPUT_TOKENS"] = os.environ[
            "CLAUDE_CODE_MAX_OUTPUT_TOKENS"
        ]

    # Force the agent subprocess to connect directly to ANTHROPIC_BASE_URL,
    # bypassing any HTTP/2 proxy/gateway. The endpoint itself is HTTP/1.1, so
    # an intermediary HTTP/2 layer is what emits `ConnectionTerminated
    # (max_age)` GOAWAY frames mid-session and kills long agent runs. Clearing
    # the proxy env (overridable via HARBOR_CLAUDE_KEEP_PROXY=1) removes it.
    if os.environ.get("HARBOR_CLAUDE_KEEP_PROXY", "").strip() not in {"1", "true", "yes"}:
        for _proxy_var in (
            "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY",
            "http_proxy", "https_proxy", "all_proxy",
        ):
            sdk_env[_proxy_var] = ""
        sdk_env["NO_PROXY"] = "*"
        sdk_env["no_proxy"] = "*"

    options_kwargs = {
        "system_prompt": os.environ["HARBOR_CLAUDE_SYSTEM_PROMPT"],
        "model": provider_model,
        "cwd": os.getcwd(),
        "env": sdk_env,
        "permission_mode": "bypassPermissions",
        "setting_sources": [],
        "skills": [],
        "max_turns": _optional_int("HARBOR_CLAUDE_MAX_TURNS"),
        "max_budget_usd": _optional_float("HARBOR_CLAUDE_MAX_BUDGET_USD"),
    }
    if _disable_thinking_for_model(provider_model):
        sdk_env["MAX_THINKING_TOKENS"] = "0"
        options_kwargs["thinking"] = {"type": "disabled"}
        options_kwargs["max_thinking_tokens"] = 0

    options = ClaudeAgentOptions(**options_kwargs)

    events = []
    with jsonl_path.open("w", encoding="utf-8") as handle:
        try:
            async with ClaudeSDKClient(options=options) as client:
                await client.query(prompt)
                async for message in client.receive_response():
                    event = _to_plain(message)
                    events.append(event)
                    handle.write(json.dumps(event, ensure_ascii=False) + "\n")
                    handle.flush()
                    if event.get("sdk_message_class") == "ResultMessage":
                        break
        except BaseException as exc:
            error = {
                "sdk_message_class": "SDKException",
                "type": exc.__class__.__name__,
                "message": str(exc),
            }
            handle.write(json.dumps(error, ensure_ascii=False) + "\n")
            result_path.write_text(
                json.dumps({"ok": False, "error": error}, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            raise

    completed = bool(
        events and isinstance(events[-1], dict)
        and events[-1].get("sdk_message_class") == "ResultMessage"
    )
    result_path.write_text(
        json.dumps(
            {
                "ok": completed,
                "completed": completed,
                "termination": "result_message" if completed else "stream_ended_without_result",
                "message_count": len(events),
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
'''


def _compact(value: Any, limit: int = 20_000) -> str:
    if isinstance(value, str):
        text = value
    else:
        try:
            text = json.dumps(value, ensure_ascii=False, indent=2)
        except TypeError:
            text = str(value)
    if len(text) > limit:
        return text[:limit] + "\n...[truncated]"
    return text


def _int_value(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _message_text_from_blocks(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict):
                if item.get("text") is not None:
                    parts.append(str(item["text"]))
                elif item.get("content") is not None and item.get("tool_use_id") is None:
                    parts.append(_message_text_from_blocks(item["content"]))
        return "".join(parts)
    return str(content) if content is not None else ""


def _write_local_json(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _event_class(event: dict[str, Any]) -> str:
    sdk_class = event.get("sdk_message_class")
    if isinstance(sdk_class, str):
        return sdk_class
    event_type = event.get("type")
    if event_type == "assistant":
        return "AssistantMessage"
    if event_type == "user":
        return "UserMessage"
    if event_type == "result":
        return "ResultMessage"
    if event_type == "system":
        return "SystemMessage"
    return str(event_type or "")


def _event_content(event: dict[str, Any]) -> Any:
    if "content" in event:
        return event.get("content")
    message = event.get("message")
    if isinstance(message, dict):
        return message.get("content")
    return None


def _event_model(event: dict[str, Any]) -> str | None:
    if isinstance(event.get("model"), str):
        return event["model"]
    message = event.get("message")
    if isinstance(message, dict) and isinstance(message.get("model"), str):
        return message["model"]
    return None


def _event_usage(event: dict[str, Any]) -> dict[str, Any]:
    usage: dict[str, Any] = {}
    message = event.get("message")
    if isinstance(message, dict) and isinstance(message.get("usage"), dict):
        usage.update(message["usage"])
    if isinstance(event.get("usage"), dict):
        usage.update(event["usage"])
    return usage


def _event_usage_key(event: dict[str, Any]) -> str | None:
    for key in ("message_id", "id"):
        value = event.get(key)
        if isinstance(value, str) and value:
            return value
    message = event.get("message")
    if isinstance(message, dict):
        for key in ("id", "message_id"):
            value = message.get(key)
            if isinstance(value, str) and value:
                return value
    return None


def _event_session_id(event: dict[str, Any]) -> str | None:
    session_id = event.get("session_id")
    if isinstance(session_id, str) and session_id:
        return session_id
    data = event.get("data")
    if isinstance(data, dict) and isinstance(data.get("session_id"), str):
        return data["session_id"]
    return None


def _is_noisy_system_event(event: dict[str, Any]) -> bool:
    if _event_class(event) != "SystemMessage":
        return False
    subtype = event.get("subtype")
    if subtype == "thinking_tokens":
        return True
    data = event.get("data")
    return isinstance(data, dict) and data.get("subtype") == "thinking_tokens"


def _assistant_thinking_block_count(event: dict[str, Any]) -> int:
    if _event_class(event) != "AssistantMessage":
        return 0
    content = _event_content(event)
    if not isinstance(content, list):
        return 0
    return sum(
        1
        for block in content
        if isinstance(block, dict) and block.get("thinking") is not None
    )


def _filtered_event(event: dict[str, Any]) -> dict[str, Any] | None:
    if _is_noisy_system_event(event):
        return None
    if _event_class(event) != "AssistantMessage":
        return event

    content = _event_content(event)
    if not isinstance(content, list):
        return event

    filtered_content = [
        block
        for block in content
        if not (isinstance(block, dict) and block.get("thinking") is not None)
    ]
    if not filtered_content:
        return None

    copied = json.loads(json.dumps(event, ensure_ascii=False))
    if "content" in copied:
        copied["content"] = filtered_content
    elif isinstance(copied.get("message"), dict):
        copied["message"]["content"] = filtered_content
    return copied


def _events_completed(events: list[dict[str, Any]]) -> bool:
    return any(_event_class(event) == "ResultMessage" for event in events)


def _termination_status(events: list[dict[str, Any]]) -> str:
    if _events_completed(events):
        return "result_message"
    if any(_event_class(event) == "SDKException" for event in events):
        return "sdk_exception"
    if events:
        return "interrupted_or_timeout"
    return "no_events"


def _disable_thinking_for_provider_model(model: str) -> bool:
    normalized = model.lower().replace("_", "-")
    return (
        "glm-5.2" in normalized
        or "glm5.2" in normalized
        or "qwen3.6" in normalized
        or "qwen3-6" in normalized
    )


def _env_text(value: str | None, default: str = "") -> str:
    if value is None:
        return default
    return str(value)


def _env_bool(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _task_filter_text(instruction: str, environment: BaseEnvironment) -> str:
    parts = [instruction]
    for attr in ("environment_name", "session_id"):
        value = getattr(environment, attr, "")
        if value:
            parts.append(str(value))
    return "\n".join(parts)


def _claude_system_prompt(memory_prompt: str = "") -> str:
    return CLAUDE_SYSTEM_PROMPT.rstrip() + str(memory_prompt or "") + "\n"


def _python_selector_script() -> str:
    return r'''
pick_python() {
  for bin in python3.12 python3.11 python3.10 python3; do
    if command -v "$bin" >/dev/null 2>&1; then
      if "$bin" - <<'PY' >/dev/null 2>&1
import sys
raise SystemExit(0 if sys.version_info >= (3, 10) else 1)
PY
      then
        command -v "$bin"
        return 0
      fi
    fi
  done
  return 1
}
'''


class ClaudeSdkAgent(BaseInstalledAgent):
    """Harbor installed-agent adapter that runs Claude Agent SDK inside E2B."""

    SUPPORTS_ATIF = True

    _JSONL_FILENAME = "claude-agent-sdk.jsonl"
    _FILTERED_JSONL_FILENAME = "claude-agent-sdk.filtered.jsonl"
    _STDERR_FILENAME = "claude-agent-sdk-stderr.txt"
    _RESULT_FILENAME = "claude-agent-sdk-result.json"
    _RUNNER_FILENAME = "claude_agent_sdk_runner.py"
    _TRAJECTORY_FILENAME = "trajectory.json"
    _SHAREGPT_FILENAME = "sharegpt.json"
    _METADATA_FILENAME = "claude-agent-metadata.json"
    _SYSTEM_PROMPT_FILENAME = "claude-system-prompt.md"
    _INSTRUCTION_FILENAME = "problem_statement.md"

    def __init__(
        self,
        logs_dir: Path,
        provider_name: str = "novita",
        api_key_env: str = "NOVITA_API_KEY",
        anthropic_base_url_env: str = "NOVITA_ANTHROPIC_BASE_URL",
        model_env: str = "NOVITA_MODEL",
        default_anthropic_base_url: str = "https://api.novita.ai/anthropic",
        default_model: str = "zai-org/glm-5.2",
        max_turns: int | None = None,
        max_budget_usd: float | None = None,
        result_only: bool = False,
        use_skills: bool = False,
        benchmark_name: str = "swe-bench",
        *args: Any,
        **kwargs: Any,
    ):
        super().__init__(logs_dir=logs_dir, *args, **kwargs)
        self.provider_name = provider_name
        self.api_key_env = api_key_env
        self.anthropic_base_url_env = anthropic_base_url_env
        self.model_env = model_env
        self.default_anthropic_base_url = default_anthropic_base_url
        self.default_model = default_model
        self.max_turns = max_turns
        self.max_budget_usd = max_budget_usd
        self.result_only = result_only
        self.use_skills = use_skills
        self.benchmark_name = benchmark_name

    @staticmethod
    def name() -> str:
        return "claude-agent-sdk"

    def get_version_command(self) -> str | None:
        return (
            f"{shlex.quote(str(REMOTE_PYTHON))} - <<'PY'\n"
            "import claude_agent_sdk\n"
            "print(getattr(claude_agent_sdk, '__version__', 'unknown'))\n"
            "PY"
        )

    def parse_version(self, stdout: str) -> str:
        for line in reversed(stdout.strip().splitlines()):
            line = line.strip()
            if line:
                return line
        return stdout.strip()

    def _get_env(self, name: str) -> str | None:
        value = os.environ.get(name)
        if value:
            return value
        return getattr(self, "_extra_env", {}).get(name)

    async def install(self, environment: BaseEnvironment) -> None:
        await self.exec_as_root(
            environment,
            command=(
                "if command -v apt-get >/dev/null 2>&1; then "
                "apt-get update && apt-get install -y python3-venv python3-pip ca-certificates; "
                "elif command -v apk >/dev/null 2>&1; then "
                "apk add --no-cache python3 py3-pip ca-certificates; "
                "elif command -v yum >/dev/null 2>&1; then "
                "yum install -y python3 python3-pip ca-certificates; "
                "fi"
            ),
            env={"DEBIAN_FRONTEND": "noninteractive"},
        )
        install_command = (
            "set -euo pipefail\n"
            f"{_python_selector_script()}\n"
            "PYBIN=\"$(pick_python)\"\n"
            f"rm -rf {shlex.quote(str(REMOTE_VENV))}\n"
            f"\"$PYBIN\" -m venv {shlex.quote(str(REMOTE_VENV))}\n"
            f"{shlex.quote(str(REMOTE_PYTHON))} -m pip install --upgrade pip\n"
            f"{shlex.quote(str(REMOTE_PYTHON))} -m pip install --upgrade claude-agent-sdk\n"
            f"{shlex.quote(str(REMOTE_PYTHON))} - <<'PY'\n"
            "import claude_agent_sdk\n"
            "print(getattr(claude_agent_sdk, '__version__', 'unknown'))\n"
            "PY"
        )
        result = await self.exec_as_agent(environment, command=install_command)
        self._version = self.parse_version(result.stdout)

    def _required_env(self) -> dict[str, str]:
        api_key = self._get_env(self.api_key_env)
        missing = [self.api_key_env] if not api_key else []
        if missing:
            raise ValueError(
                "Missing required environment variables for ClaudeSdkAgent: "
                + ", ".join(missing)
            )
        model = _env_text(self._get_env(self.model_env), self.default_model)
        anthropic_base_url = _env_text(
            self._get_env(self.anthropic_base_url_env),
            self.default_anthropic_base_url,
        )
        env = {
            self.api_key_env: api_key,
            self.anthropic_base_url_env: anthropic_base_url,
            self.model_env: model,
            "ANTHROPIC_AUTH_TOKEN": api_key,
            "ANTHROPIC_BASE_URL": anthropic_base_url,
            "ANTHROPIC_MODEL": model,
            # Always disable the Claude Code attribution header; external override wins.
            "CLAUDE_CODE_ATTRIBUTION_HEADER": self._get_env(
                "CLAUDE_CODE_ATTRIBUTION_HEADER"
            )
            or "0",
        }
        max_output_tokens = self._get_env("CLAUDE_CODE_MAX_OUTPUT_TOKENS")
        if max_output_tokens:
            env["CLAUDE_CODE_MAX_OUTPUT_TOKENS"] = max_output_tokens
        return env

    def _effective_instruction(self, instruction: str, *, use_skills: bool = False) -> str:
        prefix = CLAUDE_TASK_PREFIX_WITH_SKILLS if use_skills else CLAUDE_TASK_PREFIX
        return prefix + instruction

    async def _write_remote_file(
        self,
        environment: BaseEnvironment,
        path: PurePosixPath,
        text: str,
        env: dict[str, str] | None = None,
    ) -> None:
        quoted_path = shlex.quote(str(path))
        await self.exec_as_agent(
            environment,
            command=f"mkdir -p {shlex.quote(str(path.parent))} && : > {quoted_path}",
            env=env,
        )
        encoded = text.encode("utf-8").hex()
        for start in range(0, len(encoded), 48_000):
            chunk = encoded[start : start + 48_000]
            await self.exec_as_agent(
                environment,
                command=(
                    f"{shlex.quote(str(REMOTE_PYTHON))} - <<'PY'\n"
                    "from pathlib import Path\n"
                    f"path = Path({str(path)!r})\n"
                    f"path.open('ab').write(bytes.fromhex({chunk!r}))\n"
                    "PY"
                ),
                env=env,
            )

    @with_prompt_template
    async def run(
        self,
        instruction: str,
        environment: BaseEnvironment,
        context: AgentContext,
    ) -> None:
        env = self._required_env()
        provider_model = env[self.model_env]
        model = self.model_name or f"{self.provider_name}/{provider_model}"
        skill_harness_memory = {
            "enabled": False,
            "reason": "disabled",
            "prompt": "",
            "selected_entries": [],
        }
        if self.use_skills and _env_bool(
            "CLAUDE_USE_SKILL_HARNESS_MEMORY",
            _env_bool("PI_USE_SKILL_HARNESS_MEMORY", True),
        ):
            skill_harness_memory = retrieve_task_memory(
                _task_filter_text(instruction, environment)
            )
        effective_use_skills = self.use_skills and bool(
            skill_harness_memory.get("prompt")
        )
        claude_system_prompt = _claude_system_prompt(
            str(skill_harness_memory.get("prompt") or "")
            if effective_use_skills
            else ""
        )
        effective_instruction = self._effective_instruction(
            instruction,
            use_skills=effective_use_skills,
        )
        thinking_disabled = _disable_thinking_for_provider_model(provider_model)

        instruction_path = PurePosixPath(EnvironmentPaths.agent_dir / self._INSTRUCTION_FILENAME)
        system_prompt_path = PurePosixPath(EnvironmentPaths.agent_dir / self._SYSTEM_PROMPT_FILENAME)
        metadata_path = PurePosixPath(EnvironmentPaths.agent_dir / self._METADATA_FILENAME)
        runner_path = PurePosixPath(EnvironmentPaths.agent_dir / self._RUNNER_FILENAME)
        jsonl_path = PurePosixPath(EnvironmentPaths.agent_dir / self._JSONL_FILENAME)
        stderr_path = PurePosixPath(EnvironmentPaths.agent_dir / self._STDERR_FILENAME)
        result_path = PurePosixPath(EnvironmentPaths.agent_dir / self._RESULT_FILENAME)
        claude_config_dir = PurePosixPath(EnvironmentPaths.agent_dir / "claude-config")

        metadata = {
            "agent": self.name(),
            "claude_agent_sdk_version": self._version,
            "provider": self.provider_name,
            "model": model,
            "provider_model": provider_model,
            "provider_base_url": env["ANTHROPIC_BASE_URL"],
            "benchmark_name": self.benchmark_name,
            "api_key_env": self.api_key_env,
            "model_env": self.model_env,
            "system_prompt": claude_system_prompt,
            "tool_policy": "default Claude Code toolset; permission_mode=bypassPermissions",
            "skills_policy": (
                "skill_harness_memory prompt injected; "
                "ClaudeAgentOptions(skills=[], setting_sources=[])"
                if effective_use_skills
                else "disabled via ClaudeAgentOptions(skills=[], setting_sources=[])"
            ),
            "use_skills": effective_use_skills,
            "skill_harness_memory": {
                key: value
                for key, value in skill_harness_memory.items()
                if key != "prompt"
            },
            "thinking_policy": (
                "disabled via ClaudeAgentOptions(thinking={'type': 'disabled'}, "
                "max_thinking_tokens=0) for GLM/Qwen provider models"
                if thinking_disabled
                else "provider default"
            ),
            "execution_location": "harbor_environment",
        }

        setup_command = (
            "set -euo pipefail\n"
            "mkdir -p /logs/agent "
            f"{shlex.quote(str(claude_config_dir))} "
            f"{shlex.quote(str(claude_config_dir / 'skills'))}\n"
            f"rm -rf {shlex.quote(str(claude_config_dir / 'skills'))}\n"
            f"mkdir -p {shlex.quote(str(claude_config_dir / 'skills'))}\n"
            f": > {shlex.quote(str(stderr_path))}\n"
        )
        await self.exec_as_agent(environment, command=setup_command, env=env)
        await self._write_remote_file(environment, instruction_path, effective_instruction, env)
        await self._write_remote_file(environment, system_prompt_path, claude_system_prompt, env)
        await self._write_remote_file(
            environment,
            metadata_path,
            json.dumps(metadata, ensure_ascii=False, indent=2) + "\n",
            env,
        )
        await self._write_remote_file(environment, runner_path, RUNNER_SCRIPT, env)

        run_env = {
            **env,
            "CLAUDE_CONFIG_DIR": str(claude_config_dir),
            "HARBOR_CLAUDE_INSTRUCTION_PATH": str(instruction_path),
            "HARBOR_CLAUDE_JSONL_PATH": str(jsonl_path),
            "HARBOR_CLAUDE_STDERR_PATH": str(stderr_path),
            "HARBOR_CLAUDE_RESULT_PATH": str(result_path),
            "HARBOR_CLAUDE_SYSTEM_PROMPT": claude_system_prompt,
            "HARBOR_CLAUDE_MAX_TURNS": str(self.max_turns or ""),
            "HARBOR_CLAUDE_MAX_BUDGET_USD": str(self.max_budget_usd or ""),
        }
        command = (
            "set -euo pipefail\n"
            f"{shlex.quote(str(REMOTE_PYTHON))} {shlex.quote(str(runner_path))} "
            f"2> >(tee {shlex.quote(str(stderr_path))} >&2)"
        )
        await self.exec_as_agent(environment, command=command, env=run_env)

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
                events.append(json.loads(line))
            except json.JSONDecodeError:
                continue
        return events

    def _write_filtered_jsonl(self, events: list[dict[str, Any]]) -> tuple[Path, int]:
        path = self.logs_dir / self._FILTERED_JSONL_FILENAME
        filtered = [
            event
            for event in (_filtered_event(event) for event in events)
            if event is not None
        ]
        with path.open("w", encoding="utf-8") as handle:
            for event in filtered:
                handle.write(json.dumps(event, ensure_ascii=False) + "\n")
        return path, len(filtered)

    def _metadata(self) -> dict[str, Any]:
        path = self.logs_dir / self._METADATA_FILENAME
        if not path.exists():
            return {}
        try:
            return json.loads(path.read_text(errors="replace"))
        except json.JSONDecodeError:
            return {}

    def _instruction_text(self) -> str:
        path = self.logs_dir / self._INSTRUCTION_FILENAME
        return path.read_text(errors="replace") if path.exists() else ""

    def _runner_result(self) -> dict[str, Any]:
        path = self.logs_dir / self._RESULT_FILENAME
        if not path.exists():
            return {}
        try:
            value = json.loads(path.read_text(errors="replace"))
        except json.JSONDecodeError:
            return {}
        return value if isinstance(value, dict) else {}

    def _convert_to_trajectory(
        self, events: list[dict[str, Any]], metadata: dict[str, Any]
    ) -> Trajectory | None:
        steps: list[Step] = []
        pending_tool_steps: dict[str, Step] = {}
        step_id = 1
        instruction = self._instruction_text()
        if instruction:
            steps.append(Step(step_id=step_id, source="user", message=instruction))
            step_id += 1

        model_name = str(metadata.get("provider_model") or self.model_name or "")
        session_id = "claude-agent-sdk-session"
        total_prompt_tokens = 0
        total_completion_tokens = 0
        total_cached_tokens = 0
        total_cost = 0.0
        result_prompt_tokens: int | None = None
        result_completion_tokens: int | None = None
        result_cached_tokens: int | None = None
        seen_usage_keys: set[str] = set()
        usage_event_count = 0
        deduped_usage_event_count = 0
        task_notification_count = 0
        assistant_thinking_block_count = 0

        for event in events:
            message_class = _event_class(event)
            event_session_id = _event_session_id(event)
            if event_session_id:
                session_id = event_session_id

            usage = _event_usage(event)
            if usage:
                usage_event_count += 1
                input_tokens = _int_value(
                    usage.get("input_tokens") or usage.get("prompt_tokens") or usage.get("input")
                )
                output_tokens = _int_value(
                    usage.get("output_tokens")
                    or usage.get("completion_tokens")
                    or usage.get("output")
                )
                cached_tokens = _int_value(
                    usage.get("cache_read_input_tokens")
                    or usage.get("cached_tokens")
                    or usage.get("cacheRead")
                )
                if message_class == "ResultMessage":
                    result_prompt_tokens = input_tokens
                    result_completion_tokens = output_tokens
                    result_cached_tokens = cached_tokens
                else:
                    usage_key = _event_usage_key(event) or f"event:{usage_event_count}"
                    if usage_key not in seen_usage_keys:
                        seen_usage_keys.add(usage_key)
                        total_prompt_tokens += input_tokens
                        total_completion_tokens += output_tokens
                        total_cached_tokens += cached_tokens
                    else:
                        deduped_usage_event_count += 1

            if message_class == "ResultMessage":
                total_cost += float(event.get("total_cost_usd") or 0)
                result_text = event.get("result")
                if isinstance(result_text, str) and result_text.strip():
                    steps.append(
                        Step(
                            step_id=step_id,
                            source="agent",
                            message=result_text,
                            model_name=model_name or None,
                        )
                    )
                    step_id += 1
                continue

            if message_class == "AssistantMessage":
                assistant_thinking_block_count += _assistant_thinking_block_count(event)
                content = _event_content(event)
                text_parts: list[str] = []
                tool_calls: list[ToolCall] = []
                if isinstance(content, list):
                    for block in content:
                        if not isinstance(block, dict):
                            continue
                        if "text" in block:
                            text = _message_text_from_blocks([block])
                            if text:
                                text_parts.append(text)
                        elif "id" in block and "name" in block and "input" in block:
                            call_id = str(block.get("id") or f"claude-tool-{step_id}")
                            tool_calls.append(
                                ToolCall(
                                    tool_call_id=call_id,
                                    function_name=str(block.get("name") or "tool"),
                                    arguments=block.get("input")
                                    if isinstance(block.get("input"), dict)
                                    else {"value": block.get("input")},
                                )
                            )
                text = "\n".join(part for part in text_parts if part).strip()
                if text or tool_calls:
                    step = Step(
                        step_id=step_id,
                        source="agent",
                        message=text or "Tool call",
                        model_name=model_name or _event_model(event) or None,
                        tool_calls=tool_calls or None,
                    )
                    steps.append(step)
                    for tool_call in tool_calls:
                        pending_tool_steps[tool_call.tool_call_id] = step
                    step_id += 1
                continue

            if message_class == "UserMessage":
                content = _event_content(event)
                if isinstance(content, list):
                    for block in content:
                        if not isinstance(block, dict):
                            continue
                        tool_use_id = block.get("tool_use_id")
                        if tool_use_id:
                            call_id = str(tool_use_id)
                            step = pending_tool_steps.get(call_id)
                            observation_result = ObservationResult(
                                source_call_id=call_id,
                                content=_compact(block.get("content")),
                            )
                            if step is not None:
                                if step.observation is None:
                                    step.observation = Observation(results=[observation_result])
                                else:
                                    step.observation.results.append(observation_result)
                    text = _message_text_from_blocks(content)
                else:
                    text = str(content or "")
                if text.strip():
                    steps.append(Step(step_id=step_id, source="user", message=text))
                    step_id += 1
                continue

            if message_class == "TaskNotificationMessage":
                task_notification_count += 1
                tool_use_id = event.get("tool_use_id")
                if not tool_use_id and isinstance(event.get("data"), dict):
                    tool_use_id = event["data"].get("tool_use_id")
                call_id = str(tool_use_id or "")
                step = pending_tool_steps.get(call_id) if call_id else None
                if step is None or step.observation is not None:
                    continue
                status = event.get("status")
                summary = event.get("summary")
                data = event.get("data") if isinstance(event.get("data"), dict) else {}
                content = "\n".join(
                    str(item)
                    for item in (
                        status or data.get("status"),
                        summary or data.get("summary"),
                        data.get("output_file"),
                    )
                    if item
                )
                if content:
                    step.observation = Observation(
                        results=[
                            ObservationResult(
                                source_call_id=call_id,
                                content=_compact(content),
                            )
                        ]
                    )

        if not steps:
            return None

        if result_prompt_tokens is not None:
            total_prompt_tokens = result_prompt_tokens
        if result_completion_tokens is not None:
            total_completion_tokens = result_completion_tokens
        if result_cached_tokens is not None:
            total_cached_tokens = result_cached_tokens

        final_metrics = FinalMetrics(
            total_prompt_tokens=total_prompt_tokens or None,
            total_completion_tokens=total_completion_tokens or None,
            total_cached_tokens=total_cached_tokens or None,
            total_cost_usd=total_cost or None,
            total_steps=len(steps),
            extra={
                "tool_call_rounds": sum(1 for step in steps if step.tool_calls),
                "sdk_event_count": len(events),
                "filtered_sdk_event_count": sum(
                    1 for event in (_filtered_event(event) for event in events) if event is not None
                ),
                "usage_event_count": usage_event_count,
                "deduped_usage_event_count": deduped_usage_event_count,
                "task_notification_count": task_notification_count,
                "assistant_thinking_block_count": assistant_thinking_block_count,
                "completed": _events_completed(events),
                "termination": _termination_status(events),
            },
        )

        return Trajectory(
            schema_version="ATIF-v1.6",
            session_id=session_id,
            agent=Agent(
                name=self.name(),
                version=str(
                    metadata.get("claude_agent_sdk_version")
                    or self._version
                    or "unknown"
                ),
                model_name=model_name or None,
                extra={
                    "provider": metadata.get("provider"),
                    "provider_base_url": metadata.get("provider_base_url"),
                    "provider_model": metadata.get("provider_model"),
                    "system_prompt": metadata.get("system_prompt"),
                    "tool_policy": metadata.get("tool_policy"),
                    "skills_policy": metadata.get("skills_policy"),
                    "execution_location": metadata.get("execution_location"),
                    "thinking_policy": metadata.get("thinking_policy"),
                    "completed": _events_completed(events),
                    "termination": _termination_status(events),
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
        conversations: list[dict[str, str]] = [
            {"from": "system", "value": metadata.get("system_prompt") or CLAUDE_SYSTEM_PROMPT}
        ]
        instruction = self._instruction_text()
        if instruction:
            conversations.append({"from": "human", "value": instruction})

        for event in events:
            message_class = _event_class(event)
            if message_class == "AssistantMessage":
                content = _event_content(event)
                text = _message_text_from_blocks(content)
                if text:
                    conversations.append({"from": "gpt", "value": text})
                if isinstance(content, list):
                    for block in content:
                        if isinstance(block, dict) and "id" in block and "name" in block:
                            conversations.append(
                                {
                                    "from": "gpt",
                                    "value": (
                                        f"<tool_call name=\"{block.get('name')}\" id=\"{block.get('id')}\">\n"
                                        f"{_compact(block.get('input'))}\n"
                                        "</tool_call>"
                                    ),
                                }
                            )
            elif message_class == "UserMessage":
                content = _event_content(event)
                if isinstance(content, list):
                    for block in content:
                        if isinstance(block, dict) and block.get("tool_use_id"):
                            conversations.append(
                                {
                                    "from": "tool",
                                    "value": (
                                        f"<tool_result id=\"{block.get('tool_use_id')}\">\n"
                                        f"{_compact(block.get('content'))}\n"
                                        "</tool_result>"
                                    ),
                                }
                            )
            elif message_class == "ResultMessage" and event.get("result"):
                conversations.append({"from": "gpt", "value": str(event["result"])})

        trace_chars = sum(len(item["value"]) for item in conversations)
        return {
            "id": trajectory.session_id if trajectory else "claude-agent-sdk-session",
            "source": "harbor",
            "model": metadata.get("provider_model") or self.model_name,
            "conversations": conversations,
            "metadata": {
                "agent": self.name(),
                "provider": metadata.get("provider"),
                "provider_model": metadata.get("provider_model"),
                "trace_messages": len(conversations),
                "trace_chars": trace_chars,
                "sdk_event_count": len(events),
                "filtered_sdk_event_count": sum(
                    1 for event in (_filtered_event(event) for event in events) if event is not None
                ),
                "assistant_thinking_block_count": sum(
                    _assistant_thinking_block_count(event) for event in events
                ),
                "tool_call_rounds": sum(
                    1
                    for step in (trajectory.steps if trajectory else [])
                    if step.tool_calls
                ),
                "tool_policy": metadata.get("tool_policy"),
                "skills_policy": metadata.get("skills_policy"),
                "execution_location": metadata.get("execution_location"),
                "thinking_policy": metadata.get("thinking_policy"),
                "completed": _events_completed(events),
                "termination": _termination_status(events),
            },
        }

    def populate_context_post_run(self, context: AgentContext) -> None:
        events = self._jsonl_events()
        metadata = self._metadata()
        runner_result = self._runner_result()
        filtered_jsonl_path, filtered_event_count = self._write_filtered_jsonl(events)
        trajectory = self._convert_to_trajectory(events, metadata)
        completed = bool(runner_result.get("completed")) if runner_result else _events_completed(events)
        termination = str(runner_result.get("termination") or _termination_status(events))
        final_metrics_extra = (
            trajectory.final_metrics.extra
            if trajectory is not None and trajectory.final_metrics is not None
            else {}
        )

        if trajectory is not None:
            trajectory_path = self.logs_dir / self._TRAJECTORY_FILENAME
            trajectory_path.write_text(format_trajectory_json(trajectory.to_json_dict()), encoding="utf-8")
            if trajectory.final_metrics:
                context.cost_usd = trajectory.final_metrics.total_cost_usd
                context.n_input_tokens = trajectory.final_metrics.total_prompt_tokens
                context.n_cache_tokens = trajectory.final_metrics.total_cached_tokens
                context.n_output_tokens = trajectory.final_metrics.total_completion_tokens

        sharegpt = self._convert_to_sharegpt(events, metadata, trajectory)
        _write_local_json(self.logs_dir / self._SHAREGPT_FILENAME, sharegpt)
        context.metadata = {
            "sharegpt_path": str(self.logs_dir / self._SHAREGPT_FILENAME),
            "trajectory_path": str(self.logs_dir / self._TRAJECTORY_FILENAME),
            "claude_jsonl_path": str(self.logs_dir / self._JSONL_FILENAME),
            "claude_filtered_jsonl_path": str(filtered_jsonl_path),
            "claude_system_prompt": metadata.get("system_prompt") or CLAUDE_SYSTEM_PROMPT,
            "claude_tool_call_rounds": sharegpt["metadata"]["tool_call_rounds"],
            "sharegpt_trace_messages": sharegpt["metadata"]["trace_messages"],
            "sharegpt_trace_chars": sharegpt["metadata"]["trace_chars"],
            "tool_policy": metadata.get("tool_policy"),
            "skills_policy": metadata.get("skills_policy"),
            "execution_location": metadata.get("execution_location"),
            "thinking_policy": metadata.get("thinking_policy"),
            "completed": completed,
            "termination": termination,
            "sdk_event_count": len(events),
            "filtered_sdk_event_count": filtered_event_count,
            "usage_event_count": final_metrics_extra.get("usage_event_count"),
            "deduped_usage_event_count": final_metrics_extra.get("deduped_usage_event_count"),
            "task_notification_count": final_metrics_extra.get("task_notification_count"),
            "assistant_thinking_block_count": final_metrics_extra.get("assistant_thinking_block_count"),
        }
