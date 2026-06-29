#!/usr/bin/env python
import argparse
import asyncio
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from dotenv import load_dotenv


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from providers import ProviderSpec, resolve_provider
from scripts.tbench_runtime import ensure_docker_compose, patch_terminal_bench_runtime


DEFAULT_DATASET = "terminal-bench/terminal-bench-2"
DEFAULT_PROVIDER = "openai"


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _log(message: str, *, log_file: Path | None = None) -> None:
    line = f"[{_utc_now()}] {message}"
    print(line, flush=True)
    if log_file is not None:
        log_file.parent.mkdir(parents=True, exist_ok=True)
        with log_file.open("a") as handle:
            handle.write(line + "\n")


def _require_env(names: list[str]) -> None:
    missing = [name for name in names if not os.environ.get(name)]
    if missing:
        raise SystemExit(
            "Missing required environment variables: "
            + ", ".join(missing)
            + f"\nFill them in {ROOT / '.env'} and rerun."
        )


def _provider() -> str:
    return os.getenv("LLM_PROVIDER", DEFAULT_PROVIDER).strip().lower()


def _provider_spec() -> ProviderSpec:
    return resolve_provider(_provider())


def _provider_int_env(provider: ProviderSpec, suffix: str, default: str) -> int:
    return int(os.getenv(f"{provider.env_prefix}_{suffix}", default))


def _float_env(name: str, default: str) -> float:
    return float(os.getenv(name, default))


def _bool_env(name: str, default: str = "false") -> bool:
    return os.getenv(name, default).strip().lower() in {"1", "true", "yes", "on"}


def _optional_float_env(name: str) -> float | None:
    value = os.getenv(name)
    if value is None or value.strip() == "":
        return None
    return float(value)


def _parse_dataset(value: str) -> tuple[str, str | None]:
    if "@" in value:
        name, ref = value.split("@", 1)
        return name, ref
    return value, None


def _read_task_names_file(path: str) -> list[str]:
    task_names: list[str] = []
    task_path = Path(path).expanduser()
    for line in task_path.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            task_names.append(line)
    return task_names


def _agent_config(args: argparse.Namespace, provider: ProviderSpec) -> Any:
    from harbor.models.trial.config import AgentConfig

    return AgentConfig(
        import_path="agents.pi_agent:PiAgent",
        model_name=provider.model_name,
        override_setup_timeout_sec=args.agent_setup_timeout_sec,
        override_timeout_sec=args.agent_timeout_sec,
        kwargs={
            "provider_name": provider.name,
            "api_key_env": provider.api_key_env,
            "base_url_env": provider.base_url_env,
            "model_env": provider.model_env,
            "provider_api": provider.provider_api,
            "model_context_window": args.model_context_window,
            "model_max_tokens": args.model_max_tokens,
            "thinking": args.thinking,
            "tools": args.tools,
            "openai_compat": provider.pi_openai_compat,
            "auth_header": provider.pi_auth_header,
            "model_reasoning": provider.pi_model_reasoning,
            "default_api_key": provider.default_api_key,
            "result_only": args.result_only,
            "use_skills": args.use_skills,
            "benchmark_name": "terminal-bench",
            "require_workspace_change": args.require_workspace_change,
        },
    )


def build_config(args: argparse.Namespace) -> Any:
    from harbor.models.job.config import DatasetConfig, JobConfig
    from harbor.models.metric.config import MetricConfig
    from harbor.models.metric.type import MetricType
    from harbor.models.trial.config import EnvironmentConfig

    provider = _provider_spec()
    dataset = args.dataset.strip()
    dataset_path = Path(dataset).expanduser()
    dataset_kwargs: dict[str, Any] = {
        "n_tasks": args.n_tasks,
        "task_names": args.include_task_name or None,
        "exclude_task_names": args.exclude_task_name or None,
        "overwrite": args.overwrite_tasks,
        "download_dir": ROOT / ".cache" / "harbor_tasks",
    }
    if dataset_path.exists():
        dataset_kwargs["path"] = dataset_path.resolve()
    else:
        dataset_name, dataset_ref = _parse_dataset(dataset)
        dataset_kwargs["name"] = dataset_name
        if dataset_ref:
            dataset_kwargs["ref"] = dataset_ref

    return JobConfig(
        job_name=args.job_name,
        jobs_dir=ROOT / "jobs",
        n_attempts=1,
        n_concurrent_trials=args.concurrency,
        timeout_multiplier=args.timeout_multiplier,
        agent_timeout_multiplier=args.agent_timeout_multiplier,
        verifier_timeout_multiplier=args.verifier_timeout_multiplier,
        agent_setup_timeout_multiplier=args.agent_setup_timeout_multiplier,
        environment_build_timeout_multiplier=args.environment_build_timeout_multiplier,
        quiet=args.quiet,
        debug=args.debug,
        environment=EnvironmentConfig(
            delete=not args.keep_sandboxes,
            force_build=args.force_build,
            override_cpus=args.override_cpus,
            override_memory_mb=args.override_memory_mb,
            override_storage_mb=args.override_storage_mb,
            env={
                "LLM_PROVIDER": "${LLM_PROVIDER}",
                **provider.env_mapping(),
            },
        ),
        agents=[_agent_config(args, provider)],
        datasets=[DatasetConfig(**dataset_kwargs)],
        metrics=[MetricConfig(type=MetricType.MEAN)],
    )


