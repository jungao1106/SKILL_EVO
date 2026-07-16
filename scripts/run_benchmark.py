#!/usr/bin/env python
import argparse
import asyncio
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tomllib
from datetime import datetime, timezone
from functools import wraps
from pathlib import Path, PurePosixPath
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
from scripts.job_run_lock import exclusive_job_run


DEFAULT_DATASET_NAME = "swe-bench/swe-bench-verified"
DEFAULT_DATASET_REF = "2"
DEFAULT_DATASET = f"{DEFAULT_DATASET_NAME}@{DEFAULT_DATASET_REF}"
DEFAULT_PROVIDER = "openai"
DEFAULT_E2B_CPUS = 1
DEFAULT_E2B_MEMORY_MB = 4096
DEFAULT_E2B_STORAGE_MB = 10240
DEFAULT_VERIFIER_BUFFER_SEC = 900
DEEPSWE_MIN_CPUS = 2
DEEPSWE_MIN_MEMORY_MB = 8192
DEEPSWE_MIN_STORAGE_MB = 20480
DEEPSWE_MIN_MAX_RETRIES = 3
DEEPSWE_MIN_SANDBOX_TIMEOUT_SEC = 14400
DEEPSWE_MIN_VERIFIER_BUFFER_SEC = 4800
DEEPSWE_RESUME_CONTRACT_VERSION = 1
DEEPSWE_ARTIFACT_HOOK_VERSION = (
    "exact-official-test-paths-v8-offline-suite-infra-detection"
)
AGENT_CHOICES = ("pi", "claude-code")
DEEPSWE_TRANSIENT_RETRY_EXCEPTIONS = {
    "AgentSetupTimeoutError",
    "APIConnectionError",
    "APITimeoutError",
    "ConnectError",
    "ConnectTimeout",
    "ConnectException",
    "DeepSweProviderTransientError",
    "DeepSweAgentIncompleteError",
    "DeepSweAgentSetupInfraError",
    "EnvironmentStartTimeoutError",
    "E2BResourceMismatchError",
    "PoolTimeout",
    "ProviderTransientAgentError",
    "ProtocolError",
    "ReadError",
    "ReadTimeout",
    "RemoteProtocolError",
    "RateLimitException",
    "SandboxException",
    "TimeoutException",
    "WriteError",
}
DEEPSWE_VALID_AGENT_OUTCOME_EXCEPTIONS = {
    "AgentTimeoutError",
    "NonZeroAgentExitCodeError",
}


class DeepSweVerifierInfraError(RuntimeError):
    """Raised when the DeepSWE verifier reports an infrastructure failure."""


class DeepSweAgentSetupInfraError(RuntimeError):
    """Raised when installing the agent runtime fails before model execution."""


class DeepSweArtifactDownloadError(RuntimeError):
    """Raised when required DeepSWE audit artifacts are missing locally."""


DEEPSWE_ARTIFACT_DOWNLOAD_ATTEMPTS = 3

_DEEPSWE_OFFLINE_TOOLCHAIN_FETCH_PATTERNS = (
    (
        "go-toolchain",
        re.compile(
            r"\bgo:\s+download(?:ing)?\s+go\d[^\n]*\bgolang\.org/toolchain@",
            re.IGNORECASE,
        ),
    ),
)
_DEEPSWE_OFFLINE_NETWORK_FAILURE_PATTERNS = (
    (
        "dns-timeout",
        re.compile(
            r"\bdial tcp[^\n]*\blookup\s+\S+[^\n]*\b(?:i/o timeout|no such host|server misbehaving)\b",
            re.IGNORECASE,
        ),
    ),
    (
        "dns-resolution",
        re.compile(
            r"\b(?:temporary failure in name resolution|could not resolve host|"
            r"getaddrinfo\s+(?:eai_again|enotfound))\b",
            re.IGNORECASE,
        ),
    ),
    (
        "network-unreachable",
        re.compile(r"\bnetwork is unreachable\b", re.IGNORECASE),
    ),
)
_DEEPSWE_MISSING_TEST_RESULT_MARKER = (
    "missing from report (test did not run or produced no result"
)
_DEEPSWE_DEPENDENCY_MANIFEST_NAMES = frozenset(
    {
        ".go-version",
        ".npmrc",
        ".python-version",
        ".tool-versions",
        "bun.lock",
        "bun.lockb",
        "cargo.lock",
        "cargo.toml",
        "composer.json",
        "composer.lock",
        "deno.lock",
        "gemfile",
        "gemfile.lock",
        "go.env",
        "go.mod",
        "go.sum",
        "go.work",
        "go.work.sum",
        "gradle.lockfile",
        "mise.toml",
        "npm-shrinkwrap.json",
        "pip.conf",
        "pipfile",
        "pipfile.lock",
        "pnpm-lock.yaml",
        "poetry.lock",
        "pom.xml",
        "pyproject.toml",
        "rust-toolchain",
        "rust-toolchain.toml",
        "setup.cfg",
        "setup.py",
        "uv.lock",
        "vcpkg.json",
        "yarn.lock",
    }
)
_DEEPSWE_PYTEST_NODE = re.compile(r"\S+\.py(?:::[^\s:]+)+")


def _deepswe_trial_exception_type(trial: Any) -> str:
    result = getattr(trial, "result", None)
    exception_info = getattr(result, "exception_info", None)
    if isinstance(exception_info, dict):
        return str(exception_info.get("exception_type") or "")
    return str(getattr(exception_info, "exception_type", "") or "")


def _required_deepswe_artifact_paths(
    trial: Any,
    *,
    result_only: bool,
) -> list[Path]:
    required = [trial._trial_paths.artifacts_dir / "model.patch"]
    if result_only:
        return required

    import_path = str(trial.config.agent.import_path or "")
    if "claude_sdk_agent" in import_path:
        required.append(trial._trial_paths.agent_dir / "claude-agent-metadata.json")
        if _deepswe_trial_exception_type(trial) not in {
            "AgentTimeoutError",
            "NonZeroAgentExitCodeError",
        }:
            required.append(
                trial._trial_paths.agent_dir / "claude-agent-sdk-result.json"
            )
        required.append(trial._trial_paths.agent_dir / "claude-agent-sdk.jsonl")
    elif "pi_agent" in import_path:
        required.extend(
            [
                trial._trial_paths.agent_dir / "pi-metadata.json",
                trial._trial_paths.agent_dir / "pi-events.jsonl",
            ]
        )
    return required


async def _download_deepswe_artifacts_with_retry(
    trial: Any,
    original_download_artifacts: Any,
    *,
    result_only: bool,
    attempts: int = DEEPSWE_ARTIFACT_DOWNLOAD_ATTEMPTS,
    retry_delay_sec: float = 1.0,
) -> list[Path]:
    """Retry downloads in the existing sandbox without rerunning the model."""
    from harbor.models.trial.paths import EnvironmentPaths

    required = _required_deepswe_artifact_paths(
        trial,
        result_only=result_only,
    )
    missing = required
    for attempt in range(1, max(1, attempts) + 1):
        if attempt > 1:
            trial_logger = getattr(trial, "_logger", None) or getattr(
                trial, "logger", None
            )
            if trial_logger is not None:
                trial_logger.warning(
                    "Required DeepSWE artifacts are missing; retrying downloads "
                    "in the existing sandbox (%s/%s): %s",
                    attempt,
                    attempts,
                    ", ".join(str(path) for path in missing),
                )
            await asyncio.sleep(retry_delay_sec * (attempt - 1))
            if not result_only:
                trial._are_agent_logs_downloaded = False
                await trial._maybe_download_logs(
                    source_dir=EnvironmentPaths.agent_dir.as_posix(),
                    target_dir=trial._trial_paths.agent_dir,
                )

        await original_download_artifacts(trial)
        missing = [path for path in required if not path.is_file()]
        if not missing:
            return []
    return missing


_DEEPSWE_MODEL_PATCH_POSTPROCESS_SCRIPT = """\
import json
import os
import shutil
import subprocess
from pathlib import Path

app_dir = Path(os.environ.get("DEEPSWE_APP_DIR", "/app"))
logs_dir = Path(os.environ.get("DEEPSWE_LOGS_DIR", "/logs"))
base = os.environ.get("DEEPSWE_BASE_COMMIT", "")
baseline_path = logs_dir / "agent" / "deepswe_baseline_commit"
baseline = baseline_path.read_text().strip() if baseline_path.exists() else ""
patch = logs_dir / "artifacts" / "model.patch"


def git(*args, **kwargs):
    return subprocess.run(["git", *args], cwd=app_dir, **kwargs)


def ok_commit(ref):
    if not ref:
        return False
    return git(
        "rev-parse",
        "--verify",
        f"{ref}^{{commit}}",
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    ).returncode == 0


compare_ref = baseline if ok_commit(baseline) else base
official_test_paths = {
    path
    for path in json.loads(
        os.environ.get("DEEPSWE_OFFICIAL_TEST_PATHS_JSON", "[]")
    )
    if isinstance(path, str)
    and path
    and not Path(path).is_absolute()
    and ".." not in Path(path).parts
}
git("add", "-N", ".", stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
changed_raw = git(
    "diff",
    "--name-only",
    "-z",
    compare_ref,
    stdout=subprocess.PIPE,
    stderr=subprocess.DEVNULL,
).stdout
changed_paths = [
    path.decode("utf-8", "surrogateescape")
    for path in changed_raw.split(b"\\0")
    if path
]
changed_paths = [
    path
    for path in dict.fromkeys(changed_paths)
    if path and not Path(path).is_absolute() and ".." not in Path(path).parts
]
dropped_tests = [path for path in changed_paths if path in official_test_paths]
model_paths = [path for path in changed_paths if path not in official_test_paths]
if dropped_tests:
    print(
        "[deepswe_pre_artifacts] excluded test files from model.patch: "
        + ", ".join(dropped_tests)
    )

patch.parent.mkdir(parents=True, exist_ok=True)
if model_paths:
    with patch.open("wb") as handle:
        git(
            "diff",
            "--binary",
            base,
            "--",
            *model_paths,
            stdout=handle,
            stderr=subprocess.DEVNULL,
            check=True,
        )
else:
    patch.write_bytes(b"")

# Keep intent-to-add entries until after the diff is written so newly created,
# uncommitted source files are represented in model.patch.
git("reset", "-q", stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

# The verifier resets test.patch paths from HEAD. Leave HEAD at the pristine
# pre-agent snapshot so committed agent tests cannot be restored before the
# official test patch is applied.
git(
    "reset",
    "--hard",
    "-q",
    compare_ref,
    stdout=subprocess.DEVNULL,
    stderr=subprocess.DEVNULL,
    check=True,
)

# `git reset --hard` does not remove untracked files. Clean every agent-changed
# path that did not exist in the pristine snapshot, including dropped tests.
cleanup_paths = list(dict.fromkeys([*changed_paths, *official_test_paths]))
for rel in cleanup_paths:
    exists_in_snapshot = git(
        "cat-file",
        "-e",
        f"{compare_ref}:{rel}",
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    ).returncode == 0
    if exists_in_snapshot:
        continue
    target = app_dir / rel
    try:
        if target.is_dir() and not target.is_symlink():
            shutil.rmtree(target)
        elif target.exists() or target.is_symlink():
            target.unlink()
    except OSError:
        pass

# Ignored files are invisible to git diff but can still influence tests. Remove
# ignored paths created by the agent while preserving preinstalled dependency
# trees that were already present in the task image.
baseline_ignored_list = logs_dir / "agent" / "deepswe_baseline_ignored.json"
current_ignored_raw = git(
    "ls-files",
    "--others",
    "--ignored",
    "--exclude-standard",
    "-z",
    stdout=subprocess.PIPE,
    stderr=subprocess.DEVNULL,
).stdout
current_untracked_raw = git(
    "ls-files",
    "--others",
    "--exclude-standard",
    "-z",
    stdout=subprocess.PIPE,
    stderr=subprocess.DEVNULL,
).stdout
current_untracked = [
    value.decode("utf-8", "surrogateescape")
    for value in (current_ignored_raw + current_untracked_raw).split(b"\\0")
    if value
]
baseline_ignored = (
    set(json.loads(baseline_ignored_list.read_text()))
    if baseline_ignored_list.is_file()
    else set()
)
for rel in (
    path
    for path in dict.fromkeys(current_untracked)
    if path not in baseline_ignored
):
    target = app_dir / rel
    try:
        if target.is_dir() and not target.is_symlink():
            shutil.rmtree(target)
        elif target.exists() or target.is_symlink():
            target.unlink()
    except OSError:
        pass
"""


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


