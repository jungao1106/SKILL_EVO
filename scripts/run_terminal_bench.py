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

from providers import ProviderSpec, ensure_macaron_attribution_header, resolve_provider
from scripts.run_benchmark import _trial_reward_summary
from scripts.tbench_runtime import ensure_docker_compose, patch_terminal_bench_runtime


DEFAULT_DATASET = "terminal-bench/terminal-bench-2"
DEFAULT_DATASET_REF = "latest"
DEFAULT_PROVIDER = "openai"
DEFAULT_ENVIRONMENT = "e2b"
DEFAULT_DOCKER_CPUS = 2
DEFAULT_DOCKER_MEMORY_MB = 8192
DEFAULT_DOCKER_STORAGE_MB = 20480


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


def _optional_float_env(name: str) -> float | None:
    value = os.getenv(name)
    if value is None or value.strip() == "":
        return None
    return float(value)


def _bool_env(name: str, default: str = "false") -> bool:
    return os.getenv(name, default).strip().lower() in {"1", "true", "yes", "on"}


def _read_task_names_file(path: str) -> list[str]:
    task_names: list[str] = []
    task_path = Path(path).expanduser()
    for line in task_path.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            task_names.append(line)
    return task_names


def _parse_dataset(value: str) -> tuple[str, str | None]:
    value = value.strip()
    if "@" in value:
        name, ref = value.split("@", 1)
        return name, ref or None
    return value, None


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
        "task_names": args.include_task_name or None,
        "exclude_task_names": args.exclude_task_name or None,
        "n_tasks": args.n_tasks,
        "overwrite": args.overwrite_tasks,
        "download_dir": args.cache_dir,
    }
    if dataset_path.exists():
        dataset_kwargs["path"] = dataset_path.resolve()
    else:
        dataset_name, dataset_ref = _parse_dataset(dataset)
        dataset_kwargs["name"] = dataset_name
        if "/" in dataset_name:
            dataset_kwargs["ref"] = dataset_ref or args.dataset_ref or DEFAULT_DATASET_REF
        elif dataset_ref:
            dataset_kwargs["version"] = dataset_ref
        elif args.dataset_ref:
            dataset_kwargs["version"] = args.dataset_ref

    environment_config_kwargs: dict[str, Any] = {}
    environment_kwargs: dict[str, Any] = {}
    if args.environment == "e2b":
        environment_config_kwargs["import_path"] = (
            "environments.e2b_swebench:E2BSwebenchEnvironment"
        )
        environment_kwargs.update(
            {
                "template_namespace": args.e2b_template_namespace,
                "pi_template_suffix": args.e2b_pi_template_suffix,
                "strip_dockerfile_comments": not args.keep_dockerfile_comments,
                "sandbox_timeout_sec": args.e2b_sandbox_timeout_sec,
            }
        )
    else:
        from harbor.models.environment_type import EnvironmentType

        environment_config_kwargs["type"] = EnvironmentType(args.environment)

    if args.environment_kwarg:
        for item in args.environment_kwarg:
            key, value = item.split("=", 1)
            environment_kwargs[key] = value

    return JobConfig(
        job_name=args.job_name,
        jobs_dir=args.jobs_dir,
        n_attempts=args.n_attempts,
        n_concurrent_trials=args.concurrency,
        timeout_multiplier=args.timeout_multiplier,
        agent_timeout_multiplier=args.agent_timeout_multiplier,
        verifier_timeout_multiplier=args.verifier_timeout_multiplier,
        agent_setup_timeout_multiplier=args.agent_setup_timeout_multiplier,
        environment_build_timeout_multiplier=args.environment_build_timeout_multiplier,
        quiet=args.quiet,
        debug=args.debug,
        environment=EnvironmentConfig(
            **environment_config_kwargs,
            force_build=args.force_build,
            delete=not args.keep_sandboxes,
            override_cpus=args.override_cpus,
            override_memory_mb=args.override_memory_mb,
            override_storage_mb=args.override_storage_mb,
            override_gpus=args.override_gpus,
            env={
                "LLM_PROVIDER": "${LLM_PROVIDER}",
                **provider.env_mapping(),
            },
            kwargs=environment_kwargs,
        ),
        agents=[_agent_config(args, provider)],
        datasets=[DatasetConfig(**dataset_kwargs)],
        metrics=[MetricConfig(type=MetricType.MEAN)],
    )


