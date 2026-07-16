#!/usr/bin/env python
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import shutil
import subprocess
import sys
import tempfile
import traceback
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.run_benchmark import (  # noqa: E402
    DeepSweVerifierInfraError,
    _deepswe_fresh_environment_verifier_retry,
    _deepswe_result_infra_reason,
    _patch_e2b_disable_http2,
    _patch_harbor_runtime,
    _replace_deepswe_verifier_environment,
    _sha256_file,
    _sha256_tree,
)


SCHEMA_VERSION = 1


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def timestamp_id() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")


def read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(errors="replace"))
    if not isinstance(value, dict):
        raise ValueError(f"Expected a JSON object: {path}")
    return value


def write_json_atomic(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    with tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        dir=path.parent,
        prefix=f".{path.name}.",
        delete=False,
    ) as handle:
        handle.write(payload)
        temporary = Path(handle.name)
    os.replace(temporary, path)


def git_revision(root: Path) -> dict[str, Any]:
    def git(*args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["git", *args],
            cwd=root,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            check=False,
        )

    revision = git("rev-parse", "HEAD")
    dirty = git("status", "--porcelain")
    return {
        "commit": revision.stdout.strip() if revision.returncode == 0 else None,
        "worktree_dirty": bool(dirty.stdout.strip()) if dirty.returncode == 0 else None,
    }


def default_output_root(source_trial_dir: Path) -> Path:
    job_dir = source_trial_dir.parent
    return job_dir.parent / ".skills-evo-verifier-replays" / job_dir.name


def _assert_sidecar_path(source_trial_dir: Path, replay_dir: Path) -> None:
    source = source_trial_dir.resolve()
    replay = replay_dir.resolve()
    if replay == source or replay.is_relative_to(source):
        raise ValueError(
            "Replay output must be a sidecar outside the source trial directory"
        )


def _task_path_from_config(config: Any) -> Path:
    task_path = getattr(config.task, "path", None)
    if task_path is None:
        raise ValueError("Verifier replay requires a local DeepSWE task path")
    task_dir = Path(task_path).expanduser().resolve()
    required = (
        task_dir / "task.toml",
        task_dir / "pre_artifacts.sh",
        task_dir / "environment",
        task_dir / "tests" / "test.sh",
    )
    missing = [str(path) for path in required if not path.exists()]
    if missing:
        raise ValueError("Invalid local DeepSWE task; missing: " + ", ".join(missing))
    return task_dir


