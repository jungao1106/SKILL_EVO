#!/usr/bin/env python
from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import tempfile
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.job_run_lock import exclusive_job_run  # noqa: E402
from scripts.run_benchmark import (  # noqa: E402
    _deepswe_result_infra_reason,
    _sha256_file,
    _sha256_tree,
)


SCHEMA_VERSION = 1
OVERLAY_KIND = "deepswe_trial_verifier_overlay"
APPLICATION_KIND = "deepswe_trial_verifier_overlay_application"


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(errors="replace"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"Could not read JSON object {path}: {exc}") from exc
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
        handle.flush()
        os.fsync(handle.fileno())
        temporary = Path(handle.name)
    os.replace(temporary, path)


def copy_file_atomic(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    with (
        source.open("rb") as source_handle,
        tempfile.NamedTemporaryFile(
            mode="wb",
            dir=destination.parent,
            prefix=f".{destination.name}.",
            delete=False,
        ) as destination_handle,
    ):
        shutil.copyfileobj(source_handle, destination_handle)
        destination_handle.flush()
        os.fsync(destination_handle.fileno())
        temporary = Path(destination_handle.name)
    os.replace(temporary, destination)


def _required_string(mapping: dict[str, Any], key: str, label: str) -> str:
    value = mapping.get(key)
    if not isinstance(value, str) or not value:
        raise ValueError(f"Missing {label}.{key}")
    return value


def _validate_overlay_shape(
    overlay: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    if overlay.get("schema_version") != SCHEMA_VERSION:
        raise ValueError(
            f"Unsupported overlay schema_version: {overlay.get('schema_version')!r}"
        )
    if overlay.get("kind") != OVERLAY_KIND:
        raise ValueError(f"Unexpected overlay kind: {overlay.get('kind')!r}")
    if overlay.get("eligible") is not True:
        raise ValueError("Refusing to apply an overlay unless eligible=true")
    if overlay.get("automatic_application") is not False:
        raise ValueError("Overlay must explicitly set automatic_application=false")

    guard = overlay.get("source_guard")
    replacement = overlay.get("replacement")
    if not isinstance(guard, dict) or not isinstance(replacement, dict):
        raise ValueError("Overlay must contain source_guard and replacement objects")
    for key in (
        "trial_dir",
        "trial_name",
        "task_name",
        "source_config_sha256",
        "source_result_sha256",
        "source_infra_reason",
        "model_patch_sha256",
        "task_tree_sha256",
        "tests_tree_sha256",
    ):
        _required_string(guard, key, "source_guard")

    verifier_result = replacement.get("verifier_result")
    if not isinstance(verifier_result, dict):
        raise ValueError("Overlay replacement.verifier_result must be an object")
    if replacement.get("exception_info", object()) is not None:
        raise ValueError("Eligible overlay replacement.exception_info must be null")
    rewards = verifier_result.get("rewards")
    reward = rewards.get("reward") if isinstance(rewards, dict) else None
    if (
        isinstance(reward, bool)
        or not isinstance(reward, (int, float))
        or float(reward) not in {0.0, 1.0}
    ):
        raise ValueError("Eligible overlay must contain a binary verifier reward")

    from harbor.models.verifier.result import VerifierResult

    VerifierResult.model_validate(verifier_result)
    return guard, replacement


def _validate_replay_result(
    *,
    overlay: dict[str, Any],
    overlay_dir: Path,
    guard: dict[str, Any],
    replacement: dict[str, Any],
) -> Path:
    raw_path = _required_string(overlay, "replay_result_path", "overlay")
    replay_result_path = Path(raw_path).expanduser().resolve()
    if replay_result_path.parent != overlay_dir.resolve():
        raise ValueError("Replay result must be in the overlay sidecar directory")
    replay = read_json(replay_result_path)
    if replay.get("schema_version") != SCHEMA_VERSION:
        raise ValueError("Replay result has an unsupported schema_version")
    if replay.get("kind") != "deepswe_verifier_same_patch_replay_result":
        raise ValueError("Replay result has an unexpected kind")
    if replay.get("status") != "complete" or replay.get("exception") is not None:
        raise ValueError("Replay result is not a clean completed replay")
    if replay.get("verifier_result") != replacement["verifier_result"]:
        raise ValueError("Overlay replacement does not match replay_result.json")

    source = replay.get("source")
    model_patch = replay.get("model_patch")
    task = replay.get("task")
    if not all(isinstance(value, dict) for value in (source, model_patch, task)):
        raise ValueError("Replay result provenance is incomplete")
    replay_patch_path = (
        Path(_required_string(model_patch, "replay_path", "replay_result.model_patch"))
        .expanduser()
        .resolve()
    )
    if not replay_patch_path.is_relative_to(overlay_dir.resolve()):
        raise ValueError("Replay model.patch must be stored inside the sidecar")
    if not replay_patch_path.is_file():
        raise ValueError(f"Missing copied replay model.patch: {replay_patch_path}")
    if _sha256_file(replay_patch_path) != guard["model_patch_sha256"]:
        raise ValueError("Copied replay model.patch hash does not match overlay guard")
    expected_pairs = (
        (source.get("trial_dir"), guard["trial_dir"], "source trial_dir"),
        (source.get("trial_name"), guard["trial_name"], "source trial_name"),
        (source.get("task_name"), guard["task_name"], "source task_name"),
        (source.get("config_sha256"), guard["source_config_sha256"], "config hash"),
        (source.get("result_sha256"), guard["source_result_sha256"], "result hash"),
        (source.get("infra_reason"), guard["source_infra_reason"], "infra reason"),
        (model_patch.get("sha256"), guard["model_patch_sha256"], "patch hash"),
        (task.get("tree_sha256"), guard["task_tree_sha256"], "task hash"),
        (task.get("tests_tree_sha256"), guard["tests_tree_sha256"], "tests hash"),
    )
    for observed, expected, label in expected_pairs:
        if observed != expected:
            raise ValueError(f"Replay result {label} does not match overlay guard")
    return replay_result_path


def _validate_static_source(
    guard: dict[str, Any],
) -> tuple[Path, Path, Path, Any, Any]:
    from harbor.models.task.task import Task
    from harbor.models.trial.config import TrialConfig

    source_trial_dir = Path(guard["trial_dir"]).expanduser().resolve()
    config_path = source_trial_dir / "config.json"
    result_path = source_trial_dir / "result.json"
    patch_path = source_trial_dir / "artifacts" / "model.patch"
    for path in (config_path, result_path, patch_path):
        if not path.is_file():
            raise ValueError(f"Missing guarded source artifact: {path}")
    if _sha256_file(config_path) != guard["source_config_sha256"]:
        raise ValueError("Source config hash does not match overlay guard")
    if _sha256_file(patch_path) != guard["model_patch_sha256"]:
        raise ValueError("Source model.patch hash does not match overlay guard")

    config = TrialConfig.model_validate_json(config_path.read_text(errors="replace"))
    task_path = getattr(config.task, "path", None)
    if task_path is None:
        raise ValueError("Overlay source must use a local DeepSWE task")
    task = Task(Path(task_path).expanduser().resolve())
    if _sha256_tree(task.task_dir) != guard["task_tree_sha256"]:
        raise ValueError("Source task tree hash does not match overlay guard")
    if _sha256_tree(task.paths.tests_dir) != guard["tests_tree_sha256"]:
        raise ValueError("Source tests tree hash does not match overlay guard")
    if config.trial_name != guard["trial_name"]:
        raise ValueError("Source config trial identity does not match overlay guard")
    if task.name != guard["task_name"]:
        raise ValueError("Source task identity does not match overlay guard")
    return source_trial_dir, config_path, result_path, config, task


def _validate_source_result(
    *,
    result: dict[str, Any],
    guard: dict[str, Any],
    config: Any,
    task: Any,
    require_verifier_infra: bool,
) -> None:
    from harbor.models.trial.result import TrialResult

    validated = TrialResult.model_validate(result)
    if validated.trial_name != guard["trial_name"]:
        raise ValueError("Source result trial identity does not match overlay guard")
    if validated.task_name != guard["task_name"] or validated.task_name != task.name:
        raise ValueError("Source result task identity does not match overlay guard")
    if validated.config.trial_name != config.trial_name:
        raise ValueError("Embedded source result config identity is inconsistent")
    if validated.config != config:
        raise ValueError(
            "Embedded source result config differs from source config.json"
        )
    if validated.task_checksum != task.checksum:
        raise ValueError("Source result task checksum differs from guarded task tree")
    agent_result = result.get("agent_result")
    agent_metadata = (
        agent_result.get("metadata") if isinstance(agent_result, dict) else None
    )
    if (
        not isinstance(agent_metadata, dict)
        or agent_metadata.get("completed") is not True
    ):
        raise ValueError("Source agent result is not explicitly completed")
    if not require_verifier_infra:
        return

    observed_reason = _deepswe_result_infra_reason(result)
    if observed_reason != guard["source_infra_reason"]:
        raise ValueError(
            "Source verifier infra reason does not match overlay guard: "
            f"observed={observed_reason!r} expected={guard['source_infra_reason']!r}"
        )
    exception = result.get("exception_info")
    exception_type = (
        str(exception.get("exception_type") or "")
        if isinstance(exception, dict)
        else ""
    )
    if exception_type != "DeepSweVerifierInfraError":
        raise ValueError(
            "Source result is not an exact DeepSweVerifierInfraError: "
            f"{exception_type or '<missing>'}"
        )


def _expected_result(
    source_result: dict[str, Any], replacement: dict[str, Any]
) -> dict[str, Any]:
    expected = deepcopy(source_result)
    expected["verifier_result"] = deepcopy(replacement["verifier_result"])
    expected["exception_info"] = None
    return expected


def _application_payload(
    *,
    overlay_path: Path,
    overlay_sha256: str,
    guard: dict[str, Any],
    history_path: Path,
    after_sha256: str,
    recovered: bool,
) -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "kind": APPLICATION_KIND,
        "status": "applied",
        "applied_at": utc_now(),
        "overlay": {
            "path": str(overlay_path.resolve()),
            "sha256": overlay_sha256,
        },
        "source": {
            "trial_dir": guard["trial_dir"],
            "trial_name": guard["trial_name"],
            "task_name": guard["task_name"],
        },
        "before": {
            "result_sha256": guard["source_result_sha256"],
            "history_path": str(history_path.resolve()),
        },
        "after": {
            "result_sha256": after_sha256,
        },
        "source_guard": deepcopy(guard),
        "recovered_after_interrupted_recording": recovered,
    }


def _validate_existing_application(
    *,
    application: dict[str, Any],
    overlay_path: Path,
    overlay_sha256: str,
    guard: dict[str, Any],
    history_path: Path,
    result_path: Path,
    expected_result: dict[str, Any],
) -> None:
    if (
        application.get("schema_version") != SCHEMA_VERSION
        or application.get("kind") != APPLICATION_KIND
        or application.get("status") != "applied"
    ):
        raise ValueError("Existing application.json has an invalid schema or status")
    overlay_record = application.get("overlay")
    before = application.get("before")
    after = application.get("after")
    if not all(isinstance(value, dict) for value in (overlay_record, before, after)):
        raise ValueError("Existing application.json is incomplete")
    if overlay_record.get("path") != str(overlay_path.resolve()):
        raise ValueError("Existing application references a different overlay path")
    if overlay_record.get("sha256") != overlay_sha256:
        raise ValueError("Existing application references a different overlay hash")
    if before.get("result_sha256") != guard["source_result_sha256"]:
        raise ValueError("Existing application before hash does not match source guard")
    if before.get("history_path") != str(history_path.resolve()):
        raise ValueError("Existing application history path does not match")
    if application.get("source_guard") != guard:
        raise ValueError("Existing application source guard does not match overlay")
    current_hash = _sha256_file(result_path)
    if after.get("result_sha256") != current_hash:
        raise ValueError("Applied source result hash drifted from application.json")
    if read_json(result_path) != expected_result:
        raise ValueError(
            "Applied source result content drifted after overlay application"
        )


def apply_overlay(overlay_path: Path) -> dict[str, Any]:
    overlay_path = overlay_path.expanduser().resolve()
    overlay_dir = overlay_path.parent
    overlay = read_json(overlay_path)
    guard, replacement = _validate_overlay_shape(overlay)
    source_trial_dir = Path(guard["trial_dir"]).expanduser().resolve()
    if not source_trial_dir.is_dir():
        raise ValueError(
            f"Guarded source trial directory is missing: {source_trial_dir}"
        )
    if overlay_dir == source_trial_dir or overlay_dir.is_relative_to(source_trial_dir):
        raise ValueError("Overlay must be stored outside the source trial directory")

    history_path = (
        overlay_dir / "history" / f"source_result.{guard['source_result_sha256']}.json"
    )
    application_path = overlay_dir / "application.json"
    lock_dir = overlay_dir / ".application-lock"
    with exclusive_job_run(source_trial_dir.parent), exclusive_job_run(lock_dir):
        locked_overlay = read_json(overlay_path)
        if locked_overlay != overlay:
            raise ValueError("Overlay changed while acquiring application locks")
        guard, replacement = _validate_overlay_shape(locked_overlay)
        overlay_sha256 = _sha256_file(overlay_path)
        _validate_replay_result(
            overlay=locked_overlay,
            overlay_dir=overlay_dir,
            guard=guard,
            replacement=replacement,
        )
        (
            locked_source_trial_dir,
            _config_path,
            result_path,
            config,
            task,
        ) = _validate_static_source(guard)
        if locked_source_trial_dir != source_trial_dir:
            raise ValueError("Overlay source trial changed while acquiring locks")
        if history_path.exists():
            if _sha256_file(history_path) != guard["source_result_sha256"]:
                raise ValueError("Historical source result hash does not match guard")
            original_result = read_json(history_path)
            _validate_source_result(
                result=original_result,
                guard=guard,
                config=config,
                task=task,
                require_verifier_infra=True,
            )
        else:
            if _sha256_file(result_path) != guard["source_result_sha256"]:
                raise ValueError(
                    "Source result hash changed before its guarded history was saved"
                )
            original_result = read_json(result_path)
            _validate_source_result(
                result=original_result,
                guard=guard,
                config=config,
                task=task,
                require_verifier_infra=True,
            )
            copy_file_atomic(result_path, history_path)
            if _sha256_file(history_path) != guard["source_result_sha256"]:
                raise RuntimeError(
                    "Historical source result copy failed checksum validation"
                )

        expected_result = _expected_result(original_result, replacement)
        from harbor.models.trial.result import TrialResult

        TrialResult.model_validate(expected_result)

        if application_path.exists():
            application = read_json(application_path)
            _validate_existing_application(
                application=application,
                overlay_path=overlay_path,
                overlay_sha256=overlay_sha256,
                guard=guard,
                history_path=history_path,
                result_path=result_path,
                expected_result=expected_result,
            )
            return application

        current_hash = _sha256_file(result_path)
        recovered = False
        if current_hash == guard["source_result_sha256"]:
            current_result = read_json(result_path)
            if current_result != original_result:
                raise ValueError("Source result bytes match guard but content differs")
            if _sha256_file(overlay_path) != overlay_sha256:
                raise ValueError("Overlay changed before source result update")
            _validate_replay_result(
                overlay=locked_overlay,
                overlay_dir=overlay_dir,
                guard=guard,
                replacement=replacement,
            )
            _validate_static_source(guard)
            write_json_atomic(result_path, expected_result)
        else:
            current_result = read_json(result_path)
            if current_result != expected_result:
                raise ValueError(
                    "Source result changed outside this overlay; refusing recovery"
                )
            recovered = True

        after_sha256 = _sha256_file(result_path)
        if read_json(result_path) != expected_result:
            raise RuntimeError("Atomic source result update failed content validation")
        application = _application_payload(
            overlay_path=overlay_path,
            overlay_sha256=overlay_sha256,
            guard=guard,
            history_path=history_path,
            after_sha256=after_sha256,
            recovered=recovered,
        )
        write_json_atomic(application_path, application)
        return application


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Fail-closed application of one eligible DeepSWE verifier replay "
            "overlay. No agent or verifier is executed."
        )
    )
    parser.add_argument("--overlay", type=Path, required=True)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    application = apply_overlay(args.overlay)
    print(json.dumps(application, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
