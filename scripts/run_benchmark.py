#!/usr/bin/env python
import argparse
import asyncio
import os
import sys
import tomllib
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from dotenv import load_dotenv


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from providers import (
    ProviderSpec,
    SUPPORTED_PROVIDER_CHOICES,
    ensure_macaron_attribution_header,
    ensure_reasoning_effort_none,
    normalize_provider_name,
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
AGENT_CHOICES = ("pi", "claude-code")
DEEPSWE_TRANSIENT_RETRY_EXCEPTIONS = {
    "ConnectException",
    "ProtocolError",
    "ReadError",
    "RemoteProtocolError",
    "SandboxException",
}


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
    return normalize_provider_name(os.getenv("LLM_PROVIDER", DEFAULT_PROVIDER))


def _provider_spec() -> ProviderSpec:
    return resolve_provider(_provider())


def _provider_int_env(provider: ProviderSpec, suffix: str, default: str) -> int:
    if suffix == "CONTEXT_WINDOW":
        default = str(provider.default_context_window)
    elif suffix == "MAX_TOKENS":
        default = str(provider.default_max_tokens)
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


def _agent_name(value: str | None) -> str:
    normalized = str(value or "pi").strip().lower().replace("_", "-")
    aliases = {
        "pi-agent": "pi",
        "pi_agent": "pi",
        "claude": "claude-code",
        "claude_code": "claude-code",
        "claude-sdk": "claude-code",
        "claude_sdk": "claude-code",
    }
    return aliases.get(normalized, normalized)


def _is_local_deepswe_dataset(value: str) -> bool:
    dataset = Path(_pin_dataset(value)).expanduser()
    return (
        dataset.exists()
        and (dataset / "dataset.toml").is_file()
        and (dataset / "manifest.json").is_file()
        and any(dataset.glob("*/pre_artifacts.sh"))
    )


def _retry_include_exceptions(args: argparse.Namespace) -> set[str] | None:
    retry_include = _flatten_list_values(args.retry_include)
    if retry_include:
        return set(retry_include)
    if args.max_retries > 0 and _is_local_deepswe_dataset(args.dataset):
        return set(DEEPSWE_TRANSIENT_RETRY_EXCEPTIONS)
    return None


def _deepswe_artifact_hook_info(trial: Any) -> tuple[Path, str | None] | None:
    task = getattr(trial, "task", None) or getattr(trial, "_task", None)
    task_dir_value = getattr(task, "task_dir", None)
    if task_dir_value is None:
        paths = getattr(task, "paths", None)
        task_dir_value = getattr(paths, "task_dir", None)
    if task_dir_value is None:
        return None

    task_dir = Path(task_dir_value).expanduser()
    pre_artifacts = task_dir / "pre_artifacts.sh"
    if not pre_artifacts.is_file():
        return None

    base_commit = None
    try:
        task_toml = tomllib.loads((task_dir / "task.toml").read_text(encoding="utf-8"))
        base_commit = task_toml.get("metadata", {}).get("base_commit_hash")
    except Exception:
        base_commit = None
    if base_commit is not None:
        base_commit = str(base_commit).strip() or None
    return pre_artifacts, base_commit


def _pi_agent_config(args: argparse.Namespace, provider: ProviderSpec) -> Any:
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


def _claude_agent_config(args: argparse.Namespace, provider: ProviderSpec) -> Any:
    from harbor.models.trial.config import AgentConfig

    if not provider.supports_claude_code():
        raise SystemExit(
            f"Provider {provider.name!r} does not define a Claude Code endpoint. "
            "Use novita, macaron/marcron, or sglang for --agent claude-code."
        )
    return AgentConfig(
        import_path="agents.claude_sdk_agent:ClaudeSdkAgent",
        model_name=provider.model_name,
        override_setup_timeout_sec=args.agent_setup_timeout_sec,
        override_timeout_sec=args.agent_timeout_sec,
        kwargs={
            "provider_name": provider.name,
            "api_key_env": provider.api_key_env,
            "anthropic_base_url_env": provider.anthropic_base_url_env,
            "model_env": provider.model_env,
            "default_anthropic_base_url": provider.default_anthropic_base_url,
            "default_model": provider.default_model,
            "max_turns": args.claude_max_turns,
            "max_budget_usd": args.claude_max_budget_usd,
            "result_only": args.result_only,
            "use_skills": args.use_skills,
            "benchmark_name": args.benchmark_name,
        },
    )


def _agent_config(args: argparse.Namespace, provider: ProviderSpec) -> Any:
    if _agent_name(args.agent) == "claude-code":
        return _claude_agent_config(args, provider)
    return _pi_agent_config(args, provider)


def build_config(args: argparse.Namespace) -> Any:
    from harbor.models.job.config import DatasetConfig, JobConfig, RetryConfig
    from harbor.models.metric.config import MetricConfig
    from harbor.models.metric.type import MetricType
    from harbor.models.trial.config import EnvironmentConfig, VerifierConfig

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
    retry_include = _retry_include_exceptions(args)
    retry_exclude = _flatten_list_values(args.retry_exclude)
    if retry_include:
        retry_kwargs["include_exceptions"] = set(retry_include)
    if retry_exclude is not None:
        retry_kwargs["exclude_exceptions"] = set(retry_exclude)

    agent = _agent_name(args.agent)
    metrics = []
    if not _is_local_deepswe_dataset(args.dataset):
        metrics.append(MetricConfig(type=MetricType.MEAN))
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
                **provider.env_mapping(agent=agent),
            },
            kwargs={
                "template_namespace": args.e2b_template_namespace,
                "pi_template_suffix": args.e2b_pi_template_suffix if agent == "pi" else "",
                "strip_dockerfile_comments": not args.keep_dockerfile_comments,
                "sandbox_timeout_sec": args.e2b_sandbox_timeout_sec,
                "force_allow_internet": args.force_agent_internet,
            },
        ),
        verifier=VerifierConfig(disable=args.disable_verifier),
        agents=[_agent_config(args, provider)],
        datasets=[DatasetConfig(**dataset_kwargs)],
        metrics=metrics,
    )


