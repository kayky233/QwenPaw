# -*- coding: utf-8 -*-
"""Integration coverage for the local-only Campaign verifier."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from qwenpaw.research_ledger.contracts import ResearchArtifactType
from qwenpaw.research_ledger.episode_package import (
    EpisodeCommand,
    EpisodeExpectedArtifact,
    EpisodePackage,
)
from qwenpaw.research_ledger.local_campaign_verifier import (
    verify_local_campaign,
)


def _run(repository: Path, *argv: str) -> str:
    completed = subprocess.run(
        list(argv),
        cwd=repository,
        capture_output=True,
        text=True,
        check=True,
    )
    return completed.stdout.strip()


def _repository(tmp_path: Path) -> tuple[Path, str]:
    repository = tmp_path / "repository"
    repository.mkdir()
    _run(repository, "git", "init")
    _run(repository, "git", "config", "user.name", "Test User")
    _run(repository, "git", "config", "user.email", "test@example.com")
    source = repository / "src" / "value.py"
    source.parent.mkdir(parents=True)
    source.write_text("VALUE = 1\n", encoding="utf-8")
    _run(repository, "git", "add", "--all")
    _run(repository, "git", "commit", "-m", "baseline")
    return repository, _run(repository, "git", "rev-parse", "HEAD")


def _episode(
    path: Path,
    base_revision: str,
    *,
    command_argv: tuple[str, ...] | None = None,
) -> EpisodePackage:
    argv = command_argv or (
        sys.executable,
        "-c",
        (
            "source = open('src/value.py', encoding='utf-8').read(); "
            "compile(source, 'src/value.py', 'exec')"
        ),
    )
    package = EpisodePackage(
        episode_id="local-e2e-issue-1",
        run_id="local-e2e",
        task_type="bug_fix",
        repository="example/local-repository",
        issue_number=1,
        goal="Update the verified value",
        base_revision=base_revision,
        acceptance_criteria=("VALUE is changed from 1 to 2",),
        modifiable_files=("src/value.py",),
        commands=(
            EpisodeCommand(
                command_id="compile",
                stage="unit",
                argv=argv,
            ),
        ),
        expected_artifacts=(
            EpisodeExpectedArtifact(ResearchArtifactType.PLAN, "plan"),
            EpisodeExpectedArtifact(
                ResearchArtifactType.CODE_DIFF,
                "implement",
            ),
            EpisodeExpectedArtifact(
                ResearchArtifactType.TEST_RESULT,
                "test",
            ),
            EpisodeExpectedArtifact(ResearchArtifactType.REPORT, "review"),
            EpisodeExpectedArtifact(ResearchArtifactType.COMMIT, "delivery"),
        ),
    )
    path.write_text(package.to_json(), encoding="utf-8")
    return package


def _patch(repository: Path, path: Path) -> None:
    source = repository / "src" / "value.py"
    source.write_text("VALUE = 2\n", encoding="utf-8")
    path.write_text(
        _run(repository, "git", "diff", "--binary"),
        encoding="utf-8",
    )
    _run(repository, "git", "checkout", "--", "src/value.py")


@pytest.mark.asyncio
async def test_local_verifier_runs_full_campaign_without_remote_write(
    tmp_path: Path,
) -> None:
    repository, base_revision = _repository(tmp_path)
    episode_path = tmp_path / "episode.json"
    patch_path = tmp_path / "candidate.patch"
    report_dir = tmp_path / "report"
    _episode(episode_path, base_revision)
    _patch(repository, patch_path)

    result = await verify_local_campaign(
        repository,
        episode_path,
        patch_path,
        report_dir,
    )

    assert result.status == "verified_local_only"
    assert result.remote_created is False
    assert result.commit_sha != base_revision
    assert result.local_change_request_url.startswith("local://")
    assert set(result.artifact_types) == {
        "plan",
        "code_diff",
        "test_result",
        "report",
        "commit",
        "pull_request",
    }
    assert not Path(result.worktree).exists()
    assert _run(repository, "git", "rev-parse", "HEAD") == base_revision
    assert _run(repository, "git", "status", "--porcelain") == ""
    assert _run(repository, "git", "branch", "--list", result.branch) == ""

    report = json.loads(Path(result.report_json).read_text(encoding="utf-8"))
    assert report["result"]["remote_created"] is False
    assert report["remote_request"]["draft"] is True
    assert report["remote_request"]["head_branch"] == result.branch
    assert len(report["artifacts"]) == 6

    markdown = Path(result.report_markdown).read_text(encoding="utf-8")
    assert "Remote change request created: `no`" in markdown
    assert "Automatic merge" not in markdown
    assert result.commit_sha in markdown


@pytest.mark.asyncio
async def test_local_verifier_rejects_validation_side_effects(
    tmp_path: Path,
) -> None:
    repository, base_revision = _repository(tmp_path)
    episode_path = tmp_path / "episode.json"
    patch_path = tmp_path / "candidate.patch"
    report_dir = tmp_path / "report"
    _episode(
        episode_path,
        base_revision,
        command_argv=(
            sys.executable,
            "-c",
            "open('generated.txt', 'w', encoding='utf-8').write('side effect')",
        ),
    )
    _patch(repository, patch_path)

    with pytest.raises(RuntimeError, match="changed after validation"):
        await verify_local_campaign(
            repository,
            episode_path,
            patch_path,
            report_dir,
        )

    assert _run(repository, "git", "rev-parse", "HEAD") == base_revision
    assert _run(repository, "git", "status", "--porcelain") == ""
    assert not (report_dir / "local-e2e-worktree").exists()
