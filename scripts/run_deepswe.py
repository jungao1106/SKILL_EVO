#!/usr/bin/env python
import os
import sys
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
REFERENCE_DEEPSWE_TASKS = Path(
    "/vePFS-Mindverse/user/intern/jungao/Marcronv1-Coding/deep-swe/tasks"
)
LOCAL_DEEPSWE_TASKS = ROOT / "deep-swe" / "tasks"
DEEPSWE_DEFAULTS = (
    ("--override-cpus", "E2B_OVERRIDE_CPUS", "2"),
    ("--override-memory-mb", "E2B_OVERRIDE_MEMORY_MB", "8192"),
    ("--override-storage-mb", "E2B_OVERRIDE_STORAGE_MB", "20480"),
    ("--max-retries", "HARBOR_MAX_RETRIES", "3"),
    ("--e2b-sandbox-timeout-sec", "E2B_SANDBOX_TIMEOUT_SEC", "14400"),
    ("--verifier-buffer-sec", "VERIFIER_BUFFER_SEC", "4800"),
)


def _default_deepswe_dataset() -> str:
    env_value = os.getenv("DEEPSWE_TASKS") or os.getenv("DEEPSWE_DATASET")
    if env_value:
        return env_value
    if REFERENCE_DEEPSWE_TASKS.exists():
        return str(REFERENCE_DEEPSWE_TASKS)
    return str(LOCAL_DEEPSWE_TASKS)


def _has_option(argv: list[str], option: str) -> bool:
    return option in argv or any(item.startswith(f"{option}=") for item in argv)


def _option_value(argv: list[str], *options: str) -> str | None:
    for index, item in enumerate(argv):
        for option in options:
            if item == option and index + 1 < len(argv):
                return argv[index + 1]
            if item.startswith(f"{option}="):
                return item.split("=", 1)[1]
    return None


def build_deepswe_argv(argv: list[str]) -> list[str]:
    injected: list[str] = []
    if not _has_option(argv, "--dataset"):
        injected.extend(["--dataset", _default_deepswe_dataset()])
    if not _has_option(argv, "--benchmark-name"):
        injected.extend(["--benchmark-name", "deepswe"])
    if not _has_option(argv, "--job-name") and not os.getenv("JOB_NAME"):
        agent = (
            _option_value(argv, "--agent", "--harness")
            or
            os.getenv("BENCHMARK_AGENT")
            or os.getenv("HARBOR_AGENT")
            or "pi"
        ).replace("-", "_")
        provider = _option_value(argv, "--provider") or os.getenv(
            "LLM_PROVIDER", "novita"
        )
        use_skills = "--use-skills" in argv or (
            "--no-skills" not in argv
            and os.getenv("PI_USE_SKILLS", "").strip().lower()
            in {"1", "true", "yes", "on"}
        )
        skill_suffix = "skills" if use_skills else "noskills"
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S_%f")
        injected.extend(
            [
                "--job-name",
                f"{agent}_{provider}_deepswe_{skill_suffix}_{timestamp}",
            ]
        )
    if not _has_option(argv, "--resume-existing"):
        injected.append("--resume-existing")
    if not _has_option(argv, "--force-agent-internet"):
        injected.append("--force-agent-internet")
    for option, env_name, default in DEEPSWE_DEFAULTS:
        if not _has_option(argv, option) and not os.getenv(env_name):
            injected.extend([option, default])
    return [*injected, *argv]


def main() -> None:
    argv = build_deepswe_argv(sys.argv[1:])

    sys.argv = [sys.argv[0], *argv]
    from scripts.run_benchmark import main as run_benchmark_main

    run_benchmark_main()


if __name__ == "__main__":
    main()