def _is_local_deepswe_job_config(config: Any) -> bool:
    for dataset in getattr(config, "datasets", ()):
        path = getattr(dataset, "path", None)
        if path is not None and _is_local_deepswe_dataset(str(path)):
            return True
    return False


def _retry_include_exceptions(args: argparse.Namespace) -> set[str] | None:
    retry_include = _flatten_list_values(args.retry_include)
    if retry_include:
        return set(retry_include)
    if args.max_retries > 0 and _is_local_deepswe_dataset(args.dataset):
        return set(DEEPSWE_TRANSIENT_RETRY_EXCEPTIONS)
    return None


def _deepswe_official_test_patch_paths(task_dir: Path) -> list[str]:
    patch_path = task_dir / "tests" / "test.patch"
    if not patch_path.is_file():
        return []
    result = subprocess.run(
        ["git", "apply", "--numstat", "-z"],
        input=patch_path.read_bytes(),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"Could not parse DeepSWE official test patch {patch_path}: "
            + result.stderr.decode("utf-8", "replace").strip()
        )

    chunks = result.stdout.split(b"\0")
    paths: list[str] = []
    index = 0
    while index < len(chunks):
        record = chunks[index]
        index += 1
        if not record:
            continue
        fields = record.split(b"\t", 2)
        if len(fields) != 3:
            raise RuntimeError(
                f"Unexpected git numstat record in {patch_path}: {record!r}"
            )
        encoded_path = fields[2]
        if encoded_path:
            candidates = [encoded_path]
        else:
            if index + 1 >= len(chunks):
                raise RuntimeError(f"Incomplete rename record in {patch_path}")
            candidates = [chunks[index], chunks[index + 1]]
            index += 2
        for candidate in candidates:
            path = candidate.decode("utf-8", "surrogateescape")
            parsed = Path(path)
            if path and not parsed.is_absolute() and ".." not in parsed.parts:
                paths.append(path)
    return list(dict.fromkeys(paths))


_GO_BUILD_EVENT_FILTER = """grep -v '"Action":"build-'"""
_GO_BUILD_EVENT_LOG_PIPE = re.compile(
    r"\|\s*"
    + re.escape(_GO_BUILD_EVENT_FILTER)
    + r"\s*(?:\\\s*\n\s*)?\|\s*tee\s+-a\s+(?P<log>\"\$RUN_LOG\")"
)


def _preserve_go_build_events_in_raw_log(script: str) -> tuple[str, int]:
    """Tee raw Go JSON before filtering events unsupported by the reporter."""

    return _GO_BUILD_EVENT_LOG_PIPE.subn(
        rf"| tee -a \g<log> | {_GO_BUILD_EVENT_FILTER}",
        script,
    )


_DEEPSWE_VERIFIER_TEST_ERRATA: dict[str, dict[str, Any]] = {
    "prometheus-transactional-reload-status": {
        "path": "test.patch",
        "sha256": "e570ec05827cb951b1cd58b417e04d39805523f2abdab32dce84d7d4d8c50435",
        "replacements": (
            ("reloadStatusResponse", "olympusReloadStatusResponse", 3),
            ("getReloadStatus", "olympusGetReloadStatus", 9),
        ),
    },
}


def _apply_deepswe_verifier_test_errata(
    task_dir: Path,
    prepared_tests_dir: Path,
) -> list[str]:
    """Apply checksum-guarded fixes for confirmed hidden-test defects."""

    erratum = _DEEPSWE_VERIFIER_TEST_ERRATA.get(task_dir.name)
    if erratum is None:
        return []
    target = prepared_tests_dir / str(erratum["path"])
    content = target.read_text(encoding="utf-8")
    replacements = erratum["replacements"]
    if all(old not in content for old, _new, _count in replacements):
        return []
    digest = hashlib.sha256(target.read_bytes()).hexdigest()
    if digest != erratum["sha256"]:
        raise DeepSweVerifierInfraError(
            f"DeepSWE test erratum source changed for {task_dir.name}: "
            f"expected={erratum['sha256']} actual={digest}"
        )

    applied: list[str] = []
    for old, new, expected_count in replacements:
        actual_count = content.count(old)
        if actual_count != expected_count:
            raise DeepSweVerifierInfraError(
                f"DeepSWE test erratum count mismatch for {task_dir.name}: "
                f"identifier={old} expected={expected_count} actual={actual_count}"
            )
        content = content.replace(old, new)
        applied.append(f"{old}->{new}:{actual_count}")
    target.write_text(content, encoding="utf-8")
    return applied


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


def _deepswe_verify_after_agent_timeout(original: Any) -> Any:
    """Always grade the DeepSWE workspace snapshot left at the time limit."""

    @wraps(original)
    async def verify_deepswe_timeout_snapshot(self: Any) -> bool:
        if _deepswe_artifact_hook_info(self) is not None:
            self._logger.info(
                "DeepSWE agent timed out; verifying the final workspace snapshot"
            )
            return True
        return await original(self)

    verify_deepswe_timeout_snapshot._skills_evo_deepswe_timeout_verifier_patch = True  # type: ignore[attr-defined]
    return verify_deepswe_timeout_snapshot


def _deepswe_repeated_pytest_timeout_node(trial_dir: Path) -> str | None:
    """Return a node only when two fresh verifiers visibly hang at the same test."""

    outputs: list[str] = []
    for attempt in (1, 2):
        path = (
            trial_dir
            / "verifier_attempts"
            / f"timeout_attempt_{attempt}"
            / "test-stdout.txt"
        )
        try:
            output = path.read_text(encoding="utf-8", errors="replace").rstrip()
        except OSError:
            return None
        if (
            not output
            or "base-mode smoke-import gate exit code: 0" not in output
            or "Running new mode:" not in output
        ):
            return None
        outputs.append(output)
    if outputs[0] != outputs[1]:
        return None

    final_line = outputs[0].splitlines()[-1].strip()
    match = _DEEPSWE_PYTEST_NODE.fullmatch(final_line)
    return match.group(0) if match is not None else None


def _record_deepswe_terminal_verifier_timeout(trial: Any, node: str) -> None:
    path = (
        trial._trial_paths.trial_dir
        / "verifier_attempts"
        / "terminal_timeout.json"
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "classification": "deterministic-model-test-timeout",
                "assigned_reward": 0,
                "fresh_verifier_attempts": 2,
                "repeated_pytest_node": node,
                "recorded_at": _utc_now(),
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )


def _deepswe_fresh_environment_verifier_retry(
    original_verify_with_retry: Any,
) -> Any:
    """Retry DeepSWE verifier infrastructure failures in a pristine sandbox."""

    from harbor.trial.trial import VerifierTimeoutError

    verify_once = getattr(
        original_verify_with_retry,
        "__wrapped__",
        original_verify_with_retry,
    )

    @wraps(original_verify_with_retry)
    async def verify_without_deepswe_same_sandbox_retry(self: Any) -> Any:
        if _deepswe_artifact_hook_info(self) is None or not getattr(
            self,
            "_skills_evo_deepswe_fresh_verifier_environment",
            False,
        ):
            return await original_verify_with_retry(self)
        for attempt in range(1, 3):
            try:
                result = await verify_once(self)
            except DeepSweVerifierInfraError as exc:
                await _download_deepswe_verifier_logs_best_effort(
                    self,
                    label=f"infra_attempt_{attempt}",
                )
                if attempt >= 2:
                    raise
                self._logger.warning(
                    "DeepSWE verifier infrastructure failure (%s); retrying "
                    "the same model.patch once in a fresh verifier sandbox",
                    exc,
                )
                self.result.verifier_result = None
                await _replace_deepswe_verifier_environment(
                    self,
                    retry_index=attempt,
                    stop_current=True,
                )
                continue
            except VerifierTimeoutError:
                await _download_deepswe_verifier_logs_best_effort(
                    self,
                    label=f"timeout_attempt_{attempt}",
                )
                if attempt >= 2:
                    node = _deepswe_repeated_pytest_timeout_node(
                        self._trial_paths.trial_dir
                    )
                    if node is None:
                        raise
                    from harbor.models.verifier.result import VerifierResult

                    self.result.verifier_result = VerifierResult(
                        rewards={"reward": 0}
                    )
                    _record_deepswe_terminal_verifier_timeout(self, node)
                    self._logger.warning(
                        "DeepSWE verifier timed out twice at the same hidden test "
                        "%s; recording a model-outcome reward of 0",
                        node,
                    )
                    return None
                self._logger.warning(
                    "DeepSWE verifier timed out; retrying the same model.patch "
                    "once in a fresh verifier sandbox"
                )
                self.result.verifier_result = None
                await _replace_deepswe_verifier_environment(
                    self,
                    retry_index=attempt,
                    stop_current=True,
                )
                continue
            negative_reward = _negative_deepswe_reward(self.result.verifier_result)
            if negative_reward is None:
                return result
            await _download_deepswe_verifier_logs_best_effort(
                self,
                label=f"negative_reward_attempt_{attempt}",
            )
            if attempt >= 2:
                raise DeepSweVerifierInfraError(
                    "DeepSWE verifier returned negative reward "
                    f"{negative_reward} in two fresh sandboxes"
                )
            self._logger.warning(
                "DeepSWE verifier returned negative reward %s; retrying the "
                "same model.patch once in a fresh verifier sandbox",
                negative_reward,
            )
            self.result.verifier_result = None
            await _replace_deepswe_verifier_environment(
                self,
                retry_index=attempt,
                stop_current=True,
            )
        raise AssertionError("unreachable")

    verify_without_deepswe_same_sandbox_retry._skills_evo_deepswe_fresh_verifier_retry_patch = True  # type: ignore[attr-defined]
    return verify_without_deepswe_same_sandbox_retry


def _deepswe_verifier_task_environment(trial: Any) -> Any:
    """Recover the separate verifier environment ignored by Harbor 0.3.0."""

    hook_info = _deepswe_artifact_hook_info(trial)
    if hook_info is None:
        raise DeepSweVerifierInfraError("DeepSWE task metadata is unavailable")
    task_dir = hook_info[0].parent
    try:
        task_toml = tomllib.loads((task_dir / "task.toml").read_text(encoding="utf-8"))
    except Exception as exc:
        raise DeepSweVerifierInfraError(
            f"Could not load DeepSWE verifier environment metadata: {exc}"
        ) from exc

    verifier = task_toml.get("verifier")
    if not isinstance(verifier, dict) or verifier.get("environment_mode") != "separate":
        raise DeepSweVerifierInfraError(
            "DeepSWE task must declare verifier.environment_mode='separate'"
        )
    overrides = verifier.get("environment")
    if not isinstance(overrides, dict):
        raise DeepSweVerifierInfraError(
            "DeepSWE task is missing [verifier.environment]"
        )

    task_environment = trial._task.config.environment
    allowed = set(type(task_environment).model_fields)
    unknown = sorted(set(overrides) - allowed)
    if unknown:
        raise DeepSweVerifierInfraError(
            "Unsupported DeepSWE verifier environment fields: " + ", ".join(unknown)
        )
    values = task_environment.model_dump()
    values.update(overrides)
    values["mcp_servers"] = []
    values["skills_dir"] = None
    return type(task_environment).model_validate(values)


def _deepswe_verifier_trial_environment_config(
    trial: Any,
    task_environment: Any,
) -> Any:
    environment_config = trial.config.environment.model_copy(deep=True)
    environment_config.force_build = False
    environment_config.delete = True
    environment_config.env = {}
    environment_config.override_cpus = task_environment.cpus
    environment_config.override_memory_mb = task_environment.memory_mb
    environment_config.override_storage_mb = task_environment.storage_mb
    environment_config.override_gpus = task_environment.gpus
    environment_config.suppress_override_warnings = True
    source_kwargs = environment_config.kwargs
    environment_config.kwargs = {
        key: source_kwargs[key]
        for key in (
            "template_namespace",
            "strip_dockerfile_comments",
            "sandbox_timeout_sec",
        )
        if key in source_kwargs
    }
    environment_config.kwargs.update(
        {
            "pi_template_suffix": "",
            "force_allow_internet": False,
        }
    )
    return environment_config


