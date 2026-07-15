#!/usr/bin/env python
import json
import os
import sys
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from dotenv import load_dotenv


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from providers import (
    ProviderSpec,
    configure_provider_env,
    ensure_macaron_attribution_header,
    requires_reasoning_effort_none,
)


DEFAULT_RESPONSES_MAX_OUTPUT_TOKENS = 256


def _request(provider: ProviderSpec, path: str, payload: dict) -> dict:
    api_key = os.getenv(provider.api_key_env)
    base_url = os.getenv(provider.base_url_env, "").rstrip("/")
    if not api_key:
        raise ValueError(f"Missing {provider.api_key_env}")
    if not base_url:
        raise ValueError(f"Missing {provider.base_url_env}")
    ensure_macaron_attribution_header(base_url)
    if requires_reasoning_effort_none(base_url) and path.endswith("/chat/completions"):
        payload.setdefault("reasoning_effort", "none")
        payload.setdefault("enable_thinking", False)

    request = Request(
        f"{base_url}{path}",
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    with urlopen(request, timeout=120) as response:
        body = response.read().decode("utf-8", errors="replace")
        return {"status": response.status, "body": json.loads(body)}


def _responses_text(body: dict) -> str:
    output_text = body.get("output_text")
    if isinstance(output_text, str) and output_text.strip():
        return output_text.strip()

    parts: list[str] = []
    for item in body.get("output") or []:
        if not isinstance(item, dict):
            continue
        for content in item.get("content") or []:
            if not isinstance(content, dict):
                continue
            if content.get("type") != "output_text":
                continue
            text = content.get("text")
            if isinstance(text, str) and text.strip():
                parts.append(text.strip())
    return "\n".join(parts)


def _result_text(result: dict, provider_api: str) -> str:
    if result.get("status") != 200:
        raise ValueError(f"unexpected HTTP status {result.get('status')!r}")

    body = result.get("body")
    if not isinstance(body, dict):
        raise ValueError("response body is not a JSON object")
    if provider_api == "openai-responses":
        response_status = body.get("status")
        if response_status != "completed":
            raise ValueError(
                f"Responses request did not complete: status={response_status!r}"
            )
        text = _responses_text(body)
    else:
        choices = body.get("choices") or []
        text = (choices[0].get("message") or {}).get("content") if choices else ""

    if not isinstance(text, str) or not text.strip():
        raise ValueError("provider returned no output text")
    return text.strip()


def main() -> int:
    load_dotenv(ROOT / ".env", override=False)
    try:
        provider = configure_provider_env(
            os.getenv("LLM_PROVIDER", "openai"),
            os.environ,
        )
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2

    model = os.getenv(provider.model_env, "")
    provider_api = (
        os.getenv(
            provider.provider_api_env,
            provider.default_provider_api,
        )
        .strip()
        .lower()
    )
    if not model:
        print(f"Missing {provider.model_env}", file=sys.stderr)
        return 2

    try:
        if provider_api == "openai-responses":
            result = _request(
                provider,
                "/responses",
                {
                    "model": model,
                    "input": [{"role": "user", "content": "Reply with exactly: ok"}],
                    "max_output_tokens": DEFAULT_RESPONSES_MAX_OUTPUT_TOKENS,
                    "store": False,
                },
            )
        else:
            result = _request(
                provider,
                "/chat/completions",
                {
                    "model": model,
                    "messages": [{"role": "user", "content": "Reply with exactly: ok"}],
                    "max_tokens": 16,
                    "temperature": 0,
                },
            )
        text = _result_text(result, provider_api)
    except HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        print(f"HTTP {exc.code}: {body[:1000]}", file=sys.stderr)
        return 1
    except URLError as exc:
        print(f"Request failed: {exc}", file=sys.stderr)
        return 1
    except (TypeError, ValueError) as exc:
        print(f"Invalid provider response: {exc}", file=sys.stderr)
        return 1

    print(
        f"OK provider={provider.name} provider_api={provider_api} "
        f"model={model} status={result['status']}"
    )
    print(f"text={text[:300]!r}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