def _trial_reward_summary(result: Any) -> str:
    if result is None or result.verifier_result is None:
        return "reward=<none>"
    rewards = result.verifier_result.rewards
    if not rewards:
        return "reward=<none>"
    return " ".join(f"{key}={value}" for key, value in rewards.items())


def _patch_harbor_runtime(
    *,
    result_only: bool = False,
    deepswe_pre_artifacts: bool = False,
) -> None:
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

    from harbor.models.trial.paths import EnvironmentPaths
    from harbor.trial.trial import Trial

    if result_only:
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

    if not deepswe_pre_artifacts:
        return

    from harbor.metrics.mean import Mean

    original_mean_compute = Mean.compute
    if not getattr(original_mean_compute, "_skills_evo_deepswe_multi_reward_patch", False):

        def compute_with_deepswe_reward_key(
            self: Any,
            rewards: list[dict[str, float | int] | None],
        ) -> dict[str, float | int]:
            normalized_rewards: list[dict[str, float | int] | None] = []
            for reward in rewards:
                if isinstance(reward, dict) and len(reward) > 1 and "reward" in reward:
                    normalized_rewards.append({"reward": reward["reward"]})
                else:
                    normalized_rewards.append(reward)
            return original_mean_compute(self, normalized_rewards)

        compute_with_deepswe_reward_key._skills_evo_deepswe_multi_reward_patch = True  # type: ignore[attr-defined]
        Mean.compute = compute_with_deepswe_reward_key  # type: ignore[method-assign]

    try:
        import harbor.models.task.artifacts as task_artifacts
        import harbor.trial.artifact_handler as artifact_handler_module
        from harbor.constants import MAIN_SERVICE_NAME
    except ModuleNotFoundError:
        task_artifacts = None
        artifact_handler_module = None
        MAIN_SERVICE_NAME = None

    if task_artifacts is not None and artifact_handler_module is not None:

        def is_main_deepswe_model_patch_artifact(artifact: Any) -> bool:
            return (
                getattr(artifact, "source", "").rstrip("/")
                == "/logs/artifacts/model.patch"
                and task_artifacts.effective_artifact_service(artifact)
                == MAIN_SERVICE_NAME
            )

        original_with_convention_entry = task_artifacts.with_convention_entry
        if not getattr(
            original_with_convention_entry,
            "_skills_evo_deepswe_model_patch_artifact_patch",
            False,
        ):

            def with_convention_entry_without_deepswe_dir(
                entries: Any,
                *,
                convention_source: str,
            ) -> list[Any]:
                normalized = task_artifacts.normalize_artifact_entries(entries)
                if (
                    convention_source.rstrip("/") == "/logs/artifacts"
                    and any(
                        is_main_deepswe_model_patch_artifact(artifact)
                        for artifact in normalized
                    )
                ):
                    return normalized
                return original_with_convention_entry(
                    entries,
                    convention_source=convention_source,
                )

            with_convention_entry_without_deepswe_dir._skills_evo_deepswe_model_patch_artifact_patch = True  # type: ignore[attr-defined]
            task_artifacts.with_convention_entry = (
                with_convention_entry_without_deepswe_dir
            )
            artifact_handler_module.with_convention_entry = (
                with_convention_entry_without_deepswe_dir
            )
        else:
            artifact_handler_module.with_convention_entry = (
                original_with_convention_entry
            )

    def trial_environment(trial: Any) -> Any:
        return getattr(trial, "_environment", None) or getattr(
            trial, "agent_environment"
        )

    def trial_logger(trial: Any) -> Any:
        return getattr(trial, "_logger", None) or getattr(trial, "logger")

    original_setup_agent = Trial._setup_agent
    if not getattr(original_setup_agent, "_skills_evo_deepswe_baseline_patch", False):

        async def setup_agent_with_deepswe_baseline(self: Any) -> None:
            hook_info = _deepswe_artifact_hook_info(self)
            if hook_info is not None:
                _, base_commit = hook_info
                environment = trial_environment(self)
                logger = trial_logger(self)
                try:
                    result = await environment.exec(
                        command=(
                            "set -uo pipefail\n"
                            "if [ -n \"${DEEPSWE_BASE_COMMIT:-}\" ] && "
                            "git -C /app rev-parse --is-inside-work-tree >/dev/null 2>&1; then\n"
                            "  mkdir -p /logs/agent /logs/artifacts\n"
                            "  cd /app || exit 0\n"
                            "  git config --global --add safe.directory /app 2>/dev/null || true\n"
                            "  git add -A . 2>/dev/null || true\n"
                            "  tree=$(git write-tree)\n"
                            "  if git rev-parse --verify \"${DEEPSWE_BASE_COMMIT}^{commit}\" "
                            ">/dev/null 2>&1; then\n"
                            "    commit=$(printf 'DeepSWE initial worktree snapshot\\n' | "
                            "git commit-tree \"$tree\" -p \"${DEEPSWE_BASE_COMMIT}\")\n"
                            "  else\n"
                            "    commit=$(printf 'DeepSWE initial worktree snapshot\\n' | "
                            "git commit-tree \"$tree\")\n"
                            "  fi\n"
                            "  printf '%s\\n' \"$commit\" > /logs/agent/deepswe_baseline_commit\n"
                            "  git reset -q 2>/dev/null || true\n"
                            "  echo \"[deepswe_pre_artifacts] baseline_commit=$commit\"\n"
                            "fi\n"
                        ),
                        cwd="/app",
                        env={"DEEPSWE_BASE_COMMIT": base_commit or ""},
                        timeout_sec=120,
                        user="root",
                    )
                    if result.stdout:
                        logger.info(result.stdout.rstrip())
                    if result.stderr:
                        logger.warning(result.stderr.rstrip())
                except Exception as exc:
                    logger.warning("DeepSWE baseline hook failed: %s", exc)

            return await original_setup_agent(self)

        setup_agent_with_deepswe_baseline._skills_evo_deepswe_baseline_patch = True  # type: ignore[attr-defined]
        Trial._setup_agent = setup_agent_with_deepswe_baseline

    async def run_deepswe_pre_artifacts(self: Any) -> None:
        if getattr(self, "_skills_evo_deepswe_pre_artifacts_ran", False):
            return
        self._skills_evo_deepswe_pre_artifacts_ran = True

        hook_info = _deepswe_artifact_hook_info(self)
        if hook_info is None:
            trial_logger(self).debug(
                "DeepSWE pre_artifacts hook skipped: task path not recognized"
            )
            return
        pre_artifacts, base_commit = hook_info
        environment = trial_environment(self)
        logger = trial_logger(self)

        remote_script = f"{EnvironmentPaths.agent_dir.as_posix()}/deepswe_pre_artifacts.sh"
        try:
            await environment.upload_file(
                source_path=pre_artifacts,
                target_path=remote_script,
            )
            result = await environment.exec(
                command=(
                    "set -uo pipefail\n"
                    "mkdir -p /logs/artifacts\n"
                    f"chmod +x {remote_script}\n"
                    f"/bin/bash {remote_script} || true\n"
                    "if [ -n \"${DEEPSWE_BASE_COMMIT:-}\" ] && "
                    "git -C /app rev-parse --verify \"${DEEPSWE_BASE_COMMIT}^{commit}\" "
                    ">/dev/null 2>&1; then\n"
                    "  cd /app || exit 0\n"
                    "  git config --global --add safe.directory /app 2>/dev/null || true\n"
                    "  python3 - <<'PY'\n"
                    "import os, shutil, subprocess\n"
                    "from pathlib import Path\n"
                    "base = os.environ.get('DEEPSWE_BASE_COMMIT', '')\n"
                    "baseline_path = Path('/logs/agent/deepswe_baseline_commit')\n"
                    "baseline = baseline_path.read_text().strip() if baseline_path.exists() else ''\n"
                    "patch = Path('/logs/artifacts/model.patch')\n"
                    "def ok_commit(ref):\n"
                    "    if not ref:\n"
                    "        return False\n"
                    "    return subprocess.run(\n"
                    "        ['git', 'rev-parse', '--verify', f'{ref}^{{commit}}'],\n"
                    "        cwd='/app', stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,\n"
                    "    ).returncode == 0\n"
                    "compare_ref = baseline if ok_commit(baseline) else base\n"
                    "subprocess.run(['git', 'add', '-N', '.'], cwd='/app', stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)\n"
                    "changed_raw = subprocess.run(\n"
                    "    ['git', 'diff', '--name-only', '-z', compare_ref],\n"
                    "    cwd='/app', stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,\n"
                    ").stdout\n"
                    "subprocess.run(['git', 'reset', '-q'], cwd='/app', stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)\n"
                    "paths = [p.decode('utf-8', 'surrogateescape') for p in changed_raw.split(b'\\0') if p]\n"
                    "paths = [p for p in dict.fromkeys(paths) if p and not Path(p).is_absolute() and '..' not in Path(p).parts]\n"
                    "patch.parent.mkdir(parents=True, exist_ok=True)\n"
                    "if paths:\n"
                    "    with patch.open('wb') as handle:\n"
                    "        subprocess.run(\n"
                    "            ['git', 'diff', '--binary', base, '--', *paths],\n"
                    "            cwd='/app', stdout=handle, stderr=subprocess.DEVNULL,\n"
                    "        )\n"
                    "else:\n"
                    "    patch.write_bytes(b'')\n"
                    "for rel in paths:\n"
                    "    exists_in_base = subprocess.run(\n"
                    "        ['git', 'cat-file', '-e', f'{base}:{rel}'],\n"
                    "        cwd='/app', stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,\n"
                    "    ).returncode == 0\n"
                    "    if exists_in_base:\n"
                    "        continue\n"
                    "    target = Path('/app') / rel\n"
                    "    try:\n"
                    "        if target.is_dir() and not target.is_symlink():\n"
                    "            shutil.rmtree(target)\n"
                    "        elif target.exists() or target.is_symlink():\n"
                    "            target.unlink()\n"
                    "    except OSError:\n"
                    "        pass\n"
                    "PY\n"
                    "fi\n"
                    "chmod -R a+rX /logs/artifacts 2>/dev/null || true\n"
                    "if [ -f /logs/artifacts/model.patch ]; then "
                    "printf '[deepswe_pre_artifacts] model.patch bytes='; "
                    "wc -c < /logs/artifacts/model.patch; "
                    "else echo '[deepswe_pre_artifacts] model.patch missing'; fi\n"
                ),
                cwd="/app",
                env={"DEEPSWE_BASE_COMMIT": base_commit or ""},
                timeout_sec=120,
                user="root",
            )
            if result.stdout:
                logger.info(result.stdout.rstrip())
            if result.stderr:
                logger.warning(result.stderr.rstrip())
            if result.return_code != 0:
                logger.warning(
                    "DeepSWE pre_artifacts exited with return_code=%s",
                    result.return_code,
                )
        except Exception as exc:
            logger.warning("DeepSWE pre_artifacts hook failed: %s", exc)

    if hasattr(Trial, "_run_verification"):
        original_run_verification = Trial._run_verification
        if not getattr(
            original_run_verification,
            "_skills_evo_deepswe_pre_artifacts_patch",
            False,
        ):

            async def run_deepswe_pre_artifacts_then_verify(self: Any) -> None:
                await run_deepswe_pre_artifacts(self)
                return await original_run_verification(self)

            run_deepswe_pre_artifacts_then_verify._skills_evo_deepswe_pre_artifacts_patch = True  # type: ignore[attr-defined]
            Trial._run_verification = run_deepswe_pre_artifacts_then_verify
    else:
        original_collect_artifacts_phased = Trial._collect_artifacts_phased
        if not getattr(
            original_collect_artifacts_phased,
            "_skills_evo_deepswe_pre_artifacts_patch",
            False,
        ):

            async def collect_artifacts_after_deepswe_pre_artifacts(
                self: Any,
                *args: Any,
                **kwargs: Any,
            ) -> Any:
                await run_deepswe_pre_artifacts(self)
                return await original_collect_artifacts_phased(
                    self, *args, **kwargs
                )

            collect_artifacts_after_deepswe_pre_artifacts._skills_evo_deepswe_pre_artifacts_patch = True  # type: ignore[attr-defined]
            Trial._collect_artifacts_phased = collect_artifacts_after_deepswe_pre_artifacts


