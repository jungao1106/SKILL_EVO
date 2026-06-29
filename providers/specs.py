import os
from dataclasses import dataclass, field
from typing import Any, MutableMapping


MACARON_ATTRIBUTION_HEADER_ENV = "CLAUDE_CODE_ATTRIBUTION_HEADER"
MACARON_ATTRIBUTION_HEADER_VALUE = "0"


def is_macaron_base_url(base_url: str | None) -> bool:
    return "macaron" in str(base_url or "").lower()


def ensure_macaron_attribution_header(
    base_url: str | None,
    env: MutableMapping[str, str] | None = None,
) -> bool:
    if not is_macaron_base_url(base_url):
        return False
    target = os.environ if env is None else env
    target.setdefault(MACARON_ATTRIBUTION_HEADER_ENV, MACARON_ATTRIBUTION_HEADER_VALUE)
    return True


@dataclass(frozen=True)
class ProviderSpec:
    """Provider settings for the Pi agent model entry."""

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

    @property
    def model_name(self) -> str:
        return f"{self.name}/{self.model}"

    @property
    def model(self) -> str:
        return os.environ[self.model_env]

    @property
    def base_url(self) -> str:
        return os.environ[self.base_url_env]

    @property
    def provider_api(self) -> str:
        value = os.getenv(self.provider_api_env)
        if value is None or not value.strip():
            return self.default_provider_api
        return value.strip()

    def required_env(self) -> list[str]:
        required = [self.base_url_env, self.model_env]
        if self.default_api_key is None:
            required.insert(0, self.api_key_env)
        return required

    def env_mapping(self) -> dict[str, str]:
        include_macaron_env = ensure_macaron_attribution_header(
            os.getenv(self.base_url_env)
        )
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
    if thinking_as_text_env in os.environ:
        compat["requiresThinkingAsText"] = _bool_env(thinking_as_text_env)
    thinking_format = os.getenv(thinking_format_env)
    if thinking_format:
        compat["thinkingFormat"] = thinking_format
    reasoning_effort = os.getenv(reasoning_effort_env)
    if reasoning_effort:
        compat["supportsReasoningEffort"] = True
        compat["reasoningEffort"] = reasoning_effort
        compat["defaultReasoningEffort"] = reasoning_effort
    elif is_macaron_base_url(os.getenv(f"{prefix}_BASE_URL")):
        compat["supportsReasoningEffort"] = True
        compat["reasoningEffort"] = "none"
        compat["defaultReasoningEffort"] = "none"
    return compat


def _spec(
    *,
    name: str,
    env_prefix: str,
    default_provider_api: str,
    compat: dict[str, Any],
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
    )


def resolve_provider(name: str) -> ProviderSpec:
    provider = name.strip().lower()

    if provider in {"openai", "openai-compatible", "openai_compat", "compat"}:
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

    raise ValueError(
        f"Unsupported provider {name!r}. Supported providers are: openai, tinker."
    )
