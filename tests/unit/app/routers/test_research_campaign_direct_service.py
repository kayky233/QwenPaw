from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import APIRouter, HTTPException
from starlette.requests import Request

from qwenpaw.app.routers import research_campaign_direct_service as service


class _Episode:
    episode_id = "episode-1"

    @staticmethod
    def digest() -> str:
        return "d" * 64


def _outcome() -> SimpleNamespace:
    return SimpleNamespace(
        status=SimpleNamespace(value="delivered"),
        reason="",
        episode=_Episode(),
        attempts=(),
        artifacts=(),
        evidence=None,
        delivery=None,
        delivery_receipt=None,
    )


def _module(tmp_path: Path) -> SimpleNamespace:
    async def execute(*args, **kwargs):
        return SimpleNamespace(
            outcome=_outcome(),
            worktree=str(tmp_path / "worktree"),
            branch="autoresearch/issue-7-test",
            base_branch="main",
        )

    return SimpleNamespace(
        router=APIRouter(prefix="/research"),
        WORKING_DIR=tmp_path,
        __file__=str(tmp_path / "src/qwenpaw/app/routers/research.py"),
        unsafe_research_enabled=lambda: True,
        UNSAFE_RESEARCH_OPT_IN="QWENPAW_UNSAFE_RESEARCH",
        _request_owner_identity=lambda request, session_id=None: (
            "default",
            None,
            session_id,
        ),
        _campaign_runs={},
        _campaign_tasks={},
        _campaign_sse_queues={},
        _campaign_snapshot_root=tmp_path / "snapshots",
        _dialog_runtime_context={},
        _execute_issue_campaign=execute,
    )


def _endpoint(module, path: str, method: str):
    return next(
        route.endpoint
        for route in module.router.routes
        if route.path == f"/research{path}" and method in route.methods
    )


def _agents(*, reviewer_status: str = "running") -> dict:
    return {
        "agents": [
            {
                "id": "coder",
                "workspace_dir": "/tmp/coder",
                "enabled": True,
                "startup_status": "running",
            },
            {
                "id": "reviewer",
                "workspace_dir": "/tmp/reviewer",
                "enabled": True,
                "startup_status": reviewer_status,
            },
        ]
    }


def _request() -> Request:
    return Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/research/campaigns/run",
            "headers": [],
            "app": SimpleNamespace(
                state=SimpleNamespace(
                    multi_agent_manager=None,
                    app_services=None,
                )
            ),
        }
    )


def _body(*, delivery_mode: str = "local") -> service.RunIssueCampaignRequest:
    return service.RunIssueCampaignRequest(
        repository="owner/repository",
        issue_number=7,
        acceptance_criteria=["Fix the regression"],
        modifiable_files=["src/fix.py", "tests/test_fix.py"],
        commands=[
            {
                "command_id": "unit",
                "stage": "unit",
                "argv": ["pytest", "-q", "tests/test_fix.py"],
            }
        ],
        implementer_agent_id="coder",
        reviewer_agent_id="reviewer",
        delivery_mode=delivery_mode,
    )


@pytest.mark.asyncio
async def test_direct_run_defaults_to_local_delivery_and_persists_state(
    tmp_path: Path,
    monkeypatch,
) -> None:
    module = _module(tmp_path)
    monkeypatch.setattr(service, "list_agents_data", _agents)
    monkeypatch.setattr(service.shutil, "which", lambda name: f"/usr/bin/{name}")
    service.install_research_campaign_direct_service(module)

    response = await _endpoint(module, "/campaigns/run", "POST")(
        _body(),
        _request(),
    )
    await module._campaign_tasks[response["campaign_id"]]
    state = module._campaign_runs[response["campaign_id"]]

    assert response["delivery_mode"] == "local"
    assert state.status == "delivered"
    assert state.outcome["delivery_mode"] == "local"
    assert (module._campaign_snapshot_root / f"{state.campaign_id}.json").is_file()


@pytest.mark.asyncio
async def test_direct_run_rejects_non_running_agent_before_acceptance(
    tmp_path: Path,
    monkeypatch,
) -> None:
    module = _module(tmp_path)
    monkeypatch.setattr(
        service,
        "list_agents_data",
        lambda: _agents(reviewer_status="failed"),
    )
    monkeypatch.setattr(service.shutil, "which", lambda name: f"/usr/bin/{name}")
    service.install_research_campaign_direct_service(module)

    with pytest.raises(HTTPException) as exc_info:
        await _endpoint(module, "/campaigns/run", "POST")(
            _body(),
            _request(),
        )

    assert exc_info.value.status_code == 422
    assert "reviewer" in str(exc_info.value.detail)
    assert module._campaign_runs == {}


@pytest.mark.asyncio
async def test_campaign_info_exposes_only_running_agents_and_capabilities(
    tmp_path: Path,
    monkeypatch,
) -> None:
    module = _module(tmp_path)
    monkeypatch.setattr(
        service,
        "list_agents_data",
        lambda: {
            "agents": [
                *_agents()["agents"],
                {
                    "id": "failed-reviewer",
                    "workspace_dir": "/tmp/failed",
                    "enabled": True,
                    "startup_status": "failed",
                },
            ]
        },
    )
    monkeypatch.setattr(service.shutil, "which", lambda name: f"/usr/bin/{name}")
    service.install_research_campaign_direct_service(module)

    payload = await _endpoint(module, "/campaigns-info", "GET")()

    assert payload["available"] is True
    assert payload["default_delivery_mode"] == "local"
    assert payload["automatic_merge"] is False
    assert payload["remote_delivery_available"] is True
    assert [item["id"] for item in payload["agents"]] == ["coder", "reviewer"]
    assert [item["id"] for item in payload["unavailable_agents"]] == [
        "failed-reviewer"
    ]
