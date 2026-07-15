import os
from dataclasses import dataclass, field
from typing import Any, MutableMapping


MACARON_ATTRIBUTION_HEADER_ENV = "CLAUDE_CODE_ATTRIBUTION_HEADER"
MACARON_ATTRIBUTION_HEADER_VALUE = "0"
SUPPORTED_PROVIDER_CHOICES = (
    "openai",
    "tinker",
    "novita",
    "macaron",
    "marcron",
    "sglang",
    "sglang_qwen",
)
CLAUDE_CODE_PROVIDER_CHOICES = (
    "novita",
    "macaron",
    "marcron",
    "sglang",
    "sglang_qwen",
)


def is_macaron_base_url(base_url: str | None) -> bool:
    return "macaron" in str(base_url or "").lower()


def is_novita_base_url(base_url: str | None) -> bool:
    return "novita" in str(base_url or "").lower()


def requires_reasoning_effort_none(base_url: str | None) -> bool:
    return is_macaron_base_url(base_url) or is_novita_base_url(base_url)


def ensure_macaron_attribution_header(
    base_url: str | None,
    env: MutableMapping[str, str] | None = None,
) -> bool:
    if not is_macaron_base_url(base_url):
        return False
    target = os.environ if env is None else env
    target.setdefault(MACARON_ATTRIBUTION_HEADER_ENV, MACARON_ATTRIBUTION_HEADER_VALUE)
    return True


def ensure_reasoning_effort_none(
    base_url: str | None,
    env: MutableMapping[str, str] | None = None,
    *,
    env_prefix: str = "OPENAI_COMPAT",
) -> bool:
    if not requires_reasoning_effort_none(base_url):
        return False
    target = os.environ if env is None else env
    target.setdefault(f"{env_prefix}_REASONING_EFFORT", "none")
    target.setdefault(f"{env_prefix}_ENABLE_THINKING", "false")
    return True


@dataclass(frozen=True)
class ProviderSpec:
    """Provider settings for both Pi and Claude Code harnesses."""

    name: str
    env_prefix: str
    api_key_env: str
    base_url_env: str
    model_env: str
    provider_api_env: str
    default_provider_api: str = "openai-completions"
    pi_openai_compat: dict[str, Any] = field(default_factory=dict)
    pi_auth_header: bool = True
    pi_model_reasoning: bool = False
    default_api_key: str | None = None
    default_base_url: str | None = None
    default_model: str | None = None
    default_context_window: int = 128000
    default_max_tokens: int = 32000
    anthropic_base_url_env: str | None = None
    default_anthropic_base_url: str | None = None

    @property
    def model_name(self) -> str:
        return f"{self.name}/{self.model}"

    @property
    def model(self) -> str:
        value = os.getenv(self.model_env)
        if value is not None and value.strip():
            return value.strip()
        if self.default_model is not None:
            return self.default_model
        return os.environ[self.model_env]

    @property
    def base_url(self) -> str:
        value = os.getenv(self.base_url_env)
        if value is not None and value.strip():
            return value.strip()
        if self.default_base_url is not None:
            return self.default_base_url
        return os.environ[self.base_url_env]

    @property
    def anthropic_base_url(self) -> str:
        env_name = self.anthropic_base_url_env
        if not env_name:
            raise ValueError(f"Provider {self.name!r} does not support Claude Code.")
        value = os.getenv(env_name)
        if value is not None and value.strip():
            return value.strip()
        if self.default_anthropic_base_url is not None:
            return self.default_anthropic_base_url
        return os.environ[env_name]

    @property
    def provider_api(self) -> str:
        value = os.getenv(self.provider_api_env)
        if value is None or not value.strip():
            return self.default_provider_api
        return value.strip()

    def supports_claude_code(self) -> bool:
        return bool(self.anthropic_base_url_env and self.default_anthropic_base_url)

    def required_env(self, *, agent: str = "pi") -> list[str]:
        if self.default_api_key is None:
            required = [self.api_key_env]
        else:
            required = []

        if agent in {"claude", "claude-code", "claude_sdk", "claude-sdk"}:
            if not self.supports_claude_code():
                raise ValueError(
                    f"Provider {self.name!r} does not define a Claude Code endpoint."
                )
            if (
                self.anthropic_base_url_env
                and self.default_anthropic_base_url is None
            ):
                required.append(self.anthropic_base_url_env)
            if self.default_model is None:
                required.append(self.model_env)
            return required

        if self.default_base_url is None:
            required.append(self.base_url_env)
        if self.default_model is None:
            required.append(self.model_env)
        return required

    def env_mapping(self, *, agent: str = "pi") -> dict[str, str]:
        if agent in {"claude", "claude-code", "claude_sdk", "claude-sdk"}:
            return self.claude_env_mapping()

        include_macaron_env = ensure_macaron_attribution_header(self.base_url)
        mapping = {
            self.base_url_env: f"${{{self.base_url_env}}}",
            self.model_env: f"${{{self.model_env}}}",
        }
        if self.default_api_key is None or os.environ.get(self.api_key_env):
            mapping[self.api_key_env] = f"${{{self.api_key_env}}}"
        if include_macaron_env:
            mapping[MACARON_ATTRIBUTION_HEADER_ENV] = (
                f"${{{MACARON_ATTRIBUTION_HEADER_ENV}}}"
            )
        return mapping

    def claude_env_mapping(self) -> dict[str, str]:
        if not self.anthropic_base_url_env:
            raise ValueError(f"Provider {self.name!r} does not support Claude Code.")
        ensure_macaron_attribution_header(self.anthropic_base_url)
        mapping = {
            self.anthropic_base_url_env: f"${{{self.anthropic_base_url_env}}}",
            self.model_env: f"${{{self.model_env}}}",
            MACARON_ATTRIBUTION_HEADER_ENV: f"${{{MACARON_ATTRIBUTION_HEADER_ENV}}}",
        }
        if self.default_api_key is None or os.environ.get(self.api_key_env):
            mapping[self.api_key_env] = f"${{{self.api_key_env}}}"
        if os.environ.get("CLAUDE_CODE_MAX_OUTPUT_TOKENS"):
            mapping["CLAUDE_CODE_MAX_OUTPUT_TOKENS"] = (
                "${CLAUDE_CODE_MAX_OUTPUT_TOKENS}"
            )
        return mapping