async def run_job(args: argparse.Namespace) -> Path:
    from harbor.job import Job

    deepswe_pre_artifacts = (
        args.enable_deepswe_pre_artifacts
        and not args.disable_deepswe_pre_artifacts
        and _is_local_deepswe_dataset(args.dataset)
    )
    _patch_harbor_runtime(
        result_only=args.result_only,
        deepswe_pre_artifacts=deepswe_pre_artifacts,
    )

    config = build_config(args)
    config_path = ROOT / "configs" / f"{args.job_name}.json"
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(config.model_dump_json(indent=2))

    log_file = ROOT / "logs" / f"{args.job_name}.log"
    log_file.parent.mkdir(parents=True, exist_ok=True)
    log_file.write_text("")
    os.environ["SKILL_EVO_BENCHMARK_LOG"] = str(log_file)

    provider = _provider_spec()
    agent = _agent_name(args.agent)
    retry_include = _retry_include_exceptions(args) or set()
    provider_api_text = (
        f"provider_api={provider.provider_api} " if agent == "pi" else ""
    )
    e2b_template_suffix_text = (
        f"pi_template_suffix={args.e2b_pi_template_suffix or '<disabled>'} "
        if agent == "pi"
        else ""
    )
    _log(f"job={args.job_name} dataset={_pin_dataset(args.dataset)}", log_file=log_file)
    _log(
        "env=e2b "
        f"agent={agent} "
        f"provider={provider.name} "
        f"{provider_api_text}"
        f"namespace={args.e2b_template_namespace} "
        f"{e2b_template_suffix_text}"
        f"sandbox_timeout_sec={min(args.e2b_sandbox_timeout_sec, 7200)} "
        f"concurrency={args.concurrency} "
        f"max_retries={args.max_retries} "
        f"retry_include={sorted(retry_include)} "
        f"retry_exclude={_flatten_list_values(args.retry_exclude) or []} "
        f"cpus={args.override_cpus} "
        f"memory_mb={args.override_memory_mb} "
        f"storage_mb={args.override_storage_mb} "
        f"model={provider.model_name} "
        f"force_agent_internet={args.force_agent_internet} "
        f"disable_verifier={args.disable_verifier} "
        f"deepswe_pre_artifacts={deepswe_pre_artifacts} "
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
    if provider_name not in SUPPORTED_PROVIDER_CHOICES:
        provider_name = DEFAULT_PROVIDER
    provider = resolve_provider(provider_name)
    parser = argparse.ArgumentParser(
        description=(
            "Run SWE-Bench-style Harbor datasets through E2B with Pi or "
            "Claude Code harnesses."
        )
    )
    parser.add_argument("--dataset", default=os.getenv("HARBOR_DATASET", DEFAULT_DATASET))
    parser.add_argument(
        "--benchmark-name",
        default=os.getenv("BENCHMARK_NAME", "swe-bench"),
        help="Prompt/metadata mode passed to the agent, e.g. swe-bench, swe-gym, or deepswe.",
    )
    parser.add_argument(
        "--agent",
        "--harness",
        choices=AGENT_CHOICES,
        default=_agent_name(os.getenv("BENCHMARK_AGENT", os.getenv("HARBOR_AGENT", "pi"))),
        help="Agent harness: pi or claude-code.",
    )
    parser.add_argument(
        "--provider",
        choices=SUPPORTED_PROVIDER_CHOICES,
        default=provider.name,
        help=(
            "Provider profile. novita/macaron/sglang expose both Pi "
            "OpenAI-compatible and Claude Code Anthropic-style settings."
        ),
    )
    parser.add_argument("--job-name", default=os.getenv("JOB_NAME"))
    parser.add_argument(
        "--provider-base-url",
        default=os.getenv("PROVIDER_BASE_URL"),
        help="Override the selected provider BASE_URL for Pi/OpenAI-compatible runs.",
    )
    parser.add_argument(
        "--provider-anthropic-base-url",
        default=os.getenv("PROVIDER_ANTHROPIC_BASE_URL"),
        help="Override the selected provider ANTHROPIC_BASE_URL for Claude Code runs.",
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
        "--force-agent-internet",
        action="store_true",
        default=_bool_env("FORCE_AGENT_INTERNET"),
        help=(
            "Create E2B sandboxes with internet even when a task disables it. "
            "Intended for harness smoke tests, not official scoring."
        ),
    )
    parser.add_argument(
        "--disable-verifier",
        action="store_true",
        default=_bool_env("DISABLE_VERIFIER"),
        help="Skip Harbor verification.",
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
    parser.add_argument(
        "--enable-deepswe-pre-artifacts",
        action="store_true",
        default=_bool_env("ENABLE_DEEPSWE_PRE_ARTIFACTS", "true"),
        help=(
            "For local DeepSWE tasks datasets only, run each task's "
            "pre_artifacts.sh inside E2B before verification."
        ),
    )
    parser.add_argument(
        "--disable-deepswe-pre-artifacts",
        action="store_true",
        default=_bool_env("DISABLE_DEEPSWE_PRE_ARTIFACTS"),
        help="Disable the DeepSWE-only pre_artifacts hook.",
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
    parser.add_argument(
        "--claude-max-turns",
        type=int,
        default=int(os.getenv("CLAUDE_MAX_TURNS", "0")) or None,
    )
    parser.add_argument(
        "--claude-max-budget-usd",
        type=float,
        default=float(os.getenv("CLAUDE_MAX_BUDGET_USD", "0")) or None,
    )
    args = parser.parse_args()

    args.agent = _agent_name(args.agent)

    task_names = list(args.include_task_name or [])
    for task_names_file in args.task_names_file or []:
        task_names.extend(_read_task_names_file(task_names_file))
    args.include_task_name = list(dict.fromkeys(task_names)) or None

    args.provider = normalize_provider_name(args.provider)
    selected_provider = resolve_provider(args.provider)
    if args.job_name is None:
        skill_suffix = "skills" if args.use_skills else "noskills"
        agent_slug = args.agent.replace("-", "_")
        args.job_name = f"{agent_slug}_{selected_provider.name}_{skill_suffix}"
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
    os.environ.setdefault("CLAUDE_CODE_ATTRIBUTION_HEADER", "0")
    if args.provider_base_url:
        os.environ[provider.base_url_env] = args.provider_base_url
    elif provider.default_base_url and not os.environ.get(provider.base_url_env):
        os.environ[provider.base_url_env] = provider.default_base_url
    if args.provider_anthropic_base_url:
        if not provider.anthropic_base_url_env:
            raise SystemExit(
                f"Provider {provider.name!r} does not support --provider-anthropic-base-url."
            )
        os.environ[provider.anthropic_base_url_env] = args.provider_anthropic_base_url
    elif (
        provider.anthropic_base_url_env
        and provider.default_anthropic_base_url
        and not os.environ.get(provider.anthropic_base_url_env)
    ):
        os.environ[provider.anthropic_base_url_env] = provider.default_anthropic_base_url
    if args.provider_model:
        os.environ[provider.model_env] = args.provider_model
    elif provider.default_model and not os.environ.get(provider.model_env):
        os.environ[provider.model_env] = provider.default_model
    if args.provider_api_key:
        os.environ[provider.api_key_env] = args.provider_api_key
    if args.provider_api:
        os.environ[provider.provider_api_env] = args.provider_api
    if provider.default_api_key and not os.environ.get(provider.api_key_env):
        os.environ[provider.api_key_env] = provider.default_api_key
    for env_name in (provider.base_url_env, provider.anthropic_base_url_env):
        if env_name:
            ensure_macaron_attribution_header(os.getenv(env_name))
    if provider.name in {"novita", "macaron", "sglang", "sglang_qwen"}:
        os.environ.setdefault(f"{provider.env_prefix}_REASONING_EFFORT", "none")
        os.environ.setdefault(f"{provider.env_prefix}_ENABLE_THINKING", "false")
    ensure_reasoning_effort_none(
        os.getenv(provider.base_url_env),
        env_prefix=provider.env_prefix,
    )


def main() -> None:
    load_dotenv(ROOT / ".env", override=False)
    args = parse_args()
    _apply_provider_overrides(args)
    provider = _provider_spec()
    try:
        required_provider_env = provider.required_env(agent=args.agent)
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc
    _require_env(["LLM_PROVIDER", *required_provider_env, "E2B_API_KEY"])
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    asyncio.run(run_job(args))


if __name__ == "__main__":
    main()
