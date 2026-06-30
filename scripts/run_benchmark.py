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

from providers import (
    ProviderSpec,
    ensure_macaron_attribution_header,
    ensure_reasoning_effort_none,
    resolve_provider,
)


DEFAULT_DATASET_NAME = "swe-bench/swe-bench-verified"
DEFAULT_DATASET_REF = "2"
DEFAULT_DATASET = f"{DEFAULT_DATASET_NAME}@{DEFAULT_DATASET_REF}"
DEFAULT_PROVIDER = "openai"
DEFAULT_E2B_CPUS = 1
DEFAULT_E2B_MEMORY_MB = 4096
DEFAULT_E2B_STORAGE_MB = 10240
DEFAULT_VERIFIER_BUFFER_SEC = 900


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


def _pin_dataset(value: str) -> str:
    value = value.strip()
    if value == DEFAULT_DATASET_NAME:
        return DEFAULT_DATASET
    return value


def _parse_dataset(value: str) -> tuple[str, str | None]:
    value = _pin_dataset(value)
    if "@" in value:
        name, version = value.split("@", 1)
        return name, version
    return value, None


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


def _list_env(name: str) -> list[str] | None:
    value = os.getenv(name)
    if value is None or value.strip() == "":
        return None
    items = [item.strip() for item in value.split(",")]
    return [item for item in items if item]


def _flatten_list_values(values: list[str] | None) -> list[str] | None:
    if not values:
        return None
    items: list[str] = []
    for value in values:
        items.extend(item.strip() for item in value.split(","))
    return [item for item in items if item] or None


def _int_env(name: str, default: str) -> int:
    return int(os.getenv(name, default))


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
            "benchmark_name": args.benchmark_name,
        },
    )