def _bool_env(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _base_openai_compat() -> dict[str, Any]:
    return {
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


def _openai_compat_from_env(prefix: str) -> dict[str, Any]:
    compat = _base_openai_compat()
    thinking_as_text_env = f"{prefix}_THINKING_AS_TEXT"
    thinking_format_env = f"{prefix}_THINKING_FORMAT"
    reasoning_effort_env = f"{prefix}_REASONING_EFFORT"
    enable_thinking_env = f"{prefix}_ENABLE_THINKING"
    if thinking_as_text_env in os.environ:
        compat["requiresThinkingAsText"] = _bool_env(thinking_as_text_env)
    thinking_format = os.getenv(thinking_format_env)
    if thinking_format:
        compat["thinkingFormat"] = thinking_format
    reasoning_effort = os.getenv(reasoning_effort_env)
    enable_thinking = os.getenv(enable_thinking_env)
    if reasoning_effort:
        compat["supportsReasoningEffort"] = True
        compat["reasoningEffort"] = reasoning_effort
        compat["defaultReasoningEffort"] = reasoning_effort
    elif requires_reasoning_effort_none(os.getenv(f"{prefix}_BASE_URL")):
        compat["supportsReasoningEffort"] = True
        compat["reasoningEffort"] = "none"
        compat["defaultReasoningEffort"] = "none"
    if enable_thinking is not None:
        compat["enableThinking"] = _bool_env(enable_thinking_env)
    elif requires_reasoning_effort_none(os.getenv(f"{prefix}_BASE_URL")):
        compat["enableThinking"] = False
    return compat


def _spec(
    *,
    name: str,
    env_prefix: str,
    default_provider_api: str,
    compat: dict[str, Any],
    default_base_url: str | None = None,
    default_model: str | None = None,
    default_context_window: int = 128000,
    default_max_tokens: int = 32000,
    anthropic_base_url_env: str | None = None,
    default_anthropic_base_url: str | None = None,
) -> ProviderSpec:
    return ProviderSpec(
        name=name,
        env_prefix=env_prefix,
        api_key_env=f"{env_prefix}_API_KEY",
        base_url_env=f"{env_prefix}_BASE_URL",
        model_env=f"{env_prefix}_MODEL",
        provider_api_env=f"{env_prefix}_API",
        default_provider_api=default_provider_api,
        pi_openai_compat=compat,
        default_base_url=default_base_url,
        default_model=default_model,
        default_context_window=default_context_window,
        default_max_tokens=default_max_tokens,
        anthropic_base_url_env=anthropic_base_url_env,
        default_anthropic_base_url=default_anthropic_base_url,
    )


def normalize_provider_name(name: str) -> str:
    provider = name.strip().lower()
    if provider == "marcron":
        return "macaron"
    if provider in {
        "openai-compatible",
        "openai_compat",
        "openai_compatible",
        "compat",
    }:
        return "openai"
    return provider


def _glm_openai_compat(prefix: str) -> dict[str, Any]:
    compat = _openai_compat_from_env(prefix)
    compat.setdefault("requiresThinkingAsText", _bool_env(f"{prefix}_THINKING_AS_TEXT", True))
    compat.setdefault("thinkingFormat", os.getenv(f"{prefix}_THINKING_FORMAT", "zai"))
    compat.setdefault("supportsReasoningEffort", True)
    compat.setdefault("reasoningEffort", os.getenv(f"{prefix}_REASONING_EFFORT", "none"))
    compat.setdefault("defaultReasoningEffort", compat["reasoningEffort"])
    compat.setdefault("enableThinking", _bool_env(f"{prefix}_ENABLE_THINKING", False))
    return compat


def resolve_provider(name: str) -> ProviderSpec:
    provider = normalize_provider_name(name)

    if provider == "openai":
        return _spec(
            name="openai",
            env_prefix="OPENAI_COMPAT",
            default_provider_api="openai-completions",
            compat=_openai_compat_from_env("OPENAI_COMPAT"),
        )

    if provider == "tinker":
        compat = _openai_compat_from_env("TINKER")
        compat.update(
            {
                "requiresThinkingAsText": True,
                "thinkingFormat": os.getenv("TINKER_THINKING_FORMAT", "zai"),
            }
        )
        return _spec(
            name="tinker",
            env_prefix="TINKER",
            default_provider_api="openai-completions",
            compat=compat,
        )

    if provider == "novita":
        return _spec(
            name="novita",
            env_prefix="NOVITA",
            default_provider_api="openai-completions",
            compat=_glm_openai_compat("NOVITA"),
            default_base_url="https://api.novita.ai/v3/openai",
            default_model="zai-org/glm-5.2",
            anthropic_base_url_env="NOVITA_ANTHROPIC_BASE_URL",
            default_anthropic_base_url="https://api.novita.ai/anthropic",
        )

    if provider == "macaron":
        return _spec(
            name="macaron",
            env_prefix="MACARON",
            default_provider_api="openai-responses",
            compat=_glm_openai_compat("MACARON"),
            default_base_url="https://pi-api-cn.macaron.xin/v1",
            default_model="glm-5.2",
            default_context_window=200000,
            anthropic_base_url_env="MACARON_ANTHROPIC_BASE_URL",
            default_anthropic_base_url="https://pi-api.macaron.xin/anthropic",
        )

    if provider == "sglang":
        return _spec(
            name="sglang",
            env_prefix="SGLANG",
            default_provider_api="openai-completions",
            compat=_glm_openai_compat("SGLANG"),
            default_base_url="http://34.201.124.250:39606/v1",
            default_model="zai-org/GLM-5.2-FP8",
            anthropic_base_url_env="SGLANG_ANTHROPIC_BASE_URL",
            default_anthropic_base_url="http://34.201.124.250:39606",
        )

    if provider == "sglang_qwen":
        return _spec(
            name="sglang_qwen",
            env_prefix="SGLANG_QWEN",
            default_provider_api="openai-completions",
            compat=_glm_openai_compat("SGLANG_QWEN"),
            default_base_url="http://123.57.26.97:7997/v1",
            default_model="qwen3.6-35b-a3b",
            anthropic_base_url_env="SGLANG_QWEN_ANTHROPIC_BASE_URL",
            default_anthropic_base_url="http://123.57.26.97:7997",
        )

    raise ValueError(
        f"Unsupported provider {name!r}. Supported providers are: "
        + ", ".join(SUPPORTED_PROVIDER_CHOICES)
    )


def _nonempty(value: str | None) -> str | None:
    if value is None:
        return None
    value = value.strip()
    return value or None


def configure_provider_env(
    name: str,
    env: MutableMapping[str, str],
    *,
    api_key: str | None = None,
    base_url: str | None = None,
    model: str | None = None,
    provider_api: str | None = None,
) -> ProviderSpec:
    """Resolve one provider profile and materialize it in a child environment."""

    provider = resolve_provider(name)
    env["LLM_PROVIDER"] = provider.name

    resolved_base_url = (
        _nonempty(base_url)
        or _nonempty(env.get(provider.base_url_env))
        or provider.default_base_url
    )
    resolved_model = (
        _nonempty(model)
        or _nonempty(env.get(provider.model_env))
        or provider.default_model
    )
    resolved_api_key = (
        _nonempty(api_key)
        or _nonempty(env.get(provider.api_key_env))
        or provider.default_api_key
    )
    resolved_provider_api = (
        _nonempty(provider_api)
        or _nonempty(env.get(provider.provider_api_env))
        or provider.default_provider_api
    )

    if resolved_base_url is not None:
        env[provider.base_url_env] = resolved_base_url
    if resolved_model is not None:
        env[provider.model_env] = resolved_model
    if resolved_api_key is not None:
        env[provider.api_key_env] = resolved_api_key
    env[provider.provider_api_env] = resolved_provider_api
    env.setdefault(
        f"{provider.env_prefix}_CONTEXT_WINDOW",
        str(provider.default_context_window),
    )
    env.setdefault(
        f"{provider.env_prefix}_MAX_TOKENS",
        str(provider.default_max_tokens),
    )

    ensure_macaron_attribution_header(resolved_base_url, env)
    if provider.name in {"novita", "macaron", "sglang", "sglang_qwen"}:
        env.setdefault(f"{provider.env_prefix}_REASONING_EFFORT", "none")
        env.setdefault(f"{provider.env_prefix}_ENABLE_THINKING", "false")
    ensure_reasoning_effort_none(
        resolved_base_url,
        env,
        env_prefix=provider.env_prefix,
    )
    return provider
