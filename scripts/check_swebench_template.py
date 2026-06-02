#!/usr/bin/env python
import argparse
import asyncio
import os
import sys
from pathlib import Path
from typing import Any

from dotenv import load_dotenv


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.run_benchmark import build_config


def _apply_provider_defaults(args: argparse.Namespace) -> None:
    os.environ["LLM_PROVIDER"] = args.provider
    if args.provider == "openai":
        os.environ.setdefault("OPENAI_COMPAT_API_KEY", "template-probe")
        os.environ.setdefault("OPENAI_COMPAT_BASE_URL", "https://example.invalid/v1")
        os.environ.setdefault("OPENAI_COMPAT_MODEL", "template-probe")
        os.environ.setdefault("OPENAI_COMPAT_API", "openai-completions")
    elif args.provider == "tinker":
        os.environ.setdefault("TINKER_API_KEY", "template-probe")
        os.environ.setdefault("TINKER_BASE_URL", "https://example.invalid/v1")
        os.environ.setdefault("TINKER_MODEL", "template-probe")
        os.environ.setdefault("TINKER_API", "openai-completions")


async def _probe(args: argparse.Namespace) -> int:
    if not os.environ.get("E2B_API_KEY"):
        print("Missing E2B_API_KEY; cannot query E2B templates.", file=sys.stderr)
        return 2

    config = build_config(args)
    from harbor.job import Job
    from harbor.trial.trial import Trial

    job = await Job.create(config)
    trial_configs = getattr(job, "_trial_configs", [])
    if not trial_configs:
        print("No trials resolved for template probe.", file=sys.stderr)
        return 1

    any_miss = False
    for trial_config in trial_configs:
        trial = await Trial.create(trial_config)
        environment = getattr(trial, "_environment")
        candidates = environment._candidate_template_names()
        hit = await environment._does_template_exist()
        selected = environment._template_name
        task = getattr(trial, "_task")
        print(f"task={task.name}")
        print(f"template_candidates={', '.join(candidates)}")
        if hit:
            print(f"template_status=hit selected={selected}")
        else:
            any_miss = True
            print(f"template_status=miss will_build={selected}")
    return 1 if any_miss else 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Check whether SWE-Bench Verified E2B task templates already exist."
    )
    parser.add_argument("--dataset", default="swe-bench/swe-bench-verified@2")
    parser.add_argument("--provider", choices=["openai", "tinker"], default="openai")
    parser.add_argument("--job-name", default="template_probe")
    parser.add_argument("--include-task-name", action="append", default=None)
    parser.add_argument("--task-names-file", action="append", default=None)
    parser.add_argument("--n-tasks", type=int, default=1)
    parser.add_argument("--concurrency", type=int, default=1)
    parser.add_argument("--exclude-task-name", action="append", default=None)
    parser.add_argument("--overwrite-tasks", action="store_true")
    parser.add_argument("--force-build", action="store_true")
    parser.add_argument("--keep-sandboxes", action="store_true")
    parser.add_argument("--e2b-template-namespace", default=os.getenv("E2B_TEMPLATE_NAMESPACE", "anchen1011"))
    parser.add_argument("--e2b-pi-template-suffix", default=os.getenv("E2B_PI_TEMPLATE_SUFFIX", "pi_c6d7003a"))
    parser.add_argument("--keep-dockerfile-comments", action="store_true")
    parser.add_argument("--e2b-sandbox-timeout-sec", type=int, default=int(os.getenv("E2B_SANDBOX_TIMEOUT_SEC", "3600")))
    parser.add_argument("--quiet", action="store_true")
    parser.add_argument("--debug", action="store_true")
    parser.add_argument("--use-skills", action="store_true")
    parser.add_argument("--no-skills", dest="use_skills", action="store_false")
    parser.set_defaults(use_skills=False)
    parser.add_argument("--result-only", action="store_true")
    parser.add_argument("--timeout-multiplier", type=float, default=1.0)
    parser.add_argument("--agent-timeout-multiplier", type=float, default=None)
    parser.add_argument("--verifier-timeout-multiplier", type=float, default=None)
    parser.add_argument("--agent-setup-timeout-multiplier", type=float, default=2.0)
    parser.add_argument("--environment-build-timeout-multiplier", type=float, default=2.0)
    parser.add_argument("--agent-setup-timeout-sec", type=float, default=1200)
    parser.add_argument("--agent-timeout-sec", type=float, default=300)
    parser.add_argument("--override-cpus", type=int, default=int(os.getenv("E2B_OVERRIDE_CPUS", "1")))
    parser.add_argument("--override-memory-mb", type=int, default=int(os.getenv("E2B_OVERRIDE_MEMORY_MB", "4096")))
    parser.add_argument("--override-storage-mb", type=int, default=int(os.getenv("E2B_OVERRIDE_STORAGE_MB", "10240")))
    parser.add_argument("--model-context-window", type=int, default=128000)
    parser.add_argument("--model-max-tokens", type=int, default=32000)
    parser.add_argument("--thinking", default=os.getenv("PI_THINKING", "off"))
    parser.add_argument("--tools", default=os.getenv("PI_TOOLS", "read,write,edit,bash,grep,find,ls"))
    return parser.parse_args()


def main() -> None:
    load_dotenv(ROOT / ".env", override=False)
    args = parse_args()
    _apply_provider_defaults(args)
    raise SystemExit(asyncio.run(_probe(args)))


if __name__ == "__main__":
    main()
