from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest
from fastapi import APIRouter
from starlette.requests import Request

from qwenpaw.app.routers.research_campaign_runtime import CampaignRuntimeResult
from qwenpaw.app.routers.research_campaign_service import (
    CampaignApiState,
    CampaignCommandRequest,
    StartIssueCampaignRequest,
    install_research_campaign_service,
)


class Episode:
    episode_id = "campaign-1-issue-12"

    @staticmethod
    def digest():
        return "d" * 64


class Status:
    value = "delivered"


async def _fake_runtime(
    research_module,
    campaign_id,
    body,
    *,
    owner_agent_id,
    owner_session_id,
    emit,
):
    assert campaign_id
    assert body.repository == "owner/repo"
    assert owner_agent_id == "owner-agent"
    assert owner_session_id == "session-1"
    emit("implementing", "host-verified implementation")
    await asyncio.sleep(0)
    outcome = SimpleNamespace(
        status=Status(),
        reason="verified_committed_and_delivered",
        episode=Episode(),
        attempts=(),
        artifacts=(),
        evidence=None,
        delivery=None,
        delivery_receipt=None,
    )
    return CampaignRuntimeResult(
        outcome=outcome,
        worktree="/tmp/worktree",
        branch="autoresearch/issue-12",
        base_branch="main",
    )


def _module(tmp_path: Path) -> ModuleType:
    module = ModuleType("test_research_campaign_module")
    module.router = APIRouter(prefix="/research")
    module.WORKING_DIR = tmp_path
    module.UNSAFE_RESEARCH_OPT_IN = "QWENPAW_UNSAFE_RESEARCH"
    module.unsafe_research_enabled = lambda: True
    module._dialog_runtime_context = {}
    module._request_owner_identity = lambda request, session_id=None: (
        "owner-agent",
        "owner-user",
        session_id or "session-1",
    )
    module.__file__ = str(tmp_path / "src" / "qwenpaw" / "app" / "routers" / "research.py")
    return module


def _request() -> Request:
    app = SimpleNamespace(
        state=SimpleNamespace(
            multi_agent_manager=None,
            app_services=None,
        )
    )
    return Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/api/research/campaigns",
            "headers": [],
            "query_string": b"",
            "server": ("test", 80),
            "client": ("test", 1234),
            "scheme": "http",
            "app": app,
        }
    )


def _body() -> StartIssueCampaignRequest:
    return StartIssueCampaignRequest(
        repository="owner/repo",
        issue_number=12,
        acceptance_criteria=["cache expiry is fixed"],
        modifiable_files=[
            "src/qwenpaw/memory/cache.py",
            "tests/unit/test_cache.py",
        ],
        commands=[
            CampaignCommandRequest(
                command_id="unit",
                stage="unit",
                argv=["pytest", "-q", "tests/unit/test_cache.py"],
            )
        ],
        implementer_agent_id="implementer",
        reviewer_agent_id="reviewer",
        session_id="session-1",
    )


def _endpoint(module: ModuleType, path: str, method: str):
    for route in module.router.routes:
        if route.path == path and method in route.methods:
            return route.endpoint
    raise AssertionError(f"missing route {method} {path}")


@pytest.mark.asyncio
async def test_campaign_router_runs_background_campaign_and_persists_state(tmp_path):
    module = _module(tmp_path)
    install_research_campaign_service(module)
    module._execute_issue_campaign = _fake_runtime

    start = _endpoint(module, "/research/campaigns", "POST")
    get_status = _endpoint(
        module,
        "/research/campaigns/{campaign_id}",
        "GET",
    )
    result = await start(_body(), _request())
    campaign_id = result["campaign_id"]

    await module._campaign_tasks[campaign_id]
    status = await get_status(campaign_id, _request())

    assert status["status"] == "delivered"
    assert status["worktree_path"] == "/tmp/worktree"
    assert status["branch"] == "autoresearch/issue-12"
    assert status["outcome"]["reason"] == "verified_committed_and_delivered"
    assert status["events"][0]["phase"] == "implementing"
    snapshot = module._campaign_snapshot_root / f"{campaign_id}.json"
    assert json.loads(snapshot.read_text(encoding="utf-8"))["status"] == "delivered"


def test_campaign_router_registers_create_get_stream_and_cancel(tmp_path):
    module = _module(tmp_path)
    install_research_campaign_service(module)

    routes = {(route.path, tuple(sorted(route.methods))) for route in module.router.routes}
    assert ("/research/campaigns", ("POST",)) in routes
    assert ("/research/campaigns/{campaign_id}", ("GET",)) in routes
    assert (
        "/research/campaigns/{campaign_id}/stream",
        ("GET",),
    ) in routes
    assert (
        "/research/campaigns/{campaign_id}/cancel",
        ("POST",),
    ) in routes


def test_campaign_restore_marks_interrupted_run_needs_revision(tmp_path):
    snapshot_root = tmp_path / ".qwenpaw" / "research-campaigns"
    snapshot_root.mkdir(parents=True)
    state = CampaignApiState(
        campaign_id="campaign-1",
        status="validating",
        repository="owner/repo",
        issue_number=12,
        task_type="bug_fix",
        owner_agent_id="owner-agent",
        owner_user_id="owner-user",
        owner_session_id="session-1",
        implementer_agent_id="implementer",
        reviewer_agent_id="reviewer",
        acceptance_criteria=["fixed"],
        modifiable_files=["src/cache.py"],
        frozen_files=[],
        created_at="2026-07-29T00:00:00+00:00",
        updated_at="2026-07-29T00:01:00+00:00",
    )
    (snapshot_root / "campaign-1.json").write_text(
        json.dumps(state.__dict__),
        encoding="utf-8",
    )

    module = _module(tmp_path)
    install_research_campaign_service(module)

    restored = module._campaign_runs["campaign-1"]
    assert restored.status == "needs_revision"
    assert restored.error == "runtime_interrupted_requires_review"
    assert restored.events[-1]["phase"] == "needs_revision"
