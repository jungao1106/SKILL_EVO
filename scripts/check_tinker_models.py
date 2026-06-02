#!/usr/bin/env python
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

from dotenv import load_dotenv


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_BASE_URL = "https://tinker.thinkingmachines.dev/services/tinker-prod/oai/api/v1"
DEFAULT_MODEL = "nvidia/NVIDIA-Nemotron-3-Super-120B-A12B-BF16:peft:262144"


def _request(model: str) -> dict:
    api_key = os.getenv("TINKER_API_KEY")
    if not api_key:
        raise SystemExit("Missing TINKER_API_KEY")
    base_url = os.getenv("TINKER_BASE_URL", DEFAULT_BASE_URL).rstrip("/")

    payload = {
        "model": model,
        "prompt": "Reply with exactly: ok",
        "max_tokens": 8,
        "temperature": 0,
    }
    request = urllib.request.Request(
        f"{base_url}/completions",
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "User-Agent": "OpenAI/Python 1.0",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=180) as response:
            body = response.read().decode("utf-8", errors="replace")
            return {"status": response.status, "body": json.loads(body)}
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        return {"status": exc.code, "error": body[:2000]}


def _summarize(name: str, model: str) -> bool:
    result = _request(model)
    status = result["status"]
    if status >= 300:
        print(f"{name}: FAIL status={status} model={model}")
        print(result.get("error", ""))
        return False

    body = result["body"]
    content = ""
    choices = body.get("choices")
    if choices:
        content = choices[0].get("text") or ""
    usage = body.get("usage") or {}
    print(f"{name}: OK status={status} model={model}")
    print(f"{name}: content={content!r} usage={usage}")
    return True


def main() -> int:
    load_dotenv(ROOT / ".env", override=False)
    print(f"base_url={os.getenv('TINKER_BASE_URL', DEFAULT_BASE_URL).rstrip('/')}")
    checks = [
        _summarize("default", os.getenv("TINKER_MODEL", DEFAULT_MODEL)),
    ]
    if os.getenv("TINKER_SFT_MODEL"):
        checks.append(_summarize("sft", os.environ["TINKER_SFT_MODEL"]))
    return 0 if all(checks) else 1


if __name__ == "__main__":
    sys.exit(main())