def prepare_replay(
    *,
    source_trial_dir: Path,
    output_root: Path | None = None,
    replay_id: str | None = None,
) -> tuple[Path, dict[str, Any]]:
    """Create an immutable-input replay sidecar and its provenance request."""

    from harbor.models.task.task import Task
    from harbor.models.trial.config import TrialConfig
    from harbor.models.trial.result import TrialResult

    source_trial_dir = source_trial_dir.expanduser().resolve()
    config_path = source_trial_dir / "config.json"
    result_path = source_trial_dir / "result.json"
    patch_path = source_trial_dir / "artifacts" / "model.patch"
    for path in (config_path, result_path, patch_path):
        if not path.is_file():
            raise ValueError(f"Missing source trial artifact: {path}")

    config_payload = read_json(config_path)
    result_payload = read_json(result_path)
    config = TrialConfig.model_validate(config_payload)
    result = TrialResult.model_validate(result_payload)
    task_dir = _task_path_from_config(config)
    task = Task(task_dir)

    if result.trial_name != config.trial_name:
        raise ValueError(
            "Source trial config/result identity mismatch: "
            f"config={config.trial_name} result={result.trial_name}"
        )
    if result.task_name != task.name:
        raise ValueError(
            "Source trial result/task identity mismatch: "
            f"result={result.task_name} task={task.name}"
        )
    if result.config != config:
        raise ValueError("Source trial config.json differs from result.config")
    if result.task_checksum != task.checksum:
        raise ValueError(
            "Source trial task checksum mismatch: "
            f"result={result.task_checksum} task={task.checksum}"
        )
    infra_reason = _deepswe_result_infra_reason(result_payload)
    if infra_reason is None:
        raise ValueError("Refusing verifier replay for an infra-valid source trial")
    source_exception = result_payload.get("exception_info")
    source_exception_type = (
        str(source_exception.get("exception_type") or "")
        if isinstance(source_exception, dict)
        else ""
    )
    if source_exception_type not in {"", "DeepSweVerifierInfraError"}:
        raise ValueError(
            "Refusing verifier replay for a non-verifier source exception: "
            f"{source_exception_type}"
        )
    if not (
        source_exception_type == "DeepSweVerifierInfraError"
        or infra_reason.startswith("negative-reward:")
        or infra_reason.startswith("invalid-verifier-result:")
        or infra_reason == "missing-verifier-result"
    ):
        raise ValueError(
            "Refusing verifier replay because the source is not verifier-invalid: "
            f"{infra_reason}"
        )

    agent_result = result_payload.get("agent_result")
    agent_metadata = (
        agent_result.get("metadata") if isinstance(agent_result, dict) else None
    )
    if (
        not isinstance(agent_metadata, dict)
        or agent_metadata.get("completed") is not True
    ):
        raise ValueError(
            "Verifier replay requires an explicitly completed agent result"
        )
    if patch_path.stat().st_size <= 0:
        raise ValueError("Verifier replay requires a non-empty model.patch")

    patch_sha256 = _sha256_file(patch_path)
    output_root = (
        output_root.expanduser().resolve()
        if output_root is not None
        else default_output_root(source_trial_dir).resolve()
    )
    replay_id = replay_id or (
        f"{config.trial_name}__verify_{timestamp_id()}_{patch_sha256[:8]}"
    )
    if not replay_id or replay_id in {".", ".."} or Path(replay_id).name != replay_id:
        raise ValueError(f"Invalid replay id: {replay_id!r}")
    replay_dir = output_root / replay_id
    _assert_sidecar_path(source_trial_dir, replay_dir)
    if replay_dir.exists():
        raise FileExistsError(f"Replay sidecar already exists: {replay_dir}")

    replay_patch = replay_dir / "artifacts" / "model.patch"
    replay_patch.parent.mkdir(parents=True)
    shutil.copyfile(patch_path, replay_patch)
    if _sha256_file(replay_patch) != patch_sha256:
        raise RuntimeError("Replay model.patch checksum changed while copying")

    runner = git_revision(ROOT)
    runner["code_sha256"] = {
        "environments/e2b_swebench.py": _sha256_file(
            ROOT / "environments" / "e2b_swebench.py"
        ),
        "scripts/run_benchmark.py": _sha256_file(ROOT / "scripts" / "run_benchmark.py"),
        "scripts/replay_deepswe_verifier.py": _sha256_file(Path(__file__).resolve()),
    }
    request = {
        "schema_version": SCHEMA_VERSION,
        "kind": "deepswe_verifier_same_patch_replay",
        "replay_id": replay_id,
        "created_at": utc_now(),
        "status": "prepared",
        "source": {
            "job_dir": str(source_trial_dir.parent),
            "trial_dir": str(source_trial_dir),
            "trial_name": result.trial_name,
            "task_name": result.task_name,
            "trial_result_id": str(result.id),
            "config_sha256": _sha256_file(config_path),
            "result_sha256": _sha256_file(result_path),
            "infra_reason": infra_reason,
            "exception_type": source_exception_type or None,
        },
        "model_patch": {
            "source_path": str(patch_path),
            "replay_path": str(replay_patch),
            "sha256": patch_sha256,
            "size_bytes": patch_path.stat().st_size,
        },
        "task": {
            "path": str(task_dir),
            "name": task.name,
            "tree_sha256": _sha256_tree(task_dir),
            "tests_tree_sha256": _sha256_tree(task.paths.tests_dir),
            "task_toml_sha256": _sha256_file(task.paths.config_path),
        },
        "runner": runner,
        "safety": {
            "agent_executed": False,
            "source_trial_mutated": False,
            "verifier_environment": "fresh-offline-separate",
        },
    }
    write_json_atomic(replay_dir / "request.json", request)
    return replay_dir, request


@dataclass
class ReplayContext:
    config: Any
    _task: Any
    _trial_paths: Any
    _logger: logging.Logger
    result: Any
    _environment: Any | None = None
    _skills_evo_deepswe_fresh_verifier_environment: bool = False