def _trial_reward_summary(result: Any) -> str:
    if result is None or result.verifier_result is None:
        return "reward=<none>"
    rewards = result.verifier_result.rewards
    if not rewards:
        return "reward=<none>"
    return " ".join(f"{key}={value}" for key, value in rewards.items())


async def run_job(args: argparse.Namespace) -> Path:
    from harbor.job import Job

    patch_terminal_bench_runtime(result_only=args.result_only)
    config = build_config(args)
    config_path = ROOT / "configs" / f"{args.job_name}.json"
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(config.model_dump_json(indent=2))

    log_file = ROOT / "logs" / f"{args.job_name}.log"
    log_file.write_text("")
    provider = _provider_spec()
    _log(f"job={args.job_name} dataset={args.dataset}", log_file=log_file)
    _log(
        "env=docker "
        "benchmark=terminal-bench "
        "agent=pi "
        f"provider={provider.name} "
        f"provider_api={provider.provider_api} "
        f"concurrency={args.concurrency} "
        f"model={provider.model_name} "
        f"use_skills={args.use_skills} "
        f"require_workspace_change={args.require_workspace_change} "
        f"result_only={args.result_only}",
        log_file=log_file,
    )
    _log(f"config={config_path}", log_file=log_file)

    job = await Job.create(config)
    _log(f"resolved_trials={len(job)} job_dir={job.job_dir}", log_file=log_file)

    async def on_start(event: Any) -> None:
        _log(f"START trial={event.trial_id} task={event.task_name}", log_file=log_file)

    async def on_environment(event: Any) -> None:
        _log(f"ENV trial={event.trial_id} task={event.task_name}", log_file=log_file)

    async def on_agent(event: Any) -> None:
        _log(f"AGENT trial={event.trial_id} task={event.task_name}", log_file=log_file)

    async def on_verifier(event: Any) -> None:
        _log(f"VERIFY trial={event.trial_id} task={event.task_name}", log_file=log_file)

    async def on_end(event: Any) -> None:
        result = event.result
        status = "ok"
        exc = ""
        if result is not None and result.exception_info is not None:
            status = "error"
            exc = (
                f" exception={result.exception_info.exception_type}: "
                f"{result.exception_info.exception_message}"
            )
        _log(
            f"END trial={event.trial_id} task={event.task_name} status={status} "
            f"{_trial_reward_summary(result)}{exc}",
            log_file=log_file,
        )

    job.on_trial_started(on_start)
    job.on_environment_started(on_environment)
    job.on_agent_started(on_agent)
    job.on_verification_started(on_verifier)
    job.on_trial_ended(on_end)

    result = await job.run()
    _log(
        f"DONE total={result.n_total_trials} errors={result.stats.n_errors} "
        f"result={job.job_dir / 'result.json'}",
        log_file=log_file,
    )
    return job.job_dir


