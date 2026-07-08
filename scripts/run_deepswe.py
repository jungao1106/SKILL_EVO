#!/usr/bin/env python
import os
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
REFERENCE_DEEPSWE_TASKS = Path(
    "/vePFS-Mindverse/user/intern/jungao/Marcronv1-Coding/deep-swe/tasks"
)
LOCAL_DEEPSWE_TASKS = ROOT / "deep-swe" / "tasks"


def _default_deepswe_dataset() -> str:
    env_value = os.getenv("DEEPSWE_TASKS") or os.getenv("DEEPSWE_DATASET")
    if env_value:
        return env_value
    if REFERENCE_DEEPSWE_TASKS.exists():
        return str(REFERENCE_DEEPSWE_TASKS)
    return str(LOCAL_DEEPSWE_TASKS)


def _has_option(argv: list[str], option: str) -> bool:
    return option in argv or any(item.startswith(f"{option}=") for item in argv)


def main() -> None:
    argv = sys.argv[1:]
    injected: list[str] = []
    if not _has_option(argv, "--dataset"):
        injected.extend(["--dataset", _default_deepswe_dataset()])
    if not _has_option(argv, "--benchmark-name"):
        injected.extend(["--benchmark-name", "deepswe"])
    if not _has_option(argv, "--job-name") and not os.getenv("JOB_NAME"):
        agent = (
            os.getenv("BENCHMARK_AGENT")
            or os.getenv("HARBOR_AGENT")
            or "pi"
        ).replace("-", "_")
        provider = os.getenv("LLM_PROVIDER", "novita")
        injected.extend(["--job-name", f"{agent}_{provider}_deepswe"])

    sys.argv = [sys.argv[0], *injected, *argv]
    from scripts.run_benchmark import main as run_benchmark_main

    run_benchmark_main()


if __name__ == "__main__":
    main()
