from __future__ import annotations

from click.testing import CliRunner

from qwenpaw.cli import campaign_setup_cmd as module


class _Response:
    def __init__(self, payload, status_code=200) -> None:
        self.payload = payload
        self.status_code = status_code
        self.text = ""

    def raise_for_status(self) -> None:
        return None

    def json(self):
        return self.payload


class _Client:
    def __init__(self, existing=(), *, startup_status="running") -> None:
        self.existing = set(existing)
        self.startup_status = startup_status
        self.created = []

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return None

    def get(self, path):
        assert path == "/agents"
        return _Response(
            {
                "agents": [
                    {
                        "id": item,
                        "workspace_dir": f"/tmp/{item}",
                        "startup_status": self.startup_status,
                    }
                    for item in sorted(self.existing)
                ]
            }
        )

    def post(self, path, *, json):
        assert path == "/agents"
        self.created.append(json)
        self.existing.add(json["id"])
        return _Response(
            {
                "id": json["id"],
                "workspace_dir": json.get("workspace_dir", ""),
            },
            status_code=201,
        )


def test_setup_creates_separate_running_campaign_agents(
    monkeypatch,
    tmp_path,
) -> None:
    client = _Client()
    monkeypatch.setattr(
        module,
        "create_agent_api_client",
        lambda _: client,
    )

    result = CliRunner().invoke(
        module.campaign_setup_cmd,
        [
            "--implementer",
            "coder",
            "--reviewer",
            "reviewer",
            "--workspace-root",
            str(tmp_path),
            "--poll-interval",
            "0.1",
        ],
    )

    assert result.exit_code == 0, result.output
    assert [item["id"] for item in client.created] == ["coder", "reviewer"]
    assert client.created[0]["workspace_dir"].endswith("/coder")
    assert client.created[1]["workspace_dir"].endswith("/reviewer")
    assert '"ready": true' in result.output
    assert '"startup_status": "running"' in result.output


def test_setup_does_not_overwrite_existing_agents(monkeypatch) -> None:
    client = _Client(existing=("coder", "reviewer"))
    monkeypatch.setattr(
        module,
        "create_agent_api_client",
        lambda _: client,
    )

    result = CliRunner().invoke(
        module.campaign_setup_cmd,
        ["--implementer", "coder", "--reviewer", "reviewer"],
    )

    assert result.exit_code == 0, result.output
    assert client.created == []
    assert '"skipped_existing"' in result.output


def test_setup_fails_when_agent_startup_failed(monkeypatch) -> None:
    client = _Client(
        existing=("coder", "reviewer"),
        startup_status="failed",
    )
    monkeypatch.setattr(
        module,
        "create_agent_api_client",
        lambda _: client,
    )

    result = CliRunner().invoke(
        module.campaign_setup_cmd,
        ["--implementer", "coder", "--reviewer", "reviewer"],
    )

    assert result.exit_code != 0
    assert "startup failed" in result.output


def test_setup_rejects_self_review() -> None:
    result = CliRunner().invoke(
        module.campaign_setup_cmd,
        ["--implementer", "same", "--reviewer", "same"],
    )

    assert result.exit_code != 0
    assert "must be different" in result.output
