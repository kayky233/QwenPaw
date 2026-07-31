from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import APIRouter
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


@pytest.mark.asyncio
async def test_direct_run_defaults_to_local_delivery_and_persists_state(
    tmp_path: Path,
) -> None:
    module = _module(tmp_path)
    service.install_research_campaign_direct_service(module)
    request = Request(
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
    body = service.RunIssueCampaignRequest(
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
    )

    response = await _endpoint(module, "/campaigns/run", "POST")(
        body,
        request,
    )
    await module._campaign_tasks[response["campaign_id"]]
    state = module._campaign_runs[response["campaign_id"]]

    assert response["delivery_mode"] == "local"
    assert state.status == "delivered"
    assert state.outcome["delivery_mode"] == "local"
    assert (module._campaign_snapshot_root / f"{state.campaign_id}.json").is_file()


@pytest.mark.asyncio
async def test_campaign_info_exposes_agents_and_disables_auto_merge(
    tmp_path: Path,
    monkeypatch,
) -> None:
    module = _module(tmp_path)
    monkeypatch.setattr(
        service,
        "list_agents_data",
        lambda: {
            "agents": [
                {"id": "coder", "workspace_dir": "/tmp/coder"},
                {"id": "reviewer", "workspace_dir": "/tmp/reviewer"},
            ]
        },
    )
    service.install_research_campaign_direct_service(module)

    payload = await _endpoint(module, "/campaigns-info", "GET")()

    assert payload["default_delivery_mode"] == "local"
    assert payload["automatic_merge"] is False
    assert [item["id"] for item in payload["agents"]] == ["coder", "reviewer"]