async def _ensure_deepswe_agent_logs_before_isolation(
    trial: Any,
    *,
    result_only: bool,
) -> None:
    if result_only:
        return
    from harbor.models.trial.paths import EnvironmentPaths

    required = _required_deepswe_artifact_paths(trial, result_only=False)[1:]
    missing = [path for path in required if not path.is_file()]
    for attempt in range(1, DEEPSWE_ARTIFACT_DOWNLOAD_ATTEMPTS + 1):
        if not missing:
            return
        if attempt > 1:
            await asyncio.sleep(attempt - 1)
        trial._are_agent_logs_downloaded = False
        await trial._maybe_download_logs(
            source_dir=EnvironmentPaths.agent_dir.as_posix(),
            target_dir=trial._trial_paths.agent_dir,
        )
        missing = [path for path in required if not path.is_file()]
    if missing:
        raise DeepSweArtifactDownloadError(
            "Required DeepSWE agent logs are missing before verifier isolation: "
            + ", ".join(str(path) for path in missing)
        )


async def _replace_deepswe_verifier_environment(
    trial: Any,
    *,
    retry_index: int,
    stop_current: bool,
) -> None:
    from harbor.environments.factory import EnvironmentFactory
    from harbor.models.trial.paths import EnvironmentPaths

    task_environment = _deepswe_verifier_task_environment(trial)
    local_patch = trial._trial_paths.artifacts_dir / "model.patch"
    if not local_patch.is_file():
        raise DeepSweVerifierInfraError(
            "Local DeepSWE model.patch is unavailable for isolated verification"
        )
    if stop_current:
        await trial._environment.stop(delete=True)

    shutil.rmtree(trial._trial_paths.verifier_dir, ignore_errors=True)
    trial._trial_paths.verifier_dir.mkdir(parents=True, exist_ok=True)
    environment_config = _deepswe_verifier_trial_environment_config(
        trial,
        task_environment,
    )

    verifier_environment = EnvironmentFactory.create_environment_from_config(
        config=environment_config,
        environment_dir=trial._task.paths.environment_dir,
        environment_name=trial._task.name,
        session_id=(
            f"{trial.config.trial_name}-verifier"
            + (f"-retry{retry_index}" if retry_index else "")
        ),
        trial_paths=trial._trial_paths,
        task_env_config=task_environment,
        logger=trial._logger,
    )
    trial._environment = verifier_environment
    multiplier = (
        trial.config.environment_build_timeout_multiplier
        if trial.config.environment_build_timeout_multiplier is not None
        else trial.config.timeout_multiplier
    )
    build_timeout_sec = task_environment.build_timeout_sec * multiplier
    last_error: Exception | None = None
    for start_attempt in range(1, 3):
        try:
            await asyncio.wait_for(
                verifier_environment.start(force_build=False),
                timeout=build_timeout_sec,
            )
            last_error = None
            break
        except Exception as exc:
            last_error = exc
            await verifier_environment.stop(delete=True)
            if start_attempt < 2:
                trial._logger.warning(
                    "Fresh DeepSWE verifier environment failed to start; "
                    "retrying locally (%d/2): %s",
                    start_attempt,
                    exc,
                )
                await asyncio.sleep(start_attempt)
    if last_error is not None:
        raise DeepSweVerifierInfraError(
            "Fresh DeepSWE verifier environment could not start after two attempts: "
            f"{type(last_error).__name__}: {last_error}"
        ) from last_error

    verifier_environment.default_user = trial._task.config.verifier.user
    mkdir_result = await verifier_environment.exec(
        command="mkdir -p /logs/artifacts && chmod 777 /logs/artifacts",
        user="root",
        timeout_sec=30,
    )
    if mkdir_result.return_code != 0:
        raise DeepSweVerifierInfraError(
            "Could not prepare the isolated DeepSWE verifier artifact directory"
        )
    await verifier_environment.upload_file(
        source_path=local_patch,
        target_path=f"{EnvironmentPaths.artifacts_dir.as_posix()}/model.patch",
    )
    trial._skills_evo_deepswe_fresh_verifier_environment = True


async def _download_deepswe_verifier_logs_best_effort(
    trial: Any,
    *,
    label: str,
) -> None:
    from harbor.models.trial.paths import EnvironmentPaths

    target = trial._trial_paths.trial_dir / "verifier_attempts" / label
    try:
        target.mkdir(parents=True, exist_ok=True)
        await trial._environment.download_dir(
            source_dir=EnvironmentPaths.verifier_dir.as_posix(),
            target_dir=target,
        )
    except Exception as exc:
        trial._logger.warning(
            "Could not preserve partial DeepSWE verifier logs (%s): %s",
            label,
            exc,
        )
    verifier_result = trial.result.verifier_result
    if verifier_result is None:
        return
    try:
        if hasattr(verifier_result, "model_dump_json"):
            payload = verifier_result.model_dump_json(indent=2)
        else:
            payload = json.dumps(
                getattr(verifier_result, "rewards", None),
                indent=2,
            )
        (target / "verifier_result.json").write_text(
            payload + "\n",
            encoding="utf-8",
        )
    except Exception as exc:
        trial._logger.warning(
            "Could not preserve DeepSWE verifier result snapshot (%s): %s",
            label,
            exc,
        )


async def _move_deepswe_verification_to_fresh_environment(
    trial: Any,
    *,
    result_only: bool = False,
) -> None:
    """Move DeepSWE grading to a pristine, offline sandbox."""

    if getattr(trial, "_skills_evo_deepswe_fresh_verifier_environment", False):
        return
    from harbor.models.trial.paths import EnvironmentPaths

    local_patch = trial._trial_paths.artifacts_dir / "model.patch"
    local_patch.parent.mkdir(parents=True, exist_ok=True)
    local_patch.unlink(missing_ok=True)
    agent_environment = trial._environment
    await agent_environment.download_file(
        source_path=f"{EnvironmentPaths.artifacts_dir.as_posix()}/model.patch",
        target_path=local_patch,
    )
    if not local_patch.is_file():
        raise DeepSweVerifierInfraError(
            "DeepSWE model.patch was not downloaded before verifier isolation"
        )
    await _ensure_deepswe_agent_logs_before_isolation(
        trial,
        result_only=result_only,
    )
    await agent_environment.stop(delete=True)
    await _replace_deepswe_verifier_environment(
        trial,
        retry_index=0,
        stop_current=False,
    )
    trial._skills_evo_deepswe_fresh_verifier_environment = True
    trial._logger.info(
        "DeepSWE verifier isolation active: fresh sandbox, internet disabled, "
        "agent environment variables removed"
    )


def _negative_deepswe_reward(result: Any) -> float | None:
    rewards = getattr(result, "rewards", None)
    if not isinstance(rewards, dict):
        return None
    value = rewards.get("reward")
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value) if value < 0 else None


def _deepswe_dependency_manifest_path(path: str) -> bool:
    name = PurePosixPath(path).name.lower()
    return (
        name in _DEEPSWE_DEPENDENCY_MANIFEST_NAMES
        or (name.startswith("package") and name.endswith(".json"))
        or (name.startswith("requirements") and name.endswith(".txt"))
        or (name.startswith("constraints") and name.endswith(".txt"))
        or name.startswith("build.gradle")
        or name.startswith("settings.gradle")
        or name.endswith(".gemspec")
    )


