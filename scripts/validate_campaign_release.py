#!/usr/bin/env python3
"""Run the complete local AutoResearch Campaign release validation.

The validator performs no remote write. It never pushes a branch, creates a
Pull Request, changes a GitHub Issue, or enables automatic merge. Every stage
writes an untruncated log and the final JSON summary records exit codes and
elapsed time.
"""

from __future__ import annotations

import argparse
import json
import shlex
import subprocess
import sys
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Sequence


FEATURE_FILES = (
    "src/qwenpaw/app/routers/research_campaign_capability_service.py",
    "src/qwenpaw/app/routers/research_campaign_delivery_mode_service.py",
    "src/qwenpaw/app/routers/research_campaign_direct_service.py",
    "src/qwenpaw/app/routers/research_campaign_monitor_service.py",
    "src/qwenpaw/app/routers/research_campaign_refresh_service.py",
    "src/qwenpaw/app/routers/research_campaign_revision_service.py",
    "src/qwenpaw/app/routers/research_campaign_worktree_control_service.py",
    "src/qwenpaw/app/routers/research_services.py",
    "src/qwenpaw/cli/campaign_cmd.py",
    "src/qwenpaw/cli/campaign_entrypoint.py",
    "src/qwenpaw/cli/campaign_group_extensions.py",
    "src/qwenpaw/cli/campaign_observer_cmd.py",
    "src/qwenpaw/cli/campaign_setup_cmd.py",
    "src/qwenpaw/cli/main.py",
    "src/qwenpaw/research_ledger/campaign_api_client.py",
    "src/qwenpaw/research_ledger/campaign_execution_runner.py",
    "src/qwenpaw/research_ledger/existing_change_request.py",
    "src/qwenpaw/research_ledger/host_evidence_reviewer.py",
    "src/qwenpaw/research_ledger/worktree_campaign.py",
)

TARGETED_TESTS = (
    "tests/unit/research_ledger/test_existing_change_request.py",
    "tests/unit/research_ledger/test_campaign_execution_runner_security.py",
    "tests/unit/research_ledger/test_host_evidence_reviewer.py",
    "tests/unit/research_ledger/test_host_evidence_reviewer_security.py",
    "tests/unit/app/routers/test_research_campaign_capability_service.py",
    "tests/unit/app/routers/test_research_campaign_revision_service.py",
    "tests/unit/app/routers/test_research_campaign_worktree_control_service.py",
    "tests/unit/cli/test_campaign_group_extensions.py",
    "tests/integration/research_ledger/test_issue_campaign_local_e2e.py",
    "tests/integration/research_ledger/test_local_campaign_verifier.py",
    "tests/integration/research_ledger/test_campaign_same_pr_revision_e2e.py",
)


@dataclass(frozen=True)
class StageResult:
    name: str
    command: tuple[str, ...]
    exit_code: int
    elapsed_seconds: float
    log: str
    skipped: bool = False

    @property
    def passed(self) -> bool:
        return self.skipped or self.exit_code == 0


def _run_stage(
    name: str,
    command: Sequence[str],
    *,
    root: Path,
    output_dir: Path,
) -> StageResult:
    log_path = output_dir / f"{name}.log"
    started = time.monotonic()
    completed = subprocess.run(
        list(command),
        cwd=root,
        capture_output=True,
        text=True,
        check=False,
    )
    elapsed = time.monotonic() - started
    log_path.write_text(
        "$ "
        + shlex.join(command)
        + "\n\n"
        + completed.stdout
        + ("\n--- STDERR ---\n" + completed.stderr if completed.stderr else ""),
        encoding="utf-8",
    )
    print(f"[{name}] exit={completed.returncode} elapsed={elapsed:.2f}s")
    return StageResult(
        name=name,
        command=tuple(command),
        exit_code=completed.returncode,
        elapsed_seconds=elapsed,
        log=str(log_path),
    )


