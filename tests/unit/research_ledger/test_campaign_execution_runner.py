from __future__ import annotations

import json
import sys
from pathlib import Path

from qwenpaw.research_ledger.campaign_execution_runner import (
    CampaignSubprocessRunner,
    redact_campaign_output,
)
from qwenpaw.research_ledger.execution_runner import CommandRequest


def test_campaign_runner_uses_minimal_environment_and_isolated_home(
    tmp_path: Path,
) -> None:
    home = tmp_path / "home"
    runner = CampaignSubprocessRunner(
        home,
        source_environment={
            "PATH": "/usr/bin:/bin",
            "LANG": "C.UTF-8",
            "GITHUB_TOKEN": "github-secret",
            "OPENAI_API_KEY": "model-secret",
            "DATABASE_PASSWORD": "database-secret",
        },
    )

    assert runner.base_environment["PATH"] == "/usr/bin:/bin"
    assert runner.base_environment["HOME"] == str(home.resolve())
    assert runner.base_environment["GIT_TERMINAL_PROMPT"] == "0"
    assert "GITHUB_TOKEN" not in runner.base_environment
    assert "OPENAI_API_KEY" not in runner.base_environment
    assert "DATABASE_PASSWORD" not in runner.base_environment


def test_campaign_runner_redacts_persisted_command_output(tmp_path: Path) -> None:
    home = tmp_path / "home"
    runner = CampaignSubprocessRunner(
        home,
        source_environment={
            "PATH": "/usr/bin:/bin",
            "GITHUB_TOKEN": "should-not-be-visible",
        },
    )
    script = (
        "import json, os; "
        "print(json.dumps({'home': os.environ.get('HOME'), "
        "'github': os.environ.get('GITHUB_TOKEN')})); "
        "print('Authorization: Bearer ghp_abcdefghijklmnopqrstuvwxyz123456')"
    )

    result = runner.run(
        CommandRequest(
            argv=(sys.executable, "-c", script),
            cwd=str(tmp_path),
            environment={
                "GITHUB_TOKEN": "request-secret",
                "LANG": "C.UTF-8",
            },
        )
    )

    first_line = json.loads(result.stdout.splitlines()[0])
    assert result.exit_code == 0
    assert first_line["home"] == str(home.resolve())
    assert first_line["github"] is None
    assert "ghp_" not in result.stdout
    assert "[REDACTED]" in result.stdout


def test_redaction_covers_common_secret_forms() -> None:
    raw = (
        "token=plain-secret\n"
        "api_key: sk-abcdefghijklmnopqrstuv\n"
        "github_pat_ABCDEFGHIJKLMNOPQRSTUVWXYZ123456\n"
        "-----BEGIN PRIVATE KEY-----\nsecret\n-----END PRIVATE KEY-----"
    )

    redacted = redact_campaign_output(raw)

    assert "plain-secret" not in redacted
    assert "sk-" not in redacted
    assert "github_pat_" not in redacted
    assert "BEGIN PRIVATE KEY" not in redacted
    assert redacted.count("[REDACTED]") >= 4