def parse_args() -> argparse.Namespace:
    provider_name = _provider()
    if provider_name not in {"openai", "tinker"}:
        provider_name = DEFAULT_PROVIDER
    provider = resolve_provider(provider_name)
    parser = argparse.ArgumentParser(
        description="Run Terminal-Bench 2.0 through Harbor/Docker with Pi."
    )
    parser.add_argument("--dataset", default=os.getenv("TBENCH_DATASET", DEFAULT_DATASET))
    parser.add_argument("--provider", choices=["openai", "tinker"], default=provider.name)
    parser.add_argument("--job-name", default=os.getenv("TBENCH_JOB_NAME"))
    parser.add_argument("--provider-base-url", default=os.getenv("PROVIDER_BASE_URL"))
    parser.add_argument("--provider-model", default=os.getenv("PROVIDER_MODEL"))
    parser.add_argument("--provider-api-key", default=os.getenv("PROVIDER_API_KEY"))
    parser.add_argument("--provider-api", default=os.getenv("PROVIDER_API"))
    parser.add_argument("--concurrency", type=int, default=int(os.getenv("TBENCH_CONCURRENCY", "1")))
    n_tasks_env = os.getenv("TBENCH_N_TASKS")
    parser.add_argument("--n-tasks", type=int, default=int(n_tasks_env) if n_tasks_env else None)
    parser.add_argument("--include-task-name", action="append", default=None)
    parser.add_argument("--task-names-file", action="append", default=None)
    parser.add_argument("--exclude-task-name", action="append", default=None)
    parser.add_argument("--overwrite-tasks", action="store_true")
    parser.add_argument("--force-build", action="store_true")
    parser.add_argument("--keep-sandboxes", action="store_true")
    parser.add_argument("--quiet", action="store_true")
    parser.add_argument("--debug", action="store_true")
    skills_group = parser.add_mutually_exclusive_group()
    skills_group.add_argument(
        "--use-skills",
        action="store_true",
        default=_bool_env("TBENCH_USE_SKILLS", "false"),
    )
    skills_group.add_argument("--no-skills", dest="use_skills", action="store_false")
    workspace_group = parser.add_mutually_exclusive_group()
    workspace_group.add_argument(
        "--require-workspace-change",
        action="store_true",
        default=_bool_env("TBENCH_REQUIRE_WORKSPACE_CHANGE", "false"),
        help="Require a git diff before verifier. Off by default for Terminal-Bench.",
    )
    workspace_group.add_argument(
        "--no-require-workspace-change",
        dest="require_workspace_change",
        action="store_false",
    )
    parser.add_argument("--result-only", action="store_true", default=_bool_env("RESULT_ONLY"))
    parser.add_argument("--timeout-multiplier", type=float, default=_float_env("TIMEOUT_MULTIPLIER", "1.0"))
    parser.add_argument("--agent-timeout-multiplier", type=float, default=_optional_float_env("AGENT_TIMEOUT_MULTIPLIER"))
    parser.add_argument("--verifier-timeout-multiplier", type=float, default=_optional_float_env("VERIFIER_TIMEOUT_MULTIPLIER"))
    parser.add_argument("--agent-setup-timeout-multiplier", type=float, default=_float_env("AGENT_SETUP_TIMEOUT_MULTIPLIER", "2.0"))
    parser.add_argument("--environment-build-timeout-multiplier", type=float, default=_float_env("ENVIRONMENT_BUILD_TIMEOUT_MULTIPLIER", "2.0"))
    parser.add_argument("--agent-setup-timeout-sec", type=float, default=_float_env("AGENT_SETUP_TIMEOUT_SEC", "1200"))
    parser.add_argument("--agent-timeout-sec", type=float, default=_optional_float_env("AGENT_TIMEOUT_SEC"))
    parser.add_argument("--override-cpus", type=int, default=None)
    parser.add_argument("--override-memory-mb", type=int, default=None)
    parser.add_argument("--override-storage-mb", type=int, default=None)
    parser.add_argument("--model-context-window", type=int, default=None)
    parser.add_argument("--model-max-tokens", type=int, default=None)
    parser.add_argument("--thinking", default=os.getenv("PI_THINKING", "off"))
    parser.add_argument("--tools", default=os.getenv("PI_TOOLS", "read,write,edit,bash,grep,find,ls"))
    args = parser.parse_args()

    task_names = list(args.include_task_name or [])
    for task_names_file in args.task_names_file or []:
        task_names.extend(_read_task_names_file(task_names_file))
    args.include_task_name = list(dict.fromkeys(task_names)) or None

    selected_provider = resolve_provider(args.provider)
    if args.job_name is None:
        skill_suffix = "skills" if args.use_skills else "noskills"
        args.job_name = f"tbench_pi_{selected_provider.name}_{skill_suffix}"
    if args.model_context_window is None:
        args.model_context_window = _provider_int_env(selected_provider, "CONTEXT_WINDOW", "128000")
    if args.model_max_tokens is None:
        args.model_max_tokens = _provider_int_env(selected_provider, "MAX_TOKENS", "32000")
    return args


def _apply_provider_overrides(args: argparse.Namespace) -> None:
    provider = resolve_provider(args.provider)
    os.environ["LLM_PROVIDER"] = provider.name
    if args.provider_base_url:
        os.environ[provider.base_url_env] = args.provider_base_url
    if args.provider_model:
        os.environ[provider.model_env] = args.provider_model
    if args.provider_api_key:
        os.environ[provider.api_key_env] = args.provider_api_key
    if args.provider_api:
        os.environ[provider.provider_api_env] = args.provider_api
    if provider.default_api_key and not os.environ.get(provider.api_key_env):
        os.environ[provider.api_key_env] = provider.default_api_key


def main() -> None:
    load_dotenv(ROOT / ".env", override=False)
    args = parse_args()
    ensure_docker_compose(ROOT)
    _apply_provider_overrides(args)
    provider = _provider_spec()
    _require_env(["LLM_PROVIDER", *provider.required_env()])
    asyncio.run(run_job(args))


if __name__ == "__main__":
    main()