def _verifier_timeout_sec(config: Any, task: Any) -> float:
    base = config.verifier.override_timeout_sec or task.config.verifier.timeout_sec
    cap = config.verifier.max_timeout_sec or float("inf")
    multiplier = (
        config.verifier_timeout_multiplier
        if config.verifier_timeout_multiplier is not None
        else config.timeout_multiplier
    )
    return min(base, cap) * multiplier


async def run_isolated_verifier(
    *,
    replay_dir: Path,
    request: dict[str, Any],
) -> dict[str, Any]:
    """Verify the copied patch without constructing or executing an agent."""

    from harbor.models.task.task import Task
    from harbor.models.trial.config import TrialConfig
    from harbor.models.trial.paths import TrialPaths
    from harbor.trial.trial import VerifierTimeoutError
    from harbor.verifier.verifier import Verifier

    source_config_path = Path(request["source"]["trial_dir"]) / "config.json"
    if _sha256_file(source_config_path) != request["source"]["config_sha256"]:
        raise RuntimeError("Source config changed after replay preparation")
    source_result_path = Path(request["source"]["trial_dir"]) / "result.json"
    if _sha256_file(source_result_path) != request["source"]["result_sha256"]:
        raise RuntimeError("Source result changed after replay preparation")
    source_patch_path = Path(request["model_patch"]["source_path"])
    if _sha256_file(source_patch_path) != request["model_patch"]["sha256"]:
        raise RuntimeError("Source model.patch changed after replay preparation")
    replay_patch_path = Path(request["model_patch"]["replay_path"])
    if _sha256_file(replay_patch_path) != request["model_patch"]["sha256"]:
        raise RuntimeError("Replay model.patch does not match its provenance")

    source_config = TrialConfig.model_validate_json(
        source_config_path.read_text(errors="replace")
    )
    task = Task(request["task"]["path"])
    if _sha256_tree(task.task_dir) != request["task"]["tree_sha256"]:
        raise RuntimeError("DeepSWE task tree changed after replay preparation")
    if _sha256_tree(task.paths.tests_dir) != request["task"]["tests_tree_sha256"]:
        raise RuntimeError("DeepSWE verifier tests changed after replay preparation")

    replay_config = source_config.model_copy(deep=True)
    replay_config.trial_name = replay_dir.name
    replay_config.trials_dir = replay_dir.parent
    replay_config.environment.delete = True
    paths = TrialPaths(replay_dir)
    paths.mkdir()
    logger = logging.getLogger(f"deepswe-verifier-replay.{replay_dir.name}")
    logger.setLevel(logging.INFO)
    logger.propagate = False
    handler = logging.FileHandler(paths.log_path, encoding="utf-8")
    logger.handlers = [handler]
    context = ReplayContext(
        config=replay_config,
        _task=task,
        _trial_paths=paths,
        _logger=logger,
        result=SimpleNamespace(verifier_result=None),
    )

    _patch_e2b_disable_http2()
    _patch_harbor_runtime(result_only=True, deepswe_pre_artifacts=True)
    try:
        await _replace_deepswe_verifier_environment(
            context,
            retry_index=0,
            stop_current=False,
        )
        context._skills_evo_deepswe_fresh_verifier_environment = True
        timeout_sec = _verifier_timeout_sec(replay_config, task)

        async def verify_once(replay: ReplayContext) -> None:
            verifier = Verifier(
                task=replay._task,
                trial_paths=replay._trial_paths,
                environment=replay._environment,
                logger=replay._logger,
            )
            try:
                replay.result.verifier_result = await asyncio.wait_for(
                    verifier.verify(),
                    timeout=timeout_sec,
                )
            except asyncio.TimeoutError as exc:
                raise VerifierTimeoutError(
                    f"Verifier replay timed out after {timeout_sec} seconds"
                ) from exc

        verify_with_fresh_retry = _deepswe_fresh_environment_verifier_retry(verify_once)
        await verify_with_fresh_retry(context)
        verifier_result = context.result.verifier_result
        if verifier_result is None:
            raise DeepSweVerifierInfraError("Verifier replay returned no result")
        return verifier_result.model_dump(mode="json")
    finally:
        if context._environment is not None:
            await context._environment.stop(delete=True)
        handler.close()
        logger.handlers = []


