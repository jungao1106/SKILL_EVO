#!/usr/bin/env python
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from evolution.frozen_library import materialize_frozen_skill_library_from_files


DEFAULT_SKILL_ROOT = ROOT / "skills" / "accepted"
DEFAULT_OUTPUT_ROOT = ROOT / "skills" / "downstream"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Materialize a frozen training-distilled downstream skill library."
    )
    parser.add_argument("--run-name", required=True)
    parser.add_argument("--skill-version-id", required=True)
    parser.add_argument("--skill-root", type=Path, default=DEFAULT_SKILL_ROOT)
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT_ROOT)
    parser.add_argument("--promotion-decisions", type=Path, required=True)
    parser.add_argument("--min-success-repo-support", type=int, default=2)
    parser.add_argument("--min-success-positive-support", type=int, default=2)
    parser.add_argument("--max-success-skills", type=int, default=8)
    parser.add_argument("--include-memory-only-success", action="store_true")
    parser.add_argument("--min-failure-repo-support", type=int, default=2)
    parser.add_argument("--max-failure-skills", type=int, default=8)
    parser.add_argument("--include-support-1-failures", action="store_true")
    parser.add_argument("--exclude-general", action="store_true")
    parser.add_argument("--reuse", action="store_true")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    source_skill_root = args.skill_root.expanduser().resolve() / args.skill_version_id
    promotion_decisions_path = args.promotion_decisions.expanduser().resolve()
    output_root = args.output_root.expanduser().resolve() / args.run_name / args.skill_version_id
    if not source_skill_root.exists():
        raise SystemExit(f"Missing source skill root: {source_skill_root}")
    if not promotion_decisions_path.exists():
        raise SystemExit(f"Missing promotion decisions: {promotion_decisions_path}")
    manifest_path = output_root / "frozen_library_manifest.json"
    if args.reuse and manifest_path.exists():
        manifest = json.loads(manifest_path.read_text(errors="replace"))
    else:
        manifest = materialize_frozen_skill_library_from_files(
            source_skill_root=source_skill_root,
            output_root=output_root,
            promotion_decisions_path=promotion_decisions_path,
            run_name=args.run_name,
            min_success_repo_support=args.min_success_repo_support,
            min_success_positive_support=args.min_success_positive_support,
            max_success_skills=args.max_success_skills,
            require_accepted_success=not args.include_memory_only_success,
            min_failure_repo_support=args.min_failure_repo_support,
            max_failure_skills=args.max_failure_skills,
            include_support_1_failures=args.include_support_1_failures,
            include_general=not args.exclude_general,
            clean=not args.reuse,
        )
    print(json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
