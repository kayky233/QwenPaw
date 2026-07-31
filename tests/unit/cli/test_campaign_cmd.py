from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

from click.testing import CliRunner

from qwenpaw.cli.campaign_cmd import campaign_cmd
from qwenpaw.research_ledger.remote_campaign_control import (
    CLEANUP_CONFIRMATION,
    PROMOTE_CONFIRMATION,
)
from qwenpaw.research_ledger.remote_campaign_e2e import REMOTE_CONFIRMATION


def test_campaign_help_lists_verification_and_control_commands() -> None:
    result = CliRunner().invoke(campaign_cmd, ["--help"])
    assert result.exit_code == 0
    assert "local-verify" in result.output
    assert "remote-e2e" in result.output
    assert "promote-ready" in result.output
    assert "cleanup-e2e" in result.output


def test_remote_e2e_rejects_confirmation_before_execution(
    tmp_path: Path,
    monkeypatch,
) -> None:
    repository = tmp_path / "repository"
    repository.mkdir()
    episode = tmp_path / "episode.json"
    episode.write_text("{}", encoding="utf-8")
    patch = tmp_path / "candidate.patch"
    patch.write_text("", encoding="utf-8")
    execute = MagicMock()
    monkeypatch.setattr(
        "qwenpaw.cli.campaign_cmd.run_guarded_remote_draft_pr_e2e",
        execute,
    )

    result = CliRunner().invoke(
        campaign_cmd,
        [
            "remote-e2e",
            "--repository",
            str(repository),
            "--episode",
            str(episode),
            "--patch",
            str(patch),
            "--report-dir",
            str(tmp_path / "report"),
            "--base-branch",
            "main",
            "--allow-repository",
            "owner/e2e-repo",
            "--confirm",
            "wrong",
        ],
    )

    assert result.exit_code != 0
    assert "confirmation mismatch" in result.output
    execute.assert_not_called()
    assert REMOTE_CONFIRMATION not in result.output


def test_promote_and_cleanup_require_distinct_confirmation_tokens(
    tmp_path: Path,
    monkeypatch,
) -> None:
    report = tmp_path / "remote-verification.json"
    report.write_text("{}", encoding="utf-8")
    promote = MagicMock()
    cleanup = MagicMock()
    monkeypatch.setattr(
        "qwenpaw.cli.campaign_cmd.promote_remote_e2e_draft",
        promote,
    )
    monkeypatch.setattr(
        "qwenpaw.cli.campaign_cmd.cleanup_remote_e2e_pull_request",
        cleanup,
    )

    promote_result = CliRunner().invoke(
        campaign_cmd,
        [
            "promote-ready",
            "--report",
            str(report),
            "--allow-repository",
            "owner/e2e-repo",
            "--confirm",
            CLEANUP_CONFIRMATION,
        ],
    )
    cleanup_result = CliRunner().invoke(
        campaign_cmd,
        [
            "cleanup-e2e",
            "--report",
            str(report),
            "--allow-repository",
            "owner/e2e-repo",
            "--confirm",
            PROMOTE_CONFIRMATION,
        ],
    )

    assert promote_result.exit_code != 0
    assert cleanup_result.exit_code != 0
    promote.assert_not_called()
    cleanup.assert_not_called()