def build_overlay(
    *,
    request: dict[str, Any],
    replay_result_path: Path,
    verifier_result: dict[str, Any] | None,
    exception: dict[str, Any] | None,
) -> dict[str, Any]:
    rewards = verifier_result.get("rewards") if verifier_result else None
    reward = rewards.get("reward") if isinstance(rewards, dict) else None
    eligible = (
        exception is None
        and isinstance(reward, (int, float))
        and not isinstance(reward, bool)
        and float(reward) in {0.0, 1.0}
    )
    return {
        "schema_version": SCHEMA_VERSION,
        "kind": "deepswe_trial_verifier_overlay",
        "eligible": eligible,
        "eligibility_reason": (
            "clean-binary-verifier-result"
            if eligible
            else "replay-did-not-produce-clean-binary-verifier-result"
        ),
        "source_guard": {
            "trial_dir": request["source"]["trial_dir"],
            "trial_name": request["source"]["trial_name"],
            "task_name": request["source"]["task_name"],
            "source_config_sha256": request["source"]["config_sha256"],
            "source_result_sha256": request["source"]["result_sha256"],
            "source_infra_reason": request["source"]["infra_reason"],
            "model_patch_sha256": request["model_patch"]["sha256"],
            "task_tree_sha256": request["task"]["tree_sha256"],
            "tests_tree_sha256": request["task"]["tests_tree_sha256"],
        },
        "replacement": {
            "verifier_result": verifier_result,
            "exception_info": None if eligible else exception,
        },
        "replay_result_path": str(replay_result_path.resolve()),
        "runner": request["runner"],
        "automatic_application": False,
    }


async def execute_replay(replay_dir: Path, request: dict[str, Any]) -> dict[str, Any]:
    started_at = utc_now()
    verifier_result: dict[str, Any] | None = None
    exception: dict[str, Any] | None = None
    try:
        verifier_result = await run_isolated_verifier(
            replay_dir=replay_dir,
            request=request,
        )
        status = "complete"
    except Exception as exc:
        status = "failed"
        exception = {
            "type": type(exc).__name__,
            "message": str(exc),
            "traceback": traceback.format_exc(),
        }

    result_path = replay_dir / "replay_result.json"
    result = {
        "schema_version": SCHEMA_VERSION,
        "kind": "deepswe_verifier_same_patch_replay_result",
        "replay_id": request["replay_id"],
        "status": status,
        "started_at": started_at,
        "finished_at": utc_now(),
        "source": request["source"],
        "model_patch": request["model_patch"],
        "task": request["task"],
        "runner": request["runner"],
        "safety": request["safety"],
        "verifier_result": verifier_result,
        "exception": exception,
    }
    write_json_atomic(result_path, result)
    overlay = build_overlay(
        request=request,
        replay_result_path=result_path,
        verifier_result=verifier_result,
        exception=exception,
    )
    write_json_atomic(replay_dir / "overlay.json", overlay)
    if status != "complete":
        raise RuntimeError(
            f"DeepSWE verifier replay failed; inspect {result_path}: "
            f"{exception['type']}: {exception['message']}"
        )
    return result


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Replay one infra-invalid DeepSWE verifier against the exact saved "
            "model.patch without invoking an agent or mutating the source trial."
        )
    )
    parser.add_argument("--source-trial-dir", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, default=None)
    parser.add_argument("--replay-id", default=None)
    parser.add_argument(
        "--prepare-only",
        action="store_true",
        help="Write and validate the sidecar request without starting E2B.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    replay_dir, request = prepare_replay(
        source_trial_dir=args.source_trial_dir,
        output_root=args.output_root,
        replay_id=args.replay_id,
    )
    if args.prepare_only:
        print(
            json.dumps(
                {
                    "status": "prepared",
                    "replay_dir": str(replay_dir),
                    "request": str(replay_dir / "request.json"),
                },
                indent=2,
                sort_keys=True,
            )
        )
        return
    if not os.getenv("E2B_API_KEY"):
        raise SystemExit("E2B_API_KEY is required to run the isolated verifier replay")
    result = asyncio.run(execute_replay(replay_dir, request))
    print(
        json.dumps(
            {
                "status": result["status"],
                "replay_dir": str(replay_dir),
                "result": str(replay_dir / "replay_result.json"),
                "overlay": str(replay_dir / "overlay.json"),
            },
            indent=2,
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