def _skipped(name: str, output_dir: Path, reason: str) -> StageResult:
    log_path = output_dir / f"{name}.log"
    log_path.write_text(f"SKIPPED: {reason}\n", encoding="utf-8")
    print(f"[{name}] skipped: {reason}")
    return StageResult(
        name=name,
        command=(),
        exit_code=0,
        elapsed_seconds=0.0,
        log=str(log_path),
        skipped=True,
    )


def _campaign_unit_tests(root: Path) -> tuple[str, ...]:
    paths = [Path("tests/unit/research_ledger")]
    paths.extend(
        path.relative_to(root)
        for path in sorted(
            (root / "tests/unit/app/routers").glob("test_research_campaign*.py")
        )
    )
    paths.extend(
        path.relative_to(root)
        for path in sorted((root / "tests/unit/cli").glob("test_campaign*.py"))
    )
    return tuple(path.as_posix() for path in paths)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Validate the complete Campaign release without remote writes."
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path(".artifacts/campaign-release-validation"),
    )
    parser.add_argument("--skip-broad", action="store_true")
    parser.add_argument("--skip-quality", action="store_true")
    parser.add_argument("--fail-fast", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    root = Path(__file__).resolve().parents[1]
    output_dir = args.output_dir.expanduser().resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    results: list[StageResult] = []

    stages: list[tuple[str, tuple[str, ...]]] = [
        (
            "targeted-pytest",
            (
                "uv",
                "run",
                "--extra",
                "test",
                "pytest",
                "-q",
                *TARGETED_TESTS,
                "--tb=short",
            ),
        ),
    ]
    if not args.skip_broad:
        stages.extend(
            [
                (
                    "campaign-unit-suite",
                    (
                        "uv",
                        "run",
                        "--extra",
                        "test",
                        "pytest",
                        "-q",
                        *_campaign_unit_tests(root),
                        "--tb=short",
                    ),
                ),
                (
                    "campaign-integration-suite",
                    (
                        "uv",
                        "run",
                        "--extra",
                        "test",
                        "pytest",
                        "-q",
                        "tests/integration/research_ledger/",
                        "--tb=short",
                    ),
                ),
            ]
        )
    stages.extend(
        [
            (
                "compileall",
                (sys.executable, "-m", "compileall", "-q", "src/qwenpaw"),
            ),
            ("git-diff-check", ("git", "diff", "--check")),
        ]
    )
    if not args.skip_quality:
        stages.extend(
            [
                (
                    "mypy-feature-files",
                    (
                        "uv",
                        "run",
                        "--extra",
                        "dev",
                        "pre-commit",
                        "run",
                        "mypy",
                        "--files",
                        *FEATURE_FILES,
                    ),
                ),
                (
                    "flake8-feature-files",
                    (
                        "uv",
                        "run",
                        "--extra",
                        "dev",
                        "pre-commit",
                        "run",
                        "flake8",
                        "--files",
                        *FEATURE_FILES,
                    ),
                ),
            ]
        )

    for name, command in stages:
        result = _run_stage(name, command, root=root, output_dir=output_dir)
        results.append(result)
        if args.fail_fast and not result.passed:
            break

    if args.skip_broad:
        results.append(_skipped("campaign-unit-suite", output_dir, "--skip-broad"))
        results.append(
            _skipped("campaign-integration-suite", output_dir, "--skip-broad")
        )
    if args.skip_quality:
        results.append(_skipped("mypy-feature-files", output_dir, "--skip-quality"))
        results.append(_skipped("flake8-feature-files", output_dir, "--skip-quality"))

    summary = {
        "schema_version": 1,
        "remote_write_performed": False,
        "automatic_merge": False,
        "root": str(root),
        "output_dir": str(output_dir),
        "passed": all(item.passed for item in results),
        "stages": [{**asdict(item), "passed": item.passed} for item in results],
    }
    summary_path = output_dir / "summary.json"
    summary_path.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(f"Summary: {summary_path}")
    return 0 if summary["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