def _deepswe_model_patch_dependency_paths(
    model_patch_path: Path,
) -> list[str] | None:
    """Return dependency/toolchain manifests changed by a parsed git patch."""

    try:
        completed = subprocess.run(
            ["git", "apply", "--numstat", "-z", "--", str(model_patch_path)],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
    except OSError:
        return None
    if completed.returncode != 0:
        return None
    changed_paths: list[str] = []
    for record in completed.stdout.split(b"\0"):
        if not record:
            continue
        fields = record.split(b"\t", 2)
        if len(fields) != 3:
            return None
        path = fields[2].decode("utf-8", "surrogateescape")
        if _deepswe_dependency_manifest_path(path):
            changed_paths.append(path)
    return changed_paths


def _deepswe_verifier_suite_infra_reason(
    verifier_dir: Path,
    model_patch_path: Path,
) -> str | None:
    """Detect an offline dependency failure that prevented every scored test."""

    changed_dependency_paths = _deepswe_model_patch_dependency_paths(model_patch_path)
    if changed_dependency_paths is None or changed_dependency_paths:
        return None

    ctrf_path = verifier_dir / "ctrf.json"
    try:
        ctrf = json.loads(ctrf_path.read_text(encoding="utf-8"))
        tests = ctrf["results"]["tests"]
    except (OSError, UnicodeError, json.JSONDecodeError, KeyError, TypeError):
        return None
    if not isinstance(tests, list) or not tests:
        return None
    if not all(
        isinstance(test, dict)
        and _DEEPSWE_MISSING_TEST_RESULT_MARKER
        in str(test.get("message") or "").lower()
        for test in tests
    ):
        return None

    stdout_path = verifier_dir / "test-stdout.txt"
    try:
        with stdout_path.open(encoding="utf-8", errors="replace") as stream:
            for line in stream:
                for (
                    fetch_label,
                    fetch_pattern,
                ) in _DEEPSWE_OFFLINE_TOOLCHAIN_FETCH_PATTERNS:
                    if not fetch_pattern.search(line):
                        continue
                    for (
                        failure_label,
                        failure_pattern,
                    ) in _DEEPSWE_OFFLINE_NETWORK_FAILURE_PATTERNS:
                        if failure_pattern.search(line):
                            return (
                                "DeepSWE verifier suite did not run in the offline "
                                f"sandbox because a {fetch_label} fetch failed "
                                f"({failure_label}); every scored test was missing"
                            )
    except OSError:
        return None
    return None


def _classify_deepswe_provider_auth_failure(result: Any) -> bool:
    exception_info = getattr(result, "exception_info", None)
    if exception_info is None:
        return False
    if getattr(exception_info, "exception_type", "") != "NonZeroAgentExitCodeError":
        return False
    message = str(getattr(exception_info, "exception_message", "")).lower()
    classifications = (
        ("provider authentication failed", "DeepSweProviderAuthenticationError"),
        ("claude provider transient failure", "DeepSweProviderTransientError"),
        (
            "claude agent stream ended without resultmessage",
            "DeepSweAgentIncompleteError",
        ),
        ("claude agent resultmessage reported an error", "DeepSweAgentIncompleteError"),
        ("claude agent sdk execution failed", "DeepSweAgentIncompleteError"),
    )
    for marker, exception_type in classifications:
        if marker in message:
            exception_info.exception_type = exception_type
            return True
    return False


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _sha256_tree(root: Path, files: list[Path] | None = None) -> str:
    digest = hashlib.sha256()
    selected = files
    if selected is None:
        selected = sorted(
            path for path in root.rglob("*") if path.is_file() and not path.is_symlink()
        )
    for path in selected:
        relative = path.relative_to(root).as_posix().encode("utf-8")
        digest.update(len(relative).to_bytes(8, "big"))
        digest.update(relative)
        digest.update(bytes.fromhex(_sha256_file(path)))
    return digest.hexdigest()


def _deepswe_resume_contract(args: argparse.Namespace, config: Any) -> dict[str, Any]:
    from importlib.metadata import PackageNotFoundError, version

    provider = resolve_provider(args.provider)
    agent = _agent_name(args.agent)
    endpoint = (
        provider.anthropic_base_url if agent == "claude-code" else provider.base_url
    )
    dataset = config.datasets[0] if config.datasets else None
    dataset_path = getattr(dataset, "path", None)
    dataset_contract: dict[str, Any] = {
        "path": str(Path(dataset_path).expanduser().resolve())
        if dataset_path is not None
        else None,
        "task_names": sorted(getattr(dataset, "task_names", None) or []),
    }
    if dataset_path is not None:
        dataset_contract["tree_sha256"] = _sha256_tree(
            Path(dataset_path).expanduser().resolve()
        )

    skills_contract: dict[str, Any] = {"enabled": bool(args.use_skills)}
    if args.use_skills:
        from agents.pi_agent import (
            _active_task_skill_roots,
            _discover_pi_skills_from_roots,
            _iter_pi_skill_pack_files,
        )

        skill_roots = [
            root.expanduser().resolve() for root in _active_task_skill_roots()
        ]
        missing_roots = [str(root) for root in skill_roots if not root.is_dir()]
        if missing_roots:
            raise RuntimeError(
                "DeepSWE skill roots are missing: " + ", ".join(missing_roots)
            )
        skills_contract.update(
            {
                "roots": [str(root) for root in skill_roots],
                "tree_sha256": [
                    _sha256_tree(root, _iter_pi_skill_pack_files(root))
                    for root in skill_roots
                ],
                "discovered_count": len(_discover_pi_skills_from_roots(skill_roots)),
                "retrieval_scope": os.getenv("PI_SKILL_RETRIEVAL_SCOPE", ""),
                "pi_harness_memory": os.getenv("PI_USE_SKILL_HARNESS_MEMORY", ""),
                "claude_harness_memory": os.getenv(
                    "CLAUDE_USE_SKILL_HARNESS_MEMORY", ""
                ),
            }
        )

        def env_bool(name: str, default: bool) -> bool:
            value = os.getenv(name)
            if value is None:
                return default
            return value.strip().lower() in {"1", "true", "yes", "on"}

        memory_enabled = env_bool(
            "CLAUDE_USE_SKILL_HARNESS_MEMORY",
            env_bool("PI_USE_SKILL_HARNESS_MEMORY", True),
        )
        skills_contract["harness_memory_enabled"] = memory_enabled
        if memory_enabled:
            from agents.skill_harness_memory import memory_path_from_env

            memory_path = memory_path_from_env().expanduser().resolve()
            skills_contract["harness_memory_path"] = str(memory_path)
            skills_contract["harness_memory_sha256"] = (
                _sha256_file(memory_path) if memory_path.is_file() else None
            )

    code_paths = [
        ROOT / "agents" / "claude_sdk_agent.py",
        ROOT / "agents" / "pi_agent.py",
        ROOT / "agents" / "skill_harness_memory.py",
        ROOT / "environments" / "e2b_swebench.py",
        ROOT / "providers" / "__init__.py",
        ROOT / "providers" / "specs.py",
        ROOT / "scripts" / "run_benchmark.py",
    ]
    runtime_knob_names = (
        "FORCE_DISABLE_THINKING",
        "HARBOR_CLAUDE_DISABLE_THINKING_PROXY",
        "HARBOR_CLAUDE_KEEP_PROXY",
        "CLAUDE_CODE_MAX_OUTPUT_TOKENS",
        "TIMEOUT_MULTIPLIER",
        "AGENT_TIMEOUT_MULTIPLIER",
        "VERIFIER_TIMEOUT_MULTIPLIER",
        "AGENT_SETUP_TIMEOUT_MULTIPLIER",
        "ENVIRONMENT_BUILD_TIMEOUT_MULTIPLIER",
        "PI_SKILL_RETRIEVAL_SCOPE",
        "PI_MIN_ACTIVE_SKILL_QUALITY",
        "PI_USE_SKILL_HARNESS_MEMORY",
        "CLAUDE_USE_SKILL_HARNESS_MEMORY",
        "PI_SKILL_HARNESS_MEMORY_PATH",
        "PI_SKILL_HARNESS_MEMORY_MAX_ENTRIES",
        "PI_SKILL_HARNESS_MEMORY_MAX_CHARS",
        "PI_MIN_MEMORY_SKILL_QUALITY",
        "CLAUDE_SKILL_PROMPT_MAX_CHARS_PER_SKILL",
        f"{provider.env_prefix}_REASONING_EFFORT",
        f"{provider.env_prefix}_ENABLE_THINKING",
    )
    dependency_versions: dict[str, str] = {}
    for package in ("harbor", "e2b", "claude-agent-sdk"):
        try:
            dependency_versions[package] = version(package)
        except PackageNotFoundError:
            dependency_versions[package] = "not-installed"
    if agent == "claude-code":
        dependency_versions["claude-agent-sdk"] = args.claude_sdk_version
    elif agent == "pi":
        dependency_versions["pi-coding-agent"] = args.pi_version
    return {
        "version": DEEPSWE_RESUME_CONTRACT_VERSION,
        "artifact_hook_version": DEEPSWE_ARTIFACT_HOOK_VERSION,
        "artifact_hook_enabled": bool(
            args.enable_deepswe_pre_artifacts and not args.disable_deepswe_pre_artifacts
        ),
        "force_agent_internet": bool(args.force_agent_internet),
        "provider": {
            "name": provider.name,
            "model": provider.model,
            "endpoint": endpoint,
            "agent": agent,
            "provider_api": provider.provider_api if agent == "pi" else None,
        },
        "agent_parameters": (
            {
                "max_turns": args.claude_max_turns,
                "max_budget_usd": args.claude_max_budget_usd,
            }
            if agent == "claude-code"
            else {
                "model_context_window": args.model_context_window,
                "model_max_tokens": args.model_max_tokens,
                "thinking": args.thinking,
                "tools": args.tools,
            }
        ),
        "dataset": dataset_contract,
        "skills": skills_contract,
        "runtime_knobs": {name: os.getenv(name) for name in runtime_knob_names},
        "dependency_versions": dependency_versions,
        "code_sha256": {
            str(path.relative_to(ROOT)): _sha256_file(path) for path in code_paths
        },
    }


def _attach_deepswe_resume_contract(config: Any, contract: dict[str, Any]) -> Any:
    attached = config.model_copy(deep=True)
    for agent in attached.agents:
        kwargs = dict(agent.kwargs or {})
        kwargs["resume_contract"] = contract
        if "claude_sdk_agent" in str(agent.import_path or ""):
            kwargs["claude_sdk_version"] = contract["dependency_versions"][
                "claude-agent-sdk"
            ]
        elif "pi_agent" in str(agent.import_path or ""):
            kwargs["version"] = contract["dependency_versions"]["pi-coding-agent"]
        agent.kwargs = kwargs
    return attached


def _saved_deepswe_resume_contract(config: Any) -> dict[str, Any] | None:
    contracts = [(agent.kwargs or {}).get("resume_contract") for agent in config.agents]
    contracts = [contract for contract in contracts if isinstance(contract, dict)]
    if not contracts:
        return None
    if any(contract != contracts[0] for contract in contracts[1:]):
        raise RuntimeError("Saved DeepSWE agents have inconsistent resume contracts")
    return contracts[0]


def _validate_historical_deepswe_identity(
    job_dir: Path,
    config: Any,
    contract: dict[str, Any],
) -> None:
    saved_contract = _saved_deepswe_resume_contract(config)
    if saved_contract is not None:
        if saved_contract != contract:
            raise RuntimeError(
                "Refusing DeepSWE resume because the model, endpoint, dataset, "
                "skills, or runner code changed"
            )
        return

    provider_contract = contract["provider"]
    for agent in config.agents:
        if agent.model_name and agent.model_name != (
            f"{provider_contract['name']}/{provider_contract['model']}"
        ):
            raise RuntimeError(
                "Refusing legacy DeepSWE resume with a different provider model: "
                f"saved={agent.model_name} current="
                f"{provider_contract['name']}/{provider_contract['model']}"
            )

    models: set[str] = set()
    endpoints: set[str] = set()
    discovered_counts: set[int] = set()
    sdk_versions: set[str] = set()
    for metadata_path in job_dir.glob("*/agent/claude-agent-metadata.json"):
        try:
            metadata = json.loads(metadata_path.read_text(errors="replace"))
        except (OSError, json.JSONDecodeError):
            continue
        if metadata.get("provider_model"):
            models.add(str(metadata["provider_model"]))
        if metadata.get("provider_base_url"):
            endpoints.add(str(metadata["provider_base_url"]).rstrip("/"))
        if metadata.get("claude_agent_sdk_version"):
            sdk_versions.add(str(metadata["claude_agent_sdk_version"]))
        transferable = metadata.get("transferable_skills")
        if isinstance(transferable, dict) and isinstance(
            transferable.get("all_discovered_count"), int
        ):
            discovered_counts.add(int(transferable["all_discovered_count"]))
    if models and models != {str(provider_contract["model"])}:
        raise RuntimeError(
            f"Legacy DeepSWE model evidence does not match current model: {models}"
        )
    current_endpoint = str(provider_contract["endpoint"]).rstrip("/")
    if endpoints and endpoints != {current_endpoint}:
        raise RuntimeError(
            "Legacy DeepSWE endpoint evidence does not match current endpoint: "
            f"saved={sorted(endpoints)} current={current_endpoint}"
        )
    current_skill_count = contract["skills"].get("discovered_count")
    if discovered_counts and discovered_counts != {current_skill_count}:
        raise RuntimeError(
            "Legacy DeepSWE skill-pack evidence does not match current roots: "
            f"saved={sorted(discovered_counts)} current={current_skill_count}"
        )
    current_sdk_version = contract["dependency_versions"].get("claude-agent-sdk")
    if sdk_versions and sdk_versions != {current_sdk_version}:
        raise RuntimeError(
            "Legacy DeepSWE Claude SDK evidence does not match the pinned version: "
            f"saved={sorted(sdk_versions)} current={current_sdk_version}"
        )


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
            "version": args.pi_version,
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
            "claude_sdk_version": args.claude_sdk_version,
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
    is_deepswe_dataset = _is_local_deepswe_dataset(args.dataset)
    if retry_include:
        retry_kwargs["include_exceptions"] = set(retry_include)
    if retry_exclude is not None:
        retry_exclusions = set(retry_exclude)
        if is_deepswe_dataset:
            retry_exclusions -= DEEPSWE_TRANSIENT_RETRY_EXCEPTIONS
        retry_kwargs["exclude_exceptions"] = retry_exclusions
    elif is_deepswe_dataset and args.max_retries > 0:
        retry_kwargs["exclude_exceptions"] = (
            set(RetryConfig().exclude_exceptions or ())
            - DEEPSWE_TRANSIENT_RETRY_EXCEPTIONS
        )

    agent = _agent_name(args.agent)
    metrics = []
    if not is_deepswe_dataset:
        metrics.append(MetricConfig(type=MetricType.MEAN))
    return JobConfig(
        job_name=args.job_name,
        jobs_dir=args.jobs_dir.expanduser().resolve(),
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
                "pi_template_suffix": args.e2b_pi_template_suffix
                if agent == "pi"
                else "",
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


def _load_saved_job_config(args: argparse.Namespace) -> Any | None:
    if not args.resume_existing:
        return None
    job_dir = args.jobs_dir.expanduser().resolve() / args.job_name
    config_path = job_dir / "config.json"
    if not config_path.is_file():
        return None

    from harbor.models.job.config import JobConfig

    config = JobConfig.model_validate_json(config_path.read_text())
    configured_job_dir = (config.jobs_dir / config.job_name).resolve()
    if configured_job_dir != job_dir.resolve():
        raise SystemExit(
            "Saved Harbor config points at a different job directory: "
            f"requested={job_dir.resolve()} configured={configured_job_dir}"
        )
    return config


def _apply_saved_config_to_args(args: argparse.Namespace, config: Any) -> None:
    requested_provider = getattr(args, "_requested_provider", None)
    requested_dataset = getattr(args, "_requested_dataset", None)
    requested_use_skills = getattr(args, "_requested_use_skills", None)
    requested_task_names = getattr(args, "_requested_task_names", None)
    requested_agent = getattr(args, "_requested_agent", None)
    args.job_name = config.job_name
    args.jobs_dir = config.jobs_dir
    args.concurrency = config.n_concurrent_trials
    args.max_retries = config.retry.max_retries
    args.override_cpus = config.environment.override_cpus
    args.override_memory_mb = config.environment.override_memory_mb
    args.override_storage_mb = config.environment.override_storage_mb
    env_kwargs = config.environment.kwargs or {}
    args.e2b_sandbox_timeout_sec = int(
        env_kwargs.get("sandbox_timeout_sec") or args.e2b_sandbox_timeout_sec
    )
    args.force_agent_internet = bool(
        env_kwargs.get("force_allow_internet", args.force_agent_internet)
    )
    if config.datasets:
        dataset = config.datasets[0]
        if dataset.path is not None:
            args.dataset = str(dataset.path)
        elif dataset.name is not None:
            dataset_ref = dataset.ref or dataset.version
            args.dataset = (
                f"{dataset.name}@{dataset_ref}" if dataset_ref else dataset.name
            )
        if requested_dataset is not None:
            requested_path = Path(requested_dataset).expanduser()
            saved_path = Path(args.dataset).expanduser()
            requested_identity = (
                str(requested_path.resolve())
                if requested_path.exists()
                else _pin_dataset(str(requested_dataset))
            )
            saved_identity = (
                str(saved_path.resolve())
                if saved_path.exists()
                else _pin_dataset(str(args.dataset))
            )
            if requested_identity != saved_identity:
                raise SystemExit(
                    "Refusing to resume with a different dataset: "
                    f"requested={requested_identity} saved={saved_identity}"
                )
        if requested_task_names is not None:
            saved_task_names = set(dataset.task_names or [])
            if set(requested_task_names) != saved_task_names:
                raise SystemExit(
                    "Refusing to resume with a different task set: "
                    f"requested={len(requested_task_names)} "
                    f"saved={len(saved_task_names)}"
                )
    if config.agents:
        agent = config.agents[0]
        saved_agent = (
            "claude-code"
            if "claude_sdk_agent" in str(agent.import_path or "")
            else "pi"
        )
        if requested_agent is not None and _agent_name(requested_agent) != saved_agent:
            raise SystemExit(
                "Refusing to resume with a different agent harness: "
                f"requested={requested_agent} saved={saved_agent}"
            )
        args.agent = saved_agent
        agent_kwargs = agent.kwargs or {}
        saved_provider = str(agent_kwargs.get("provider_name") or "")
        if saved_provider:
            normalized_saved_provider = normalize_provider_name(saved_provider)
            if (
                requested_provider is not None
                and normalize_provider_name(requested_provider)
                != normalized_saved_provider
            ):
                raise SystemExit(
                    "Refusing to resume with a different provider: "
                    f"requested={requested_provider} saved={normalized_saved_provider}"
                )
            args.provider = normalized_saved_provider
        args.benchmark_name = str(
            agent_kwargs.get("benchmark_name") or args.benchmark_name
        )
        saved_use_skills = bool(agent_kwargs.get("use_skills", args.use_skills))
        if (
            requested_use_skills is not None
            and bool(requested_use_skills) != saved_use_skills
        ):
            raise SystemExit(
                "Refusing to resume with a different skills mode: "
                f"requested={bool(requested_use_skills)} saved={saved_use_skills}"
            )
        args.use_skills = saved_use_skills
        args.result_only = bool(agent_kwargs.get("result_only", args.result_only))
        args.agent_timeout_sec = agent.override_timeout_sec
        args.agent_setup_timeout_sec = agent.override_setup_timeout_sec
        if agent_kwargs.get("claude_sdk_version"):
            args.claude_sdk_version = str(agent_kwargs["claude_sdk_version"])
        if agent_kwargs.get("version") and saved_agent == "pi":
            args.pi_version = str(agent_kwargs["version"])


def _enforce_deepswe_infra_args(args: argparse.Namespace) -> None:
    args.force_agent_internet = True
    args.override_cpus = max(args.override_cpus or 0, DEEPSWE_MIN_CPUS)
    args.override_memory_mb = max(args.override_memory_mb or 0, DEEPSWE_MIN_MEMORY_MB)
    args.override_storage_mb = max(
        args.override_storage_mb or 0, DEEPSWE_MIN_STORAGE_MB
    )
    args.max_retries = max(args.max_retries or 0, DEEPSWE_MIN_MAX_RETRIES)
    args.verifier_buffer_sec = max(
        args.verifier_buffer_sec or 0,
        DEEPSWE_MIN_VERIFIER_BUFFER_SEC,
    )
    agent_multiplier = (
        args.agent_timeout_multiplier
        if args.agent_timeout_multiplier is not None
        else args.timeout_multiplier
    )
    setup_multiplier = (
        args.agent_setup_timeout_multiplier
        if args.agent_setup_timeout_multiplier is not None
        else args.timeout_multiplier
    )
    requested_agent_timeout = (args.agent_timeout_sec or 0) * agent_multiplier
    requested_setup_timeout = (args.agent_setup_timeout_sec or 0) * setup_multiplier
    args.e2b_sandbox_timeout_sec = max(
        args.e2b_sandbox_timeout_sec or 0,
        DEEPSWE_MIN_SANDBOX_TIMEOUT_SEC,
        int(
            requested_setup_timeout + requested_agent_timeout + args.verifier_buffer_sec
        ),
    )


def _upgrade_deepswe_resume_config(
    config: Any,
    *,
    resume_contract: dict[str, Any] | None = None,
) -> Any:
    upgraded = config.model_copy(deep=True)
    upgraded.environment.override_cpus = max(
        upgraded.environment.override_cpus or 0, DEEPSWE_MIN_CPUS
    )
    upgraded.environment.override_memory_mb = max(
        upgraded.environment.override_memory_mb or 0, DEEPSWE_MIN_MEMORY_MB
    )
    upgraded.environment.override_storage_mb = max(
        upgraded.environment.override_storage_mb or 0, DEEPSWE_MIN_STORAGE_MB
    )
    upgraded.retry.max_retries = max(
        upgraded.retry.max_retries, DEEPSWE_MIN_MAX_RETRIES
    )
    retry_include = set(upgraded.retry.include_exceptions or ())
    retry_include.update(DEEPSWE_TRANSIENT_RETRY_EXCEPTIONS)
    upgraded.retry.include_exceptions = retry_include
    if upgraded.retry.exclude_exceptions is not None:
        upgraded.retry.exclude_exceptions = (
            set(upgraded.retry.exclude_exceptions) - DEEPSWE_TRANSIENT_RETRY_EXCEPTIONS
        )
    environment_kwargs = dict(upgraded.environment.kwargs or {})
    configured_agent_timeout = max(
        (agent.override_timeout_sec or 0 for agent in upgraded.agents),
        default=0,
    )
    configured_setup_timeout = max(
        (agent.override_setup_timeout_sec or 0 for agent in upgraded.agents),
        default=0,
    )
    agent_multiplier = (
        upgraded.agent_timeout_multiplier
        if upgraded.agent_timeout_multiplier is not None
        else upgraded.timeout_multiplier
    )
    setup_multiplier = (
        upgraded.agent_setup_timeout_multiplier
        if upgraded.agent_setup_timeout_multiplier is not None
        else upgraded.timeout_multiplier
    )
    environment_kwargs["sandbox_timeout_sec"] = max(
        int(environment_kwargs.get("sandbox_timeout_sec") or 0),
        DEEPSWE_MIN_SANDBOX_TIMEOUT_SEC,
        int(
            configured_setup_timeout * setup_multiplier
            + configured_agent_timeout * agent_multiplier
            + DEEPSWE_MIN_VERIFIER_BUFFER_SEC
        ),
    )
    environment_kwargs["force_allow_internet"] = True
    upgraded.environment.kwargs = environment_kwargs
    if resume_contract is not None:
        upgraded = _attach_deepswe_resume_contract(upgraded, resume_contract)
    return upgraded


def _sync_deepswe_infra_args(args: argparse.Namespace, config: Any) -> None:
    args.override_cpus = config.environment.override_cpus
    args.override_memory_mb = config.environment.override_memory_mb
    args.override_storage_mb = config.environment.override_storage_mb
    args.max_retries = config.retry.max_retries
    args.e2b_sandbox_timeout_sec = int(
        (config.environment.kwargs or {}).get("sandbox_timeout_sec")
        or args.e2b_sandbox_timeout_sec
    )
    args.verifier_buffer_sec = max(
        args.verifier_buffer_sec or 0,
        DEEPSWE_MIN_VERIFIER_BUFFER_SEC,
    )


def _job_config_without_deepswe_infra(config: Any) -> dict[str, Any]:
    payload = config.model_dump(mode="json")
    payload.pop("job_name", None)
    environment = payload.get("environment")
    if isinstance(environment, dict):
        for key in (
            "override_cpus",
            "override_memory_mb",
            "override_storage_mb",
        ):
            environment.pop(key, None)
        kwargs = environment.get("kwargs")
        if isinstance(kwargs, dict):
            kwargs.pop("sandbox_timeout_sec", None)
            kwargs.pop("force_allow_internet", None)
    payload.pop("retry", None)
    for agent in payload.get("agents") or []:
        kwargs = agent.get("kwargs") if isinstance(agent, dict) else None
        if isinstance(kwargs, dict):
            kwargs.pop("resume_contract", None)
            kwargs.pop("claude_sdk_version", None)
    return payload


def _trial_config_without_deepswe_infra(config: Any) -> dict[str, Any]:
    payload = config.model_dump(mode="json")
    payload.pop("trial_name", None)
    payload.pop("job_id", None)
    environment = payload.get("environment")
    if isinstance(environment, dict):
        for key in (
            "override_cpus",
            "override_memory_mb",
            "override_storage_mb",
        ):
            environment.pop(key, None)
        kwargs = environment.get("kwargs")
        if isinstance(kwargs, dict):
            kwargs.pop("sandbox_timeout_sec", None)
            kwargs.pop("force_allow_internet", None)
    agent = payload.get("agent")
    kwargs = agent.get("kwargs") if isinstance(agent, dict) else None
    if isinstance(kwargs, dict):
        kwargs.pop("resume_contract", None)
        kwargs.pop("claude_sdk_version", None)
    return payload


def _trial_config_is_deepswe(config: Any) -> bool:
    task = getattr(config, "task", None)
    task_path = getattr(task, "path", None)
    return task_path is not None and (Path(task_path) / "pre_artifacts.sh").is_file()


def _patch_harbor_deepswe_resume_equality() -> None:
    """Allow completed trials to retain their original resource metadata."""
    from harbor.models.trial.config import TrialConfig

    original_eq = TrialConfig.__eq__
    if getattr(original_eq, "_skills_evo_deepswe_infra_resume_patch", False):
        return

    def eq_allowing_deepswe_infra_upgrade(self: Any, other: Any) -> Any:
        result = original_eq(self, other)
        if result is True or result is NotImplemented:
            return result
        if not isinstance(other, TrialConfig):
            return False
        if not (_trial_config_is_deepswe(self) and _trial_config_is_deepswe(other)):
            return False
        return _trial_config_without_deepswe_infra(
            self
        ) == _trial_config_without_deepswe_infra(other)

    eq_allowing_deepswe_infra_upgrade._skills_evo_deepswe_infra_resume_patch = True  # type: ignore[attr-defined]
    TrialConfig.__eq__ = eq_allowing_deepswe_infra_upgrade  # type: ignore[method-assign]


def _atomic_write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        temporary.write_text(text)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _resume_archive_dir(job_dir: Path, run_id: str) -> Path:
    return job_dir.parent / ".skills-evo-infra-archive" / job_dir.name / run_id


def _migrate_deepswe_root_config(
    job_dir: Path,
    previous: Any,
    upgraded: Any,
    *,
    run_id: str,
) -> Path | None:
    if previous.model_dump(mode="json") == upgraded.model_dump(mode="json"):
        return None
    if _job_config_without_deepswe_infra(previous) != _job_config_without_deepswe_infra(
        upgraded
    ):
        raise RuntimeError(
            "Refusing DeepSWE resume migration with non-infrastructure config changes"
        )

    config_path = job_dir / "config.json"
    current_text = config_path.read_text()
    from harbor.models.job.config import JobConfig

    current = JobConfig.model_validate_json(current_text)
    if current.job_name != previous.job_name or current != previous:
        raise RuntimeError(
            f"Saved DeepSWE config changed while acquiring resume lock: {config_path}"
        )

    archive_dir = _resume_archive_dir(job_dir, run_id)
    archive_dir.mkdir(parents=True, exist_ok=True)
    before_path = archive_dir / "config.before.json"
    before_path.write_text(current_text)
    migration = {
        "migrated_at": _utc_now(),
        "job_dir": str(job_dir.resolve()),
        "before": {
            "cpus": previous.environment.override_cpus,
            "memory_mb": previous.environment.override_memory_mb,
            "storage_mb": previous.environment.override_storage_mb,
            "max_retries": previous.retry.max_retries,
            "retry_include": sorted(previous.retry.include_exceptions or ()),
            "sandbox_timeout_sec": (previous.environment.kwargs or {}).get(
                "sandbox_timeout_sec"
            ),
        },
        "after": {
            "cpus": upgraded.environment.override_cpus,
            "memory_mb": upgraded.environment.override_memory_mb,
            "storage_mb": upgraded.environment.override_storage_mb,
            "max_retries": upgraded.retry.max_retries,
            "retry_include": sorted(upgraded.retry.include_exceptions or ()),
            "sandbox_timeout_sec": (upgraded.environment.kwargs or {}).get(
                "sandbox_timeout_sec"
            ),
        },
    }
    (archive_dir / "config.migration.json").write_text(
        json.dumps(migration, indent=2, sort_keys=True) + "\n"
    )
    _atomic_write_text(config_path, upgraded.model_dump_json(indent=4))
    return before_path


def _trial_reward_summary(result: Any) -> str:
    if result is None or result.verifier_result is None:
        return "reward=<none>"
    rewards = result.verifier_result.rewards
    if not rewards:
        return "reward=<none>"
    return " ".join(f"{key}={value}" for key, value in rewards.items())


def _patch_e2b_disable_http2() -> None:
    """Force the E2B envd/API httpx transports to HTTP/1.1.

    The E2B SDK builds sandbox command/filesystem/pty transports with
    ``http2=True`` by default. Long-lived HTTP/2 channels can be terminated by
    gateway max-age GOAWAY frames during benchmark runs. Pinning these E2B
    transports to HTTP/1.1 matches the Marcronv1-Coding harness behavior.
    Override with E2B_FORCE_HTTP2=1.
    """
    if os.environ.get("E2B_FORCE_HTTP2", "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }:
        return
    import importlib

    patched: list[str] = []
    transport_classes = (
        ("e2b.api.client_sync", "TransportWithLogger"),
        ("e2b.api.client_async", "AsyncTransportWithLogger"),
    )
    for mod_name, class_name in transport_classes:
        try:
            mod = importlib.import_module(mod_name)
        except Exception:
            continue
        original_class = getattr(mod, class_name, None)
        if original_class is None or getattr(
            original_class, "_skills_evo_no_http2", False
        ):
            continue

        class Http1Transport(original_class):  # type: ignore[misc, valid-type]
            _skills_evo_no_http2 = True

            def __init__(self, *args: Any, **kwargs: Any) -> None:
                kwargs["http2"] = False
                super().__init__(*args, **kwargs)

        Http1Transport.__name__ = class_name
        Http1Transport.__qualname__ = class_name
        if hasattr(original_class, "singleton"):
            Http1Transport.singleton = None
        if hasattr(original_class, "_instances"):
            Http1Transport._instances = {}
        setattr(mod, class_name, Http1Transport)
        patched.append(f"{mod_name}.{class_name}")

    if patched:
        _log(f"e2b transports pinned to HTTP/1.1 ({len(patched)} sites)")


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
            is_deepswe = (
                deepswe_pre_artifacts and _deepswe_artifact_hook_info(self) is not None
            )
            original_task_dir = self._task.paths.task_dir
            prepared_task_dir: Path | None = None
            if is_deepswe:
                prepared_task_dir = (
                    self._trial_paths.trial_dir / ".deepswe-verifier-task"
                )
                shutil.rmtree(prepared_task_dir, ignore_errors=True)
                prepared_tests_dir = prepared_task_dir / "tests"
                shutil.copytree(self._task.paths.tests_dir, prepared_tests_dir)
                applied_errata = _apply_deepswe_verifier_test_errata(
                    original_task_dir,
                    prepared_tests_dir,
                )
                if applied_errata:
                    self._logger.info(
                        "Applied checksum-guarded DeepSWE verifier test errata: %s",
                        ", ".join(applied_errata),
                    )
                prepared_test_script = prepared_tests_dir / "test.sh"
                script, preserved_count = _preserve_go_build_events_in_raw_log(
                    prepared_test_script.read_text(encoding="utf-8")
                )
                prepared_test_script.write_text(script, encoding="utf-8")
                self._task.paths.task_dir = prepared_task_dir
                if preserved_count:
                    self._logger.info(
                        "DeepSWE verifier will preserve %d filtered Go build event "
                        "stream(s) in the raw run log",
                        preserved_count,
                    )
            try:
                result = await original_verify(self)
                if is_deepswe:
                    infra_reason = _deepswe_verifier_suite_infra_reason(
                        self._trial_paths.verifier_dir,
                        self._trial_paths.artifacts_dir / "model.patch",
                    )
                    if infra_reason is not None:
                        raise DeepSweVerifierInfraError(infra_reason)
            finally:
                self._task.paths.task_dir = original_task_dir
                if prepared_task_dir is not None:
                    shutil.rmtree(prepared_task_dir, ignore_errors=True)
            return result

        verify_with_local_dirs._skills_evo_verifier_dir_patch = True  # type: ignore[attr-defined]
        Verifier.verify = verify_with_local_dirs

    from harbor.models.trial.paths import EnvironmentPaths
    from harbor.trial.trial import Trial

    original_should_verify_after_timeout = getattr(
        Trial,
        "_should_verify_after_agent_timeout",
        None,
    )
    if original_should_verify_after_timeout is not None and not getattr(
        original_should_verify_after_timeout,
        "_skills_evo_deepswe_timeout_verifier_patch",
        False,
    ):
        Trial._should_verify_after_agent_timeout = (  # type: ignore[method-assign]
            _deepswe_verify_after_agent_timeout(original_should_verify_after_timeout)
        )

    original_verify_with_retry = Trial._verify_with_retry
    if not getattr(
        original_verify_with_retry,
        "_skills_evo_deepswe_fresh_verifier_retry_patch",
        False,
    ):
        Trial._verify_with_retry = _deepswe_fresh_environment_verifier_retry(
            original_verify_with_retry
        )

    original_cleanup = Trial._cleanup_and_finalize
    if not getattr(
        original_cleanup,
        "_skills_evo_deepswe_auth_classification_patch",
        False,
    ):

        async def cleanup_with_deepswe_auth_classification(self: Any) -> None:
            if _deepswe_artifact_hook_info(self) is not None:
                _classify_deepswe_provider_auth_failure(self.result)
            return await original_cleanup(self)

        cleanup_with_deepswe_auth_classification._skills_evo_deepswe_auth_classification_patch = True  # type: ignore[attr-defined]
        Trial._cleanup_and_finalize = cleanup_with_deepswe_auth_classification

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
        if not getattr(
            original_populate_context, "_skills_evo_result_only_patch", False
        ):

            def skip_agent_context_population(self: Any) -> None:
                return None

            skip_agent_context_population._skills_evo_result_only_patch = True  # type: ignore[attr-defined]
            Trial._maybe_populate_agent_context = skip_agent_context_population

    if not deepswe_pre_artifacts:
        return

    from harbor.metrics.mean import Mean

    original_mean_compute = Mean.compute
    if not getattr(
        original_mean_compute, "_skills_evo_deepswe_multi_reward_patch", False
    ):

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
                if convention_source.rstrip("/") == "/logs/artifacts" and any(
                    is_main_deepswe_model_patch_artifact(artifact)
                    for artifact in normalized
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
                            "set -euo pipefail\n"
                            'if [ -n "${DEEPSWE_BASE_COMMIT:-}" ] && '
                            "git -C /app rev-parse --is-inside-work-tree >/dev/null 2>&1; then\n"
                            "  mkdir -p /logs/agent /logs/artifacts\n"
                            "  cd /app || exit 0\n"
                            "  git config --global --add safe.directory /app 2>/dev/null || true\n"
                            "  python3 - <<'PY'\n"
                            "import json, subprocess\n"
                            "from pathlib import Path\n"
                            "root = Path('/app')\n"
                            "logs = Path('/logs/agent')\n"
                            "raw = subprocess.run(['git', 'ls-files', '--others', '--ignored', '--exclude-standard', '-z'], cwd=root, stdout=subprocess.PIPE, check=True).stdout\n"
                            "paths = [value.decode('utf-8', 'surrogateescape') for value in raw.split(b'\\0') if value]\n"
                            "(logs / 'deepswe_baseline_ignored.json').write_text(json.dumps(paths))\n"
                            "PY\n"
                            "  git add -A . 2>/dev/null || true\n"
                            "  tree=$(git write-tree)\n"
                            '  if git rev-parse --verify "${DEEPSWE_BASE_COMMIT}^{commit}" '
                            ">/dev/null 2>&1; then\n"
                            "    commit=$(printf 'DeepSWE initial worktree snapshot\\n' | "
                            'git commit-tree "$tree" -p "${DEEPSWE_BASE_COMMIT}")\n'
                            "  else\n"
                            "    commit=$(printf 'DeepSWE initial worktree snapshot\\n' | "
                            'git commit-tree "$tree")\n'
                            "  fi\n"
                            "  printf '%s\\n' \"$commit\" > /logs/agent/deepswe_baseline_commit\n"
                            "  git reset -q 2>/dev/null || true\n"
                            '  echo "[deepswe_pre_artifacts] baseline_commit=$commit"\n'
                            "fi\n"
                            "test -s /logs/agent/deepswe_baseline_commit\n"
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
                        raise DeepSweAgentSetupInfraError(
                            "DeepSWE baseline snapshot command failed with "
                            f"return_code={result.return_code}"
                        )
                except Exception as exc:
                    if isinstance(exc, DeepSweAgentSetupInfraError):
                        raise
                    raise DeepSweAgentSetupInfraError(
                        f"DeepSWE baseline snapshot failed: {exc}"
                    ) from exc

            try:
                return await original_setup_agent(self)
            except Exception as exc:
                if hook_info is None:
                    raise
                if exc.__class__.__name__ == "AgentSetupTimeoutError":
                    raise
                raise DeepSweAgentSetupInfraError(
                    f"DeepSWE agent setup failed: {type(exc).__name__}: {exc}"
                ) from exc

        setup_agent_with_deepswe_baseline._skills_evo_deepswe_baseline_patch = True  # type: ignore[attr-defined]
        Trial._setup_agent = setup_agent_with_deepswe_baseline

    original_download_artifacts = Trial._download_artifacts
    if not getattr(
        original_download_artifacts,
        "_skills_evo_deepswe_required_artifacts_patch",
        False,
    ):

        async def download_required_deepswe_artifacts(self: Any) -> None:
            if (
                _deepswe_artifact_hook_info(self) is None
                or self.result.verifier_result is None
            ):
                await original_download_artifacts(self)
                return
            missing_paths = await _download_deepswe_artifacts_with_retry(
                self,
                original_download_artifacts,
                result_only=result_only,
            )
            missing = [str(path) for path in missing_paths]
            if not missing:
                return
            from harbor.models.trial.result import ExceptionInfo

            error = DeepSweArtifactDownloadError(
                "Required DeepSWE artifacts/logs were not downloaded: "
                + ", ".join(missing)
            )
            self.result.exception_info = ExceptionInfo.from_exception(error)

        download_required_deepswe_artifacts._skills_evo_deepswe_required_artifacts_patch = True  # type: ignore[attr-defined]
        Trial._download_artifacts = download_required_deepswe_artifacts

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
        try:
            official_test_paths = _deepswe_official_test_patch_paths(
                pre_artifacts.parent
            )
        except Exception as exc:
            raise DeepSweVerifierInfraError(
                f"Failed to parse DeepSWE official test.patch paths: {exc}"
            ) from exc
        environment = trial_environment(self)
        logger = trial_logger(self)

        remote_script = (
            f"{EnvironmentPaths.agent_dir.as_posix()}/deepswe_pre_artifacts.sh"
        )
        try:
            await environment.upload_file(
                source_path=pre_artifacts,
                target_path=remote_script,
            )
            result = await environment.exec(
                command=(
                    "set -euo pipefail\n"
                    "mkdir -p /logs/artifacts\n"
                    f"chmod +x {remote_script}\n"
                    f"/bin/bash {remote_script}\n"
                    'if [ -n "${DEEPSWE_BASE_COMMIT:-}" ] && '
                    'git -C /app rev-parse --verify "${DEEPSWE_BASE_COMMIT}^{commit}" '
                    ">/dev/null 2>&1; then\n"
                    "  cd /app || exit 0\n"
                    "  git config --global --add safe.directory /app 2>/dev/null || true\n"
                    "  python3 - <<'PY'\n"
                    + _DEEPSWE_MODEL_PATCH_POSTPROCESS_SCRIPT
                    + "PY\n"
                    "fi\n"
                    "test -f /logs/artifacts/model.patch\n"
                    "chmod -R a+rX /logs/artifacts 2>/dev/null || true\n"
                    "printf '[deepswe_pre_artifacts] model.patch bytes='\n"
                    "wc -c < /logs/artifacts/model.patch\n"
                ),
                cwd="/app",
                env={
                    "DEEPSWE_BASE_COMMIT": base_commit or "",
                    "DEEPSWE_OFFICIAL_TEST_PATHS_JSON": json.dumps(official_test_paths),
                },
                timeout_sec=120,
                user="root",
            )
            if result.stdout:
                logger.info(result.stdout.rstrip())
            if result.stderr:
                logger.warning(result.stderr.rstrip())
            if result.return_code != 0:
                raise DeepSweVerifierInfraError(
                    "DeepSWE pre_artifacts/postprocess failed with "
                    f"return_code={result.return_code}"
                )
        except Exception as exc:
            if isinstance(exc, DeepSweVerifierInfraError):
                raise
            raise DeepSweVerifierInfraError(
                f"DeepSWE pre_artifacts hook failed: {exc}"
            ) from exc

    if hasattr(Trial, "_run_verification"):
        original_run_verification = Trial._run_verification
        if not getattr(
            original_run_verification,
            "_skills_evo_deepswe_pre_artifacts_patch",
            False,
        ):

            async def run_deepswe_pre_artifacts_then_verify(self: Any) -> None:
                await run_deepswe_pre_artifacts(self)
                if _deepswe_artifact_hook_info(self) is not None:
                    await _move_deepswe_verification_to_fresh_environment(
                        self,
                        result_only=result_only,
                    )
                try:
                    return await original_run_verification(self)
                except BaseException:
                    if _deepswe_artifact_hook_info(self) is not None:
                        await _download_deepswe_verifier_logs_best_effort(
                            self,
                            label="verification_error",
                        )
                    raise

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
                return await original_collect_artifacts_phased(self, *args, **kwargs)

            collect_artifacts_after_deepswe_pre_artifacts._skills_evo_deepswe_pre_artifacts_patch = True  # type: ignore[attr-defined]
            Trial._collect_artifacts_phased = (
                collect_artifacts_after_deepswe_pre_artifacts
            )


def _existing_job_progress(job_dir: Path) -> tuple[int, int | None, str | None]:
    trial_count = sum(
        1 for path in job_dir.glob("*/result.json") if path.parent != job_dir
    )
    root_path = job_dir / "result.json"
    if not root_path.exists():
        return trial_count, None, None
    try:
        root = json.loads(root_path.read_text(errors="replace"))
    except (OSError, json.JSONDecodeError):
        return trial_count, None, None
    total = root.get("n_total_trials")
    try:
        total_int = int(total) if total is not None else None
    except (TypeError, ValueError):
        total_int = None
    return trial_count, total_int, root.get("finished_at")


def _deepswe_result_infra_reason(result: dict[str, Any]) -> str | None:
    verifier_result = result.get("verifier_result")
    if not isinstance(verifier_result, dict):
        return "invalid-verifier-result:missing"
    rewards = verifier_result.get("rewards")
    if not isinstance(rewards, dict):
        return "invalid-verifier-result:missing-rewards"
    reward = rewards.get("reward") if isinstance(rewards, dict) else None
    if isinstance(reward, bool) or not isinstance(reward, (int, float)):
        return "invalid-verifier-result:non-numeric-reward"
    if isinstance(reward, (int, float)) and reward < 0:
        return f"negative-reward:{reward}"
    if float(reward) not in {0.0, 1.0}:
        return f"invalid-verifier-result:out-of-domain-reward:{reward}"

    exception_info = result.get("exception_info")
    if not isinstance(exception_info, dict):
        return None
    exception_type = str(exception_info.get("exception_type") or "")
    if exception_type and exception_type not in DEEPSWE_VALID_AGENT_OUTCOME_EXCEPTIONS:
        return f"exception:{exception_type}"
    return None


def _normalize_legacy_deepswe_timeout_outcome(
    trial_dir: Path,
    result_text: str,
    result: dict[str, Any],
) -> str | None:
    """Convert auditable legacy timeout states into terminal model reward zero."""

    if isinstance(result.get("verifier_result"), dict):
        return None
    exception_info = result.get("exception_info")
    exception_type = (
        str(exception_info.get("exception_type") or "")
        if isinstance(exception_info, dict)
        else ""
    )
    evidence: dict[str, Any]
    if exception_type == "AgentTimeoutError":
        try:
            trial_log = (trial_dir / "trial.log").read_text(
                encoding="utf-8",
                errors="replace",
            )
        except OSError:
            return None
        marker = "Skipping verifier after agent timeout because"
        if marker not in trial_log:
            return None
        evidence = {
            "classification": "agent-time-limit-with-verifier-skipped",
            "log_marker": marker,
        }
    elif exception_type == "VerifierTimeoutError":
        node = _deepswe_repeated_pytest_timeout_node(trial_dir)
        if node is None:
            return None
        evidence = {
            "classification": "deterministic-model-test-timeout",
            "fresh_verifier_attempts": 2,
            "repeated_pytest_node": node,
        }
    else:
        return None

    backup_path = trial_dir / "result.pre-terminal-normalization.json"
    if not backup_path.exists():
        _atomic_write_text(backup_path, result_text)
    result["verifier_result"] = {"rewards": {"reward": 0}}
    if exception_type == "VerifierTimeoutError":
        # The immutable backup and sidecar retain the timeout evidence. The
        # normalized result is a semantic model failure, not an infra error.
        result["exception_info"] = None
    _atomic_write_text(
        trial_dir / "result.json",
        json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
    )
    _atomic_write_text(
        trial_dir / "terminal_outcome.json",
        json.dumps(
            {
                "schema_version": 1,
                "assigned_reward": 0,
                "exception_type": exception_type,
                "normalized_at": _utc_now(),
                **evidence,
            },
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        + "\n",
    )
    return str(evidence["classification"])


def _prepare_deepswe_root_result(
    job_dir: Path,
    *,
    run_id: str,
    mark_unfinished: bool,
) -> None:
    result_path = job_dir / "result.json"
    if not result_path.is_file():
        return
    try:
        result_text = result_path.read_text(errors="replace")
        result = json.loads(result_text)
        from harbor.models.job.result import JobResult

        JobResult.model_validate(result)
    except Exception:
        archive_dir = _resume_archive_dir(job_dir, run_id)
        archive_dir.mkdir(parents=True, exist_ok=True)
        os.replace(result_path, archive_dir / "result.invalid.json")
        return
    if not mark_unfinished or result.get("finished_at") is None:
        return
    result["finished_at"] = None
    _atomic_write_text(result_path, json.dumps(result, indent=4) + "\n")


def _archive_deepswe_infra_trials(
    job_dir: Path,
    *,
    run_id: str,
) -> list[dict[str, str]]:
    """Quarantine retryable and interrupted trials without destroying evidence."""
    candidates: list[tuple[Path, str]] = []
    expected_contract: dict[str, Any] | None = None
    root_config_path = job_dir / "config.json"
    if root_config_path.is_file():
        try:
            from harbor.models.job.config import JobConfig

            root_config = JobConfig.model_validate_json(
                root_config_path.read_text(errors="replace")
            )
            expected_contract = _saved_deepswe_resume_contract(root_config)
        except Exception:
            expected_contract = None
    for trial_dir in sorted(path for path in job_dir.iterdir() if path.is_dir()):
        config_path = trial_dir / "config.json"
        result_path = trial_dir / "result.json"
        if not config_path.is_file():
            candidates.append((trial_dir, "interrupted:no-config"))
            continue
        try:
            from harbor.models.trial.config import TrialConfig

            trial_config = TrialConfig.model_validate_json(
                config_path.read_text(errors="replace")
            )
        except Exception:
            candidates.append((trial_dir, "interrupted:invalid-config"))
            continue
        if expected_contract is not None:
            trial_kwargs = trial_config.agent.kwargs or {}
            trial_contract = trial_kwargs.get("resume_contract")
            resources = trial_config.environment
            environment_kwargs = resources.kwargs or {}
            incompatible_resources = (
                (resources.override_cpus or 0) < DEEPSWE_MIN_CPUS
                or (resources.override_memory_mb or 0) < DEEPSWE_MIN_MEMORY_MB
                or (resources.override_storage_mb or 0) < DEEPSWE_MIN_STORAGE_MB
                or int(environment_kwargs.get("sandbox_timeout_sec") or 0)
                < DEEPSWE_MIN_SANDBOX_TIMEOUT_SEC
                or environment_kwargs.get("force_allow_internet") is not True
            )
            if incompatible_resources or trial_contract != expected_contract:
                candidates.append(
                    (
                        trial_dir,
                        "incompatible-trial-contract-or-resources",
                    )
                )
                continue
        if not result_path.is_file():
            candidates.append((trial_dir, "interrupted:no-result"))
            continue
        try:
            from harbor.models.trial.result import TrialResult

            result_text = result_path.read_text(errors="replace")
            result = json.loads(result_text)
            TrialResult.model_validate(result)
        except Exception:
            candidates.append((trial_dir, "interrupted:invalid-result"))
            continue
        exception_info = result.get("exception_info")
        exception_type = (
            str(exception_info.get("exception_type") or "")
            if isinstance(exception_info, dict)
            else ""
        )
        normalization = _normalize_legacy_deepswe_timeout_outcome(
            trial_dir,
            result_text,
            result,
        )
        if normalization is not None:
            result = json.loads(result_path.read_text(errors="replace"))
        reason = _deepswe_result_infra_reason(result)
        if reason is None or reason.startswith("negative-reward:"):
            continue
        if exception_type in DEEPSWE_TRANSIENT_RETRY_EXCEPTIONS:
            candidates.append((trial_dir, f"exception:{exception_type}"))
        elif not exception_type:
            candidates.append((trial_dir, reason))

    _prepare_deepswe_root_result(
        job_dir,
        run_id=run_id,
        mark_unfinished=bool(candidates),
    )
    if not candidates:
        return []
    archive_dir = _resume_archive_dir(job_dir, run_id) / "trials"
    archive_dir.mkdir(parents=True, exist_ok=True)
    archived: list[dict[str, str]] = []
    for trial_dir, reason in candidates:
        if trial_dir.parent.resolve() != job_dir.resolve():
            raise RuntimeError(
                f"Refusing to archive trial outside job directory: {trial_dir}"
            )
        destination = archive_dir / trial_dir.name
        if destination.exists():
            raise RuntimeError(f"DeepSWE trial archive already exists: {destination}")
        os.replace(trial_dir, destination)
        archived.append(
            {
                "trial_name": trial_dir.name,
                "reason": reason,
                "archive_path": str(destination.resolve()),
            }
        )

    (_resume_archive_dir(job_dir, run_id) / "trials.manifest.json").write_text(
        json.dumps(
            {
                "archived_at": _utc_now(),
                "job_dir": str(job_dir.resolve()),
                "trials": archived,
            },
            indent=2,
            sort_keys=True,
        )
        + "\n"
    )
    return archived


async def _run_configured_job(
    args: argparse.Namespace,
    config: Any,
    *,
    deepswe_pre_artifacts: bool,
) -> Path:
    from harbor.job import Job

    config_path = ROOT / "configs" / f"{args.job_name}.json"
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(config.model_dump_json(indent=2))

    log_file = ROOT / "logs" / f"{args.job_name}.log"
    log_file.parent.mkdir(parents=True, exist_ok=True)
    job_dir = config.jobs_dir / config.job_name
    existing_trials, existing_total, existing_finished_at = _existing_job_progress(
        job_dir
    )
    has_existing_job = (job_dir / "config.json").exists() or (
        job_dir / "result.json"
    ).exists()
    if has_existing_job:
        _log(
            "RESUME existing job "
            f"trials={existing_trials}/{existing_total or '?'} "
            f"finished_at={existing_finished_at or 'none'}",
            log_file=log_file,
        )
    else:
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
        f"sandbox_timeout_sec={args.e2b_sandbox_timeout_sec} "
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


async def run_job(args: argparse.Namespace) -> Path:
    saved_config = getattr(args, "_saved_job_config", None)
    if saved_config is None:
        saved_config = _load_saved_job_config(args)
    previous_config = None
    if saved_config is not None:
        previous_config = saved_config.model_copy(deep=True)
        _apply_saved_config_to_args(args, saved_config)
        if _is_local_deepswe_job_config(saved_config):
            saved_job_dir = saved_config.jobs_dir / saved_config.job_name
            resume_contract = _deepswe_resume_contract(args, saved_config)
            _validate_historical_deepswe_identity(
                saved_job_dir,
                saved_config,
                resume_contract,
            )
            config = _upgrade_deepswe_resume_config(
                saved_config,
                resume_contract=resume_contract,
            )
            _sync_deepswe_infra_args(args, config)
        else:
            config = saved_config
    else:
        if _is_local_deepswe_dataset(args.dataset):
            _enforce_deepswe_infra_args(args)
        config = build_config(args)
        if _is_local_deepswe_job_config(config):
            config = _attach_deepswe_resume_contract(
                config,
                _deepswe_resume_contract(args, config),
            )
    is_deepswe_job = _is_local_deepswe_job_config(config)
    deepswe_pre_artifacts = (
        args.enable_deepswe_pre_artifacts
        and not args.disable_deepswe_pre_artifacts
        and _is_local_deepswe_dataset(args.dataset)
    )
    if args.resume_existing and is_deepswe_job:
        _patch_harbor_deepswe_resume_equality()
    _patch_harbor_runtime(
        result_only=args.result_only,
        deepswe_pre_artifacts=deepswe_pre_artifacts,
    )
    job_dir = config.jobs_dir / config.job_name
    resume_run_id = (
        datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ") + f"-pid{os.getpid()}"
    )
    try:
        with exclusive_job_run(job_dir):
            if args.resume_existing and is_deepswe_job and previous_config is not None:
                backup = _migrate_deepswe_root_config(
                    job_dir,
                    previous_config,
                    config,
                    run_id=resume_run_id,
                )
                if backup is not None:
                    _log(
                        f"RESUME migrated DeepSWE infrastructure config backup={backup}"
                    )
                archived = _archive_deepswe_infra_trials(
                    job_dir,
                    run_id=resume_run_id,
                )
                if archived:
                    _log(
                        "RESUME archived DeepSWE infrastructure/interrupted trials "
                        f"count={len(archived)} archive="
                        f"{_resume_archive_dir(job_dir, resume_run_id)}"
                    )
            return await _run_configured_job(
                args,
                config,
                deepswe_pre_artifacts=deepswe_pre_artifacts,
            )
    except RuntimeError as exc:
        raise SystemExit(str(exc)) from exc


def _validate_timeout_budget(args: argparse.Namespace) -> None:
    is_deepswe = _is_local_deepswe_dataset(args.dataset)
    setup_multiplier = (
        args.agent_setup_timeout_multiplier
        if args.agent_setup_timeout_multiplier is not None
        else args.timeout_multiplier
    )
    agent_multiplier = (
        args.agent_timeout_multiplier
        if args.agent_timeout_multiplier is not None
        else args.timeout_multiplier
    )
    setup_reserve = (
        (args.agent_setup_timeout_sec or 0) * setup_multiplier if is_deepswe else 0
    )
    max_effective_agent_timeout = (
        args.e2b_sandbox_timeout_sec - args.verifier_buffer_sec - setup_reserve
    )
    if max_effective_agent_timeout < 60:
        raise SystemExit(
            "Invalid timeout configuration: sandbox timeout must exceed the "
            "verifier and agent-setup reserves by at least 60 seconds."
        )
    effective_agent_timeout = (args.agent_timeout_sec or 0) * agent_multiplier
    if (
        args.agent_timeout_sec is not None
        and effective_agent_timeout > max_effective_agent_timeout
    ):
        reduced_agent_timeout = max_effective_agent_timeout / agent_multiplier
        _log(
            "Reducing agent_timeout_sec from "
            f"{args.agent_timeout_sec:g} to {reduced_agent_timeout:g} so setup and "
            "verification remain inside the sandbox lifetime."
        )
        args.agent_timeout_sec = reduced_agent_timeout


def parse_args() -> argparse.Namespace:
    raw_argv = sys.argv[1:]

    def supplied(*options: str) -> bool:
        return any(
            item == option or item.startswith(f"{option}=")
            for item in raw_argv
            for option in options
        )

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
    parser.add_argument(
        "--dataset", default=os.getenv("HARBOR_DATASET", DEFAULT_DATASET)
    )
    parser.add_argument(
        "--benchmark-name",
        default=os.getenv("BENCHMARK_NAME", "swe-bench"),
        help="Prompt/metadata mode passed to the agent, e.g. swe-bench, swe-gym, or deepswe.",
    )
    parser.add_argument(
        "--agent",
        "--harness",
        choices=AGENT_CHOICES,
        default=_agent_name(
            os.getenv("BENCHMARK_AGENT", os.getenv("HARBOR_AGENT", "pi"))
        ),
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
        "--jobs-dir",
        type=Path,
        default=Path(os.getenv("HARBOR_JOBS_DIR", str(ROOT / "jobs"))),
        help="Directory where Harbor job outputs are written.",
    )
    parser.add_argument(
        "--resume-existing",
        action="store_true",
        help=(
            "Resume an existing job from its saved Harbor config. Completed trial "
            "results are preserved and only missing or interrupted trials rerun."
        ),
    )
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
    parser.add_argument(
        "--concurrency", type=int, default=int(os.getenv("E2B_CONCURRENCY", "10"))
    )
    parser.add_argument(
        "--max-retries", type=int, default=int(os.getenv("HARBOR_MAX_RETRIES", "0"))
    )
    parser.add_argument(
        "--retry-min-wait-sec",
        type=float,
        default=_float_env("HARBOR_RETRY_MIN_WAIT_SEC", "5"),
    )
    parser.add_argument(
        "--retry-max-wait-sec",
        type=float,
        default=_float_env("HARBOR_RETRY_MAX_WAIT_SEC", "60"),
    )
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
    parser.add_argument(
        "--n-tasks", type=int, default=int(n_tasks_env) if n_tasks_env else None
    )
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
    parser.add_argument(
        "--timeout-multiplier",
        type=float,
        default=_float_env("TIMEOUT_MULTIPLIER", "1.0"),
    )
    parser.add_argument(
        "--agent-timeout-multiplier",
        type=float,
        default=_optional_float_env("AGENT_TIMEOUT_MULTIPLIER"),
    )
    parser.add_argument(
        "--verifier-timeout-multiplier",
        type=float,
        default=_optional_float_env("VERIFIER_TIMEOUT_MULTIPLIER"),
    )
    parser.add_argument(
        "--agent-setup-timeout-multiplier",
        type=float,
        default=_float_env("AGENT_SETUP_TIMEOUT_MULTIPLIER", "2.0"),
    )
    parser.add_argument(
        "--environment-build-timeout-multiplier",
        type=float,
        default=_float_env("ENVIRONMENT_BUILD_TIMEOUT_MULTIPLIER", "2.0"),
    )
    parser.add_argument(
        "--agent-setup-timeout-sec",
        type=float,
        default=_float_env("AGENT_SETUP_TIMEOUT_SEC", "1200"),
    )
    parser.add_argument(
        "--agent-timeout-sec",
        type=float,
        default=_optional_float_env("AGENT_TIMEOUT_SEC"),
    )
    parser.add_argument(
        "--verifier-buffer-sec",
        type=float,
        default=_float_env("VERIFIER_BUFFER_SEC", str(DEFAULT_VERIFIER_BUFFER_SEC)),
        help="Seconds reserved at the end of each sandbox lifetime for uploading tests and running the verifier.",
    )
    parser.add_argument(
        "--override-cpus",
        type=int,
        default=int(os.getenv("E2B_OVERRIDE_CPUS", str(DEFAULT_E2B_CPUS))),
    )
    parser.add_argument(
        "--override-memory-mb",
        type=int,
        default=int(os.getenv("E2B_OVERRIDE_MEMORY_MB", str(DEFAULT_E2B_MEMORY_MB))),
    )
    parser.add_argument(
        "--override-storage-mb",
        type=int,
        default=int(os.getenv("E2B_OVERRIDE_STORAGE_MB", str(DEFAULT_E2B_STORAGE_MB))),
    )
    parser.add_argument("--model-context-window", type=int, default=None)
    parser.add_argument("--model-max-tokens", type=int, default=None)
    parser.add_argument("--thinking", default=os.getenv("PI_THINKING", "off"))
    parser.add_argument(
        "--tools", default=os.getenv("PI_TOOLS", "read,write,edit,bash,grep,find,ls")
    )
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
    parser.add_argument(
        "--claude-sdk-version",
        default=os.getenv("CLAUDE_AGENT_SDK_VERSION", "0.2.116"),
        help="Pinned claude-agent-sdk version installed inside benchmark sandboxes.",
    )
    parser.add_argument(
        "--pi-version",
        default=os.getenv("PI_CODING_AGENT_VERSION", "0.80.6"),
        help="Pinned @earendil-works/pi-coding-agent version installed in sandboxes.",
    )
    args = parser.parse_args()

    args.agent = _agent_name(args.agent)

    task_names = list(args.include_task_name or [])
    for task_names_file in args.task_names_file or []:
        task_names.extend(_read_task_names_file(task_names_file))
    args.include_task_name = list(dict.fromkeys(task_names)) or None
    args._requested_provider = args.provider if supplied("--provider") else None
    args._requested_dataset = args.dataset if supplied("--dataset") else None
    args._requested_agent = args.agent if supplied("--agent", "--harness") else None
    args._requested_use_skills = (
        args.use_skills if supplied("--use-skills", "--no-skills") else None
    )
    args._requested_task_names = (
        set(args.include_task_name or [])
        if supplied("--include-task-name", "--task-names-file")
        else None
    )

    args.provider = normalize_provider_name(args.provider)
    selected_provider = resolve_provider(args.provider)
    if args.job_name is None:
        skill_suffix = "skills" if args.use_skills else "noskills"
        agent_slug = args.agent.replace("-", "_")
        args.job_name = f"{agent_slug}_{selected_provider.name}_{skill_suffix}"
    if args.model_context_window is None:
        args.model_context_window = _provider_int_env(
            selected_provider, "CONTEXT_WINDOW", "128000"
        )
    if args.model_max_tokens is None:
        args.model_max_tokens = _provider_int_env(
            selected_provider, "MAX_TOKENS", "32000"
        )
    if _is_local_deepswe_dataset(args.dataset):
        _enforce_deepswe_infra_args(args)
    _validate_timeout_budget(args)
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
        os.environ[provider.anthropic_base_url_env] = (
            provider.default_anthropic_base_url
        )
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
        os.environ.setdefault("FORCE_DISABLE_THINKING", "1")
    ensure_reasoning_effort_none(
        os.getenv(provider.base_url_env),
        env_prefix=provider.env_prefix,
    )


def main() -> None:
    load_dotenv(ROOT / ".env", override=False)
    args = parse_args()
    saved_config = _load_saved_job_config(args)
    if saved_config is not None:
        _apply_saved_config_to_args(args, saved_config)
        args._saved_job_config = saved_config
        if _is_local_deepswe_dataset(args.dataset):
            _enforce_deepswe_infra_args(args)
            _validate_timeout_budget(args)
    _apply_provider_overrides(args)
    provider = _provider_spec()
    try:
        required_provider_env = provider.required_env(agent=args.agent)
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc
    _require_env(["LLM_PROVIDER", *required_provider_env, "E2B_API_KEY"])
    _patch_e2b_disable_http2()
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    asyncio.run(run_job(args))


if __name__ == "__main__":
    main()