async def run_job(args: argparse.Namespace) -> Path:
    from harbor.job import Job

    patch_terminal_bench_runtime(result_only=args.result_only)

    config = build_config(args)
    config_path = ROOT / "configs" / f"{args.job_name}.json"
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(config.model_dump_json(indent=2))

    log_file = ROOT / "logs" / f"{args.job_name}.log"
    log_file.write_text("")
    os.environ["SKILL_EVO_BENCHMARK_LOG"] = str(log_file)

    provider = _provider_spec()
    e2b_detail = ""
    if args.environment == "e2b":
        e2b_detail = (
            f"namespace={args.e2b_template_namespace} "
            f"pi_template_suffix={args.e2b_pi_template_suffix or '<disabled>'} "
            f"sandbox_timeout_sec={min(args.e2b_sandbox_timeout_sec, 3600)} "
        )
    _log(f"job={args.job_name} dataset={args.dataset}", log_file=log_file)
    _log(
        "benchmark=terminal-bench-2 "
        f"env={args.environment} "
        "agent=pi "
        f"provider={provider.name} "
        f"provider_api={provider.provider_api} "
        f"{e2b_detail}"
        f"concurrency={args.concurrency} "
        f"n_attempts={args.n_attempts} "
        f"cpus={args.override_cpus} "
        f"memory_mb={args.override_memory_mb} "
        f"storage_mb={args.override_storage_mb} "
        f"model={provider.model_name} "
        f"use_skills={args.use_skills} "
        f"result_only={args.result_only}",
        log_file=log_file,
    )
    _log(f"config={config_path}", log_file=log_file)

    if args.dry_run:
        _log("dry_run=true; wrote config without launching Harbor job", log_file=log_file)
        return args.jobs_dir / args.job_name

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
        description="Run Terminal-Bench 2.0 through Harbor with Pi."
    )
    parser.add_argument(
        "--dataset",
        default=os.getenv("TERMINAL_BENCH_DATASET", DEFAULT_DATASET),
        help="Harbor dataset package, registry name, or local dataset path.",
    )
    parser.add_argument(
        "--dataset-ref",
        default=os.getenv("TERMINAL_BENCH_DATASET_REF"),
        help="Dataset ref/version when --dataset does not include @ref.",
    )
    parser.add_argument(
        "--provider",
        choices=["openai", "tinker"],
        default=provider.name,
        help="Provider profile used by Pi.",
    )
    parser.add_argument("--job-name", default=os.getenv("TERMINAL_BENCH_JOB_NAME"))
    parser.add_argument(
        "--provider-base-url",
        default=os.getenv("PROVIDER_BASE_URL"),
        help="Override the selected provider BASE_URL for this run.",
    )
    parser.add_argument(
        "--provider-model",
        default=os.getenv("PROVIDER_MODEL"),
        help="Override the selected provider MODEL for this run.",
    )
    parser.add_argument(
        "--provider-api-key",
        default=os.getenv("PROVIDER_API_KEY"),
        help="Override the selected provider API_KEY for this run.",
    )
    parser.add_argument(
        "--provider-api",
        default=os.getenv("PROVIDER_API"),
        help="Pi provider API shape, for example openai-completions or openai-responses.",
    )
    parser.add_argument(
        "--environment",
        choices=[
            "docker",
            "daytona",
            "e2b",
            "modal",
            "runloop",
            "gke",
            "apple-container",
        ],
        default=os.getenv("TERMINAL_BENCH_ENVIRONMENT", DEFAULT_ENVIRONMENT),
    )
    parser.add_argument(
        "--environment-kwarg",
        action="append",
        default=None,
        metavar="KEY=VALUE",
        help="Pass a raw environment kwargs entry to Harbor. Repeatable.",
    )
    parser.add_argument(
        "--e2b-template-namespace",
        default=os.getenv("E2B_TEMPLATE_NAMESPACE", "anchen1011"),
        help="E2B team namespace used for template names.",
    )
    parser.add_argument(
        "--e2b-pi-template-suffix",
        default=os.getenv("E2B_PI_TEMPLATE_SUFFIX", "pi_c6d7003a"),
        help="Prefer prebuilt Pi E2B templates ending with this suffix. Use an empty value to disable.",
    )
    parser.add_argument(
        "--keep-dockerfile-comments",
        action="store_true",
        help="Pass task Dockerfile comments through to the E2B SDK parser.",
    )
    parser.add_argument(
        "--e2b-sandbox-timeout-sec",
        type=int,
        default=int(os.getenv("E2B_SANDBOX_TIMEOUT_SEC", "3600")),
        help="E2B sandbox timeout in seconds. E2B currently caps this at 3600.",
    )
    parser.add_argument(
        "--jobs-dir",
        type=Path,
        default=Path(os.getenv("TERMINAL_BENCH_JOBS_DIR", str(ROOT / "jobs"))),
    )
    parser.add_argument(
        "--cache-dir",
        type=Path,
        default=Path(os.getenv("TERMINAL_BENCH_CACHE_DIR", str(ROOT / ".cache" / "harbor_tasks"))),
    )
    parser.add_argument(
        "--concurrency",
        type=int,
        default=int(os.getenv("TERMINAL_BENCH_CONCURRENCY", "1")),
    )
    parser.add_argument(
        "--n-attempts",
        type=int,
        default=int(os.getenv("TERMINAL_BENCH_N_ATTEMPTS", "1")),
    )
    n_tasks_env = os.getenv("TERMINAL_BENCH_N_TASKS")
    parser.add_argument("--n-tasks", type=int, default=int(n_tasks_env) if n_tasks_env else None)
    parser.add_argument("--include-task-name", action="append", default=None)
    parser.add_argument(
        "--task-names-file",
        action="append",
        default=None,
        help="Read task names from a file, one task name per non-comment line. Repeatable.",
    )
    parser.add_argument("--exclude-task-name", action="append", default=None)
    parser.add_argument("--overwrite-tasks", action="store_true")
    parser.add_argument("--force-build", action="store_true")
    parser.add_argument("--keep-sandboxes", action="store_true")
    parser.add_argument(
        "--require-workspace-change",
        action="store_true",
        default=_bool_env("TERMINAL_BENCH_REQUIRE_WORKSPACE_CHANGE", "false"),
        help="Fail a git-backed task if Pi exits without changing the workspace diff.",
    )
    parser.add_argument("--quiet", action="store_true")
    parser.add_argument("--debug", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    skills_group = parser.add_mutually_exclusive_group()
    skills_group.add_argument(
        "--use-skills",
        action="store_true",
        default=_bool_env("TERMINAL_BENCH_USE_SKILLS", "false"),
        help="Enable the existing skill packaging path. Default is off for TB2.",
    )
    skills_group.add_argument(
        "--no-skills",
        dest="use_skills",
        action="store_false",
        help="Disable skill packaging and skill prompt injection.",
    )
    parser.add_argument(
        "--result-only",
        action="store_true",
        default=_bool_env("TERMINAL_BENCH_RESULT_ONLY", os.getenv("RESULT_ONLY", "false")),
        help="Run verifier and keep only success/failure-style trial results.",
    )
    parser.add_argument("--timeout-multiplier", type=float, default=_float_env("TIMEOUT_MULTIPLIER", "1.0"))
    parser.add_argument("--agent-timeout-multiplier", type=float, default=_optional_float_env("AGENT_TIMEOUT_MULTIPLIER"))
    parser.add_argument("--verifier-timeout-multiplier", type=float, default=_optional_float_env("VERIFIER_TIMEOUT_MULTIPLIER"))
    parser.add_argument("--agent-setup-timeout-multiplier", type=float, default=_float_env("AGENT_SETUP_TIMEOUT_MULTIPLIER", "2.0"))
    parser.add_argument("--environment-build-timeout-multiplier", type=float, default=_float_env("ENVIRONMENT_BUILD_TIMEOUT_MULTIPLIER", "2.0"))
    parser.add_argument("--agent-setup-timeout-sec", type=float, default=_float_env("AGENT_SETUP_TIMEOUT_SEC", "1200"))
    parser.add_argument("--agent-timeout-sec", type=float, default=_optional_float_env("AGENT_TIMEOUT_SEC"))
    parser.add_argument(
        "--override-cpus",
        type=int,
        default=int(os.getenv("TERMINAL_BENCH_OVERRIDE_CPUS", str(DEFAULT_DOCKER_CPUS))),
    )
    parser.add_argument(
        "--override-memory-mb",
        type=int,
        default=int(os.getenv("TERMINAL_BENCH_OVERRIDE_MEMORY_MB", str(DEFAULT_DOCKER_MEMORY_MB))),
    )
    parser.add_argument(
        "--override-storage-mb",
        type=int,
        default=int(os.getenv("TERMINAL_BENCH_OVERRIDE_STORAGE_MB", str(DEFAULT_DOCKER_STORAGE_MB))),
    )
    parser.add_argument(
        "--override-gpus",
        type=int,
        default=(
            int(os.environ["TERMINAL_BENCH_OVERRIDE_GPUS"])
            if os.getenv("TERMINAL_BENCH_OVERRIDE_GPUS")
            else None
        ),
    )
    parser.add_argument("--model-context-window", type=int, default=None)
    parser.add_argument("--model-max-tokens", type=int, default=None)
    parser.add_argument("--thinking", default=os.getenv("PI_THINKING", "off"))
    parser.add_argument("--tools", default=os.getenv("PI_TOOLS", "read,write,edit,bash,grep,find,ls"))
    args = parser.parse_args()

    if args.environment_kwarg:
        invalid = [item for item in args.environment_kwarg if "=" not in item]
        if invalid:
            raise SystemExit("--environment-kwarg entries must be KEY=VALUE: " + ", ".join(invalid))

    task_names = list(args.include_task_name or [])
    for task_names_file in args.task_names_file or []:
        task_names.extend(_read_task_names_file(task_names_file))
    args.include_task_name = list(dict.fromkeys(task_names)) or None

    selected_provider = resolve_provider(args.provider)
    if args.job_name is None:
        skill_suffix = "skills" if args.use_skills else "noskills"
        args.job_name = f"tb2_pi_{selected_provider.name}_{skill_suffix}"
    if args.model_context_window is None:
        args.model_context_window = _provider_int_env(selected_provider, "CONTEXT_WINDOW", "128000")
    if args.model_max_tokens is None:
        args.model_max_tokens = _provider_int_env(selected_provider, "MAX_TOKENS", "32000")
    args.jobs_dir = args.jobs_dir.expanduser().resolve()
    args.cache_dir = args.cache_dir.expanduser().resolve()
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
    ensure_macaron_attribution_header(os.getenv(provider.base_url_env))


def main() -> None:
    load_dotenv(ROOT / ".env", override=False)
    args = parse_args()
    if args.environment == "docker":
        ensure_docker_compose(ROOT)
    _apply_provider_overrides(args)
    provider = _provider_spec()
    _require_env(["LLM_PROVIDER", *provider.required_env()])
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    asyncio.run(run_job(args))


if __name__ == "__main__":
    main()
