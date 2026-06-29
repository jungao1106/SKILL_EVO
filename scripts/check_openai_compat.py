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

from providers import ensure_macaron_attribution_header


def _request(path: str, payload: dict) -> dict:
    api_key = os.getenv("OPENAI_COMPAT_API_KEY")
    base_url = os.getenv("OPENAI_COMPAT_BASE_URL", "").rstrip("/")
    if not api_key:
        raise SystemExit("Missing OPENAI_COMPAT_API_KEY")
    if not base_url:
        raise SystemExit("Missing OPENAI_COMPAT_BASE_URL")
    ensure_macaron_attribution_header(base_url)
    if "macaron" in base_url.lower() and path.endswith("/chat/completions"):
        payload.setdefault("reasoning_effort", "none")

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


def main() -> int:
    load_dotenv(ROOT / ".env", override=True)
    model = os.getenv("OPENAI_COMPAT_MODEL", "")
    provider_api = os.getenv("OPENAI_COMPAT_API", "openai-completions")
    if not model:
        print("Missing OPENAI_COMPAT_MODEL", file=sys.stderr)
        return 2

    try:
        if provider_api == "openai-responses":
            result = _request(
                "/responses",
                {
                    "model": model,
                    "input": [{"role": "user", "content": "Reply with exactly: ok"}],
                    "max_output_tokens": 16,
                },
            )
            text = json.dumps(result["body"], ensure_ascii=False)[:500]
        else:
            result = _request(
                "/chat/completions",
                {
                    "model": model,
                    "messages": [{"role": "user", "content": "Reply with exactly: ok"}],
                    "max_tokens": 16,
                    "temperature": 0,
                },
            )
            choices = result["body"].get("choices") or []
            text = (choices[0].get("message") or {}).get("content") if choices else ""
    except HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        print(f"HTTP {exc.code}: {body[:1000]}", file=sys.stderr)
        return 1
    except URLError as exc:
        print(f"Request failed: {exc}", file=sys.stderr)
        return 1

    print(f"OK provider_api={provider_api} model={model} status={result['status']}")
    if text:
        print(f"text={text[:300]!r}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