def build_config(args: argparse.Namespace) -> Any:
    from harbor.models.job.config import DatasetConfig, JobConfig, RetryConfig
    from harbor.models.metric.config import MetricConfig
    from harbor.models.metric.type import MetricType
    from harbor.models.trial.config import EnvironmentConfig

    provider = _provider_spec()
    dataset = _pin_dataset(args.dataset)
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

    retry_kwargs: dict[str, Any] = {
        "max_retries": args.max_retries,
        "min_wait_sec": args.retry_min_wait_sec,
        "max_wait_sec": args.retry_max_wait_sec,
    }
    retry_include = _flatten_list_values(args.retry_include)
    retry_exclude = _flatten_list_values(args.retry_exclude)
    if retry_include:
        retry_kwargs["include_exceptions"] = set(retry_include)
    if retry_exclude is not None:
        retry_kwargs["exclude_exceptions"] = set(retry_exclude)

    return JobConfig(
        job_name=args.job_name,
        jobs_dir=ROOT / "jobs",
        n_attempts=1,
        n_concurrent_trials=args.concurrency,
        retry=RetryConfig(**retry_kwargs),
        timeout_multiplier=args.timeout_multiplier,
        agent_timeout_multiplier=args.agent_timeout_multiplier,
        verifier_timeout_multiplier=args.verifier_timeout_multiplier,
        agent_setup_timeout_multiplier=args.agent_setup_timeout_multiplier,
        environment_build_timeout_multiplier=args.environment_build_timeout_multiplier,
        quiet=args.quiet,
        debug=args.debug,
        environment=EnvironmentConfig(
            import_path="environments.e2b_swebench:E2BSwebenchEnvironment",
            force_build=args.force_build,
            delete=not args.keep_sandboxes,
            override_cpus=args.override_cpus,
            override_memory_mb=args.override_memory_mb,
            override_storage_mb=args.override_storage_mb,
            env={
                "LLM_PROVIDER": "${LLM_PROVIDER}",
                **provider.env_mapping(),
            },
            kwargs={
                "template_namespace": args.e2b_template_namespace,
                "pi_template_suffix": args.e2b_pi_template_suffix,
                "strip_dockerfile_comments": not args.keep_dockerfile_comments,
                "sandbox_timeout_sec": args.e2b_sandbox_timeout_sec,
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


def _patch_harbor_runtime(*, result_only: bool = False) -> None:
    """Apply small compatibility fixes for the installed Harbor package."""
    from harbor.verifier.verifier import Verifier

    original_verify = Verifier.verify
    if not getattr(original_verify, "_skills_evo_verifier_dir_patch", False):

        async def verify_with_local_dirs(self: Any) -> Any:
            self._trial_paths.verifier_dir.mkdir(parents=True, exist_ok=True)
            self._trial_paths.test_stdout_path.parent.mkdir(parents=True, exist_ok=True)
            return await original_verify(self)

        verify_with_local_dirs._skills_evo_verifier_dir_patch = True  # type: ignore[attr-defined]
        Verifier.verify = verify_with_local_dirs

    if not result_only:
        return

    from harbor.models.trial.paths import EnvironmentPaths
    from harbor.trial.trial import Trial

    original_download_logs = Trial._maybe_download_logs
    if not getattr(original_download_logs, "_skills_evo_result_only_patch", False):

        async def download_without_agent_logs(
            self: Any, source_dir: str, target_dir: Path
        ) -> None:
            if str(source_dir) == EnvironmentPaths.agent_dir.as_posix():
                self._are_agent_logs_downloaded = True
                return
            return await original_download_logs(self, source_dir, target_dir)

        download_without_agent_logs._skills_evo_result_only_patch = True  # type: ignore[attr-defined]
        Trial._maybe_download_logs = download_without_agent_logs

    original_upload_logs = Trial._maybe_upload_agent_logs
    if not getattr(original_upload_logs, "_skills_evo_result_only_patch", False):

        async def skip_agent_log_upload(self: Any) -> None:
            return None

        skip_agent_log_upload._skills_evo_result_only_patch = True  # type: ignore[attr-defined]
        Trial._maybe_upload_agent_logs = skip_agent_log_upload

    original_populate_context = Trial._maybe_populate_agent_context
    if not getattr(original_populate_context, "_skills_evo_result_only_patch", False):

        def skip_agent_context_population(self: Any) -> None:
            return None

        skip_agent_context_population._skills_evo_result_only_patch = True  # type: ignore[attr-defined]
        Trial._maybe_populate_agent_context = skip_agent_context_population


async def run_job(args: argparse.Namespace) -> Path:
    from harbor.job import Job

    _patch_harbor_runtime(result_only=args.result_only)

    config = build_config(args)
    config_path = ROOT / "configs" / f"{args.job_name}.json"
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(config.model_dump_json(indent=2))

    log_file = ROOT / "logs" / f"{args.job_name}.log"
    log_file.parent.mkdir(parents=True, exist_ok=True)
    log_file.write_text("")
    os.environ["SKILL_EVO_BENCHMARK_LOG"] = str(log_file)

    provider = _provider_spec()
    _log(f"job={args.job_name} dataset={_pin_dataset(args.dataset)}", log_file=log_file)
    _log(
        "env=e2b "
        "agent=pi "
        f"provider={provider.name} "
        f"provider_api={provider.provider_api} "
        f"namespace={args.e2b_template_namespace} "
        f"pi_template_suffix={args.e2b_pi_template_suffix or '<disabled>'} "
        f"sandbox_timeout_sec={min(args.e2b_sandbox_timeout_sec, 7200)} "
        f"concurrency={args.concurrency} "
        f"max_retries={args.max_retries} "
        f"retry_include={_flatten_list_values(args.retry_include) or []} "
        f"retry_exclude={_flatten_list_values(args.retry_exclude) or []} "
        f"cpus={args.override_cpus} "
        f"memory_mb={args.override_memory_mb} "
        f"storage_mb={args.override_storage_mb} "
        f"model={provider.model_name} "
        f"use_skills={args.use_skills} "
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
        description="Run SWE-Bench through Harbor/E2B with Pi and minimal model providers."
    )
    parser.add_argument("--dataset", default=os.getenv("HARBOR_DATASET", DEFAULT_DATASET))
    parser.add_argument(
        "--benchmark-name",
        default=os.getenv("BENCHMARK_NAME", "swe-bench"),
        help="Prompt/metadata mode passed to PiAgent, e.g. swe-bench, swe-gym, or terminal-bench.",
    )
    parser.add_argument(
        "--provider",
        choices=["openai", "tinker"],
        default=provider.name,
        help="Provider profile. openai is a base_url/model OpenAI-compatible profile; tinker keeps the Tinker path.",
    )
    parser.add_argument("--job-name", default=os.getenv("JOB_NAME"))
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
    parser.add_argument("--concurrency", type=int, default=int(os.getenv("E2B_CONCURRENCY", "10")))
    parser.add_argument("--max-retries", type=int, default=int(os.getenv("HARBOR_MAX_RETRIES", "0")))
    parser.add_argument("--retry-min-wait-sec", type=float, default=_float_env("HARBOR_RETRY_MIN_WAIT_SEC", "5"))
    parser.add_argument("--retry-max-wait-sec", type=float, default=_float_env("HARBOR_RETRY_MAX_WAIT_SEC", "60"))
    parser.add_argument(
        "--retry-include",
        action="append",
        default=_list_env("HARBOR_RETRY_INCLUDE"),
        help="Exception type to retry. Repeatable; comma-separated values are accepted.",
    )
    parser.add_argument(
        "--retry-exclude",
        action="append",
        default=_list_env("HARBOR_RETRY_EXCLUDE"),
        help="Exception type to exclude from retry. Repeatable; comma-separated values are accepted.",
    )
    n_tasks_env = os.getenv("N_TASKS")
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
        default=_int_env("E2B_SANDBOX_TIMEOUT_SEC", "7200"),
        help="E2B sandbox timeout in seconds. Keep this above agent timeout plus verifier buffer.",
    )
    parser.add_argument("--quiet", action="store_true")
    parser.add_argument("--debug", action="store_true")
    skills_group = parser.add_mutually_exclusive_group()
    skills_group.add_argument(
        "--use-skills",
        action="store_true",
        default=_bool_env("PI_USE_SKILLS", "false"),
        help="Package swe_agent_skills into the Pi task sandbox and include skill instructions.",
    )
    skills_group.add_argument(
        "--no-skills",
        dest="use_skills",
        action="store_false",
        help="Do not package swe_agent_skills and do not include skill instructions.",
    )
    parser.add_argument(
        "--result-only",
        action="store_true",
        default=_bool_env("RESULT_ONLY"),
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
        "--verifier-buffer-sec",
        type=float,
        default=_float_env("VERIFIER_BUFFER_SEC", str(DEFAULT_VERIFIER_BUFFER_SEC)),
        help="Seconds reserved at the end of each sandbox lifetime for uploading tests and running the verifier.",
    )
    parser.add_argument("--override-cpus", type=int, default=int(os.getenv("E2B_OVERRIDE_CPUS", str(DEFAULT_E2B_CPUS))))
    parser.add_argument("--override-memory-mb", type=int, default=int(os.getenv("E2B_OVERRIDE_MEMORY_MB", str(DEFAULT_E2B_MEMORY_MB))))
    parser.add_argument("--override-storage-mb", type=int, default=int(os.getenv("E2B_OVERRIDE_STORAGE_MB", str(DEFAULT_E2B_STORAGE_MB))))
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
        args.job_name = f"pi_{selected_provider.name}_{skill_suffix}"
    if args.model_context_window is None:
        args.model_context_window = _provider_int_env(selected_provider, "CONTEXT_WINDOW", "128000")
    if args.model_max_tokens is None:
        args.model_max_tokens = _provider_int_env(selected_provider, "MAX_TOKENS", "32000")
    max_agent_timeout = args.e2b_sandbox_timeout_sec - args.verifier_buffer_sec
    if max_agent_timeout < 60:
        raise SystemExit(
            "Invalid timeout configuration: --e2b-sandbox-timeout-sec must exceed "
            "--verifier-buffer-sec by at least 60 seconds."
        )
    if args.agent_timeout_sec is not None and args.agent_timeout_sec > max_agent_timeout:
        _log(
            "Reducing agent_timeout_sec from "
            f"{args.agent_timeout_sec:g} to {max_agent_timeout:g} so the verifier "
            f"keeps a {args.verifier_buffer_sec:g}s sandbox buffer."
        )
        args.agent_timeout_sec = max_agent_timeout
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
    ensure_reasoning_effort_none(
        os.getenv(provider.base_url_env),
        env_prefix=provider.env_prefix,
    )


def main() -> None:
    load_dotenv(ROOT / ".env", override=False)
    args = parse_args()
    _apply_provider_overrides(args)
    provider = _provider_spec()
    _require_env(["LLM_PROVIDER", *provider.required_env(), "E2B_API_KEY"])
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    asyncio.run(run_job(args))


if __name__ == "__main__":
    main()
