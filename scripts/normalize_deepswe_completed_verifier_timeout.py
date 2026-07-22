#!/usr/bin/env python
"""Normalize a repeated, completed DeepSWE test-suite timeout to reward zero."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import tempfile
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in os.sys.path:
    os.sys.path.insert(0, str(ROOT))

from scripts.job_run_lock import exclusive_job_run  # noqa: E402


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def canonical_sha256(value: Any) -> str:
    payload = json.dumps(value, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode()).hexdigest()


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
        handle.flush()
        os.fsync(handle.fileno())
        temporary = Path(handle.name)
    os.replace(temporary, path)


def junit_outcomes(path: Path) -> dict[str, Any]:
    try:
        root = ET.parse(path).getroot()
    except (OSError, ET.ParseError) as exc:
        raise ValueError(f"Invalid completed JUnit XML {path}: {exc}") from exc
    cases: list[tuple[str, str, str]] = []
    for case in root.iter("testcase"):
        identity = (str(case.get("classname") or ""), str(case.get("name") or ""))
        if not all(identity):
            raise ValueError(f"JUnit testcase has no stable identity: {path}")
        if case.find("error") is not None:
            status = "error"
        elif case.find("failure") is not None:
            status = "failure"
        elif case.find("skipped") is not None:
            status = "skipped"
        else:
            status = "passed"
        cases.append((*identity, status))
    if not cases or len(cases) != len(set((row[0], row[1]) for row in cases)):
        raise ValueError(f"JUnit XML has no unique completed testcases: {path}")
    cases.sort()
    counts = {
        status: sum(row[2] == status for row in cases)
        for status in ("passed", "failure", "error", "skipped")
    }
    return {
        "tests": len(cases),
        **counts,
        "outcomes_sha256": canonical_sha256(cases),
        "failed_tests": [f"{row[0]}::{row[1]}" for row in cases if row[2] == "failure"],
    }


def repeated_completed_suite_evidence(trial_dir: Path) -> list[dict[str, Any]]:
    evidence: list[dict[str, Any]] = []
    for attempt in (1, 2):
        attempt_dir = trial_dir / "verifier_attempts" / f"timeout_attempt_{attempt}"
        base_path = attempt_dir / "base.xml"
        new_path = attempt_dir / "new.xml"
        base = junit_outcomes(base_path)
        new = junit_outcomes(new_path)
        if base["failure"] or base["error"]:
            raise ValueError(f"Base suite had failures or errors in {attempt_dir}")
        if new["failure"] <= 0 or new["error"] or new["skipped"]:
            raise ValueError(
                f"New suite does not prove a cleanly completed model failure in {attempt_dir}"
            )
        evidence.append(
            {
                "attempt": attempt,
                "base_xml": str(base_path.resolve()),
                "base_xml_sha256": sha256_file(base_path),
                "base": base,
                "new_xml": str(new_path.resolve()),
                "new_xml_sha256": sha256_file(new_path),
                "new": new,
            }
        )
    for key in ("tests", "passed", "failure", "error", "skipped", "outcomes_sha256"):
        if evidence[0]["base"][key] != evidence[1]["base"][key]:
            raise ValueError(f"Fresh verifier base outcomes differ at {key}")
        if evidence[0]["new"][key] != evidence[1]["new"][key]:
            raise ValueError(f"Fresh verifier new outcomes differ at {key}")
    return evidence


def normalize_trial(trial_dir: Path) -> dict[str, Any]:
    from harbor.models.task.task import Task
    from harbor.models.trial.config import TrialConfig
    from harbor.models.trial.result import TrialResult

    trial_dir = trial_dir.expanduser().resolve()
    job_dir = trial_dir.parent
    config_path = trial_dir / "config.json"
    result_path = trial_dir / "result.json"
    patch_path = trial_dir / "artifacts" / "model.patch"
    backup_path = trial_dir / "result.pre-terminal-normalization.json"
    outcome_path = trial_dir / "terminal_outcome.json"
    for path in (config_path, result_path, patch_path):
        if not path.is_file():
            raise ValueError(f"Missing required trial artifact: {path}")

    with exclusive_job_run(job_dir):
        result = read_json(result_path)
        if outcome_path.is_file():
            outcome = read_json(outcome_path)
            reward = ((result.get("verifier_result") or {}).get("rewards") or {}).get(
                "reward"
            )
            if (
                outcome.get("classification")
                == "repeated-completed-tests-post-suite-hang"
                and reward == 0
                and result.get("exception_info") is None
                and backup_path.is_file()
                and sha256_file(backup_path) == outcome.get("source_result_sha256")
            ):
                return outcome
            raise ValueError("Existing terminal normalization is incomplete or different")

        config = TrialConfig.model_validate_json(config_path.read_text(errors="replace"))
        validated = TrialResult.model_validate(result)
        task_path = getattr(config.task, "path", None)
        if task_path is None:
            raise ValueError("Terminal normalization requires a local DeepSWE task")
        task = Task(Path(task_path).expanduser().resolve())
        if validated.config != config or validated.task_checksum != task.checksum:
            raise ValueError("Trial config or task checksum differs from the result")
        exception = result.get("exception_info")
        exception_type = (
            str(exception.get("exception_type") or "")
            if isinstance(exception, dict)
            else ""
        )
        if exception_type != "VerifierTimeoutError" or result.get("verifier_result") is not None:
            raise ValueError("Trial is not an unresolved VerifierTimeoutError")
        metadata = (result.get("agent_result") or {}).get("metadata") or {}
        if metadata.get("completed") is not True:
            raise ValueError("Agent result is not explicitly completed")
        if patch_path.stat().st_size <= 0:
            raise ValueError("Saved model.patch is empty")

        evidence = repeated_completed_suite_evidence(trial_dir)
        original_bytes = result_path.read_bytes()
        original_sha256 = hashlib.sha256(original_bytes).hexdigest()
        if backup_path.exists():
            if backup_path.read_bytes() != original_bytes:
                raise ValueError("Existing source result backup differs")
        else:
            backup_path.write_bytes(original_bytes)
            with backup_path.open("rb") as handle:
                os.fsync(handle.fileno())

        normalized = dict(result)
        normalized["verifier_result"] = {"rewards": {"reward": 0}}
        normalized["exception_info"] = None
        TrialResult.model_validate(normalized)
        write_json_atomic(result_path, normalized)
        outcome = {
            "schema_version": 1,
            "classification": "repeated-completed-tests-post-suite-hang",
            "assigned_reward": 0,
            "normalized_at": utc_now(),
            "trial_dir": str(trial_dir),
            "trial_name": validated.trial_name,
            "task_name": validated.task_name,
            "source_result_sha256": original_sha256,
            "normalized_result_sha256": sha256_file(result_path),
            "model_patch_sha256": sha256_file(patch_path),
            "model_patch_size_bytes": patch_path.stat().st_size,
            "fresh_verifier_attempts": evidence,
        }
        write_json_atomic(outcome_path, outcome)

        root_result_path = job_dir / "result.json"
        if root_result_path.is_file():
            root = read_json(root_result_path)
            root_backup = job_dir / "result.pre-terminal-normalization.json"
            if root_backup.exists():
                if root_backup.read_bytes() != root_result_path.read_bytes():
                    raise ValueError("Existing root result backup differs")
            else:
                root_backup.write_bytes(root_result_path.read_bytes())
            root["finished_at"] = None
            write_json_atomic(root_result_path, root)
        return outcome


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--trial-dir", type=Path, required=True)
    return parser.parse_args()


def main() -> None:
    print(json.dumps(normalize_trial(parse_args().trial_dir), indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
