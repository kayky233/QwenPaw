from __future__ import annotations

from click.testing import CliRunner

from qwenpaw.cli.campaign_entrypoint import campaign_cmd


def test_campaign_group_exposes_direct_recovery_commands() -> None:
    runner = CliRunner()

    result = runner.invoke(campaign_cmd, ["--help"])

    assert result.exit_code == 0
    assert "setup-agents" in result.output
    assert "revise" in result.output
    assert "recovery" in result.output
    assert "cleanup-worktree" in result.output


def test_campaign_cleanup_alias_requires_explicit_confirmation() -> None:
    runner = CliRunner()

    result = runner.invoke(
        campaign_cmd,
        ["cleanup-worktree", "campaign-1", "--confirm", "wrong"],
    )

    assert result.exit_code != 0
    assert "confirmation mismatch" in result.output
