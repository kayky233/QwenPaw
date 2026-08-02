from __future__ import annotations

from pathlib import Path

from qwenpaw.research_ledger.campaign_execution_runner import (
    CampaignSubprocessRunner,
    redact_campaign_output,
)
from qwenpaw.research_ledger.execution_runner import (
    CommandRequest,
    CommandResult,
    ExecutionCapabilities,
)


class _Delegate:
    def __init__(self) -> None:
        self.request: CommandRequest | None = None

    @property
    def capabilities(self) -> ExecutionCapabilities:
        return ExecutionCapabilities("test", False, True, True)

    def run(self, request: CommandRequest) -> CommandResult:
        self.request = request
        return CommandResult(
            argv=request.argv,
            cwd=request.cwd,
            exit_code=0,
            stdout="token=github_pat_abcdefghijklmnopqrstuvwxyz123456",
            stderr="Authorization: Bearer ghp_abcdefghijklmnopqrstuvwxyz123456",
            duration_seconds=0.01,
            runner_name="delegate",
        )


def test_runner_uses_cross_platform_minimal_environment(tmp_path: Path) -> None:
    source = {
        "PATH": "/bin",
        "SYSTEMROOT": "C:/Windows",
        "COMSPEC": "C:/Windows/System32/cmd.exe",
        "PATHEXT": ".EXE;.CMD",
        "SSL_CERT_FILE": "/etc/certs.pem",
        "REQUESTS_CA_BUNDLE": "/etc/requests.pem",
        "CARGO_HOME": "/secret/cargo",
        "GITHUB_TOKEN": "github_pat_should_not_escape",
        "OPENAI_API_KEY": "sk-should-not-escape",
        "HOME": "/real/home",
    }
    runner = CampaignSubprocessRunner(
        tmp_path / "isolated-home",
        source_environment=source,
    )

    assert runner.base_environment["PATH"] == "/bin"
    assert runner.base_environment["SYSTEMROOT"] == "C:/Windows"
    assert runner.base_environment["COMSPEC"].endswith("cmd.exe")
    assert runner.base_environment["PATHEXT"] == ".EXE;.CMD"
    assert runner.base_environment["SSL_CERT_FILE"] == "/etc/certs.pem"
    assert runner.base_environment["REQUESTS_CA_BUNDLE"] == "/etc/requests.pem"
    assert runner.base_environment["HOME"] == str((tmp_path / "isolated-home").resolve())
    assert runner.base_environment["USERPROFILE"] == runner.base_environment["HOME"]
    assert "CARGO_HOME" not in runner.base_environment
    assert "GITHUB_TOKEN" not in runner.base_environment
    assert "OPENAI_API_KEY" not in runner.base_environment


def test_runner_blocks_request_credentials_and_redacts_output(tmp_path: Path) -> None:
    runner = CampaignSubprocessRunner(
        tmp_path / "home",
        source_environment={"PATH": "/bin"},
    )
    delegate = _Delegate()
    runner.delegate = delegate

    result = runner.run(
        CommandRequest(
            argv=("python", "-V"),
            cwd=str(tmp_path),
            environment={
                "LANG": "C.UTF-8",
                "GITHUB_TOKEN": "github_pat_request_secret",
                "API_KEY": "request-secret",
            },
        )
    )

    assert delegate.request is not None
    assert delegate.request.environment["LANG"] == "C.UTF-8"
    assert "GITHUB_TOKEN" not in delegate.request.environment
    assert "API_KEY" not in delegate.request.environment
    assert "github_pat_" not in result.stdout
    assert "ghp_" not in result.stderr
    assert "[REDACTED]" in result.stdout
    assert "[REDACTED]" in result.stderr


def test_redaction_handles_private_keys_and_provider_tokens() -> None:
    raw = (
        "sk-abcdefghijklmnopqrstuvwxyz\n"
        "-----BEGIN PRIVATE KEY-----\nsecret\n-----END PRIVATE KEY-----"
    )

    redacted = redact_campaign_output(raw)

    assert "sk-" not in redacted
    assert "secret" not in redacted
    assert redacted.count("[REDACTED]") == 2
