from __future__ import annotations

from types import SimpleNamespace

from click.testing import CliRunner

from qwenpaw.cli import campaign_observer_cmd as module


class _Client:
    def __init__(self) -> None:
        self.revisions = []

    def get(self, campaign_id):
        return {"campaign_id": campaign_id, "status": "delivered", "events": []}

    def revise(self, campaign_id, **kwargs):
        self.revisions.append((campaign_id, kwargs))
        return {"status": "revision_accepted"}

    def wait(self, campaign_id, **kwargs):
        del kwargs
        return SimpleNamespace(
            state={
                "campaign_id": campaign_id,
                "status": "delivered",
                "events": [],
                "outcome": {"revision_history": [{"revision_number": 1}]},
            },
            elapsed_seconds=0.1,
        )


def test_explicit_revision_runs_even_when_campaign_is_delivered(
    monkeypatch,
    tmp_path,
) -> None:
    client = _Client()
    monkeypatch.setattr(module, "_client", lambda *args, **kwargs: client)
    runner = CliRunner()

    result = runner.invoke(
        module.campaign_revise_cmd,
        [
            "campaign-1",
            "--feedback",
            "address new reviewer feedback",
            "--report",
            str(tmp_path / "result.json"),
        ],
        obj={"host": "127.0.0.1", "port": 8088},
    )

    assert result.exit_code == 0
    assert len(client.revisions) == 1
    campaign_id, payload = client.revisions[0]
    assert campaign_id == "campaign-1"
    assert payload["feedback"] == "address new reviewer feedback"
    assert (tmp_path / "result.json").is_file()
    assert (tmp_path / "result.md").is_file()
