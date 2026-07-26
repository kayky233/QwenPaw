from __future__ import annotations

import time
from dataclasses import replace
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from qwenpaw.app.routers import research as research_router
from qwenpaw.research import ResearchOutcome


def _make_task(root: Path, name: str = "demo") -> Path:
    task = root / name
    task.mkdir(parents=True)
    (task / "program.md").write_text(
        "# Demo task\n\nImprove the solution.",
        encoding="utf-8",
    )
    (task / "solution.sh").write_text("printf ok\n", encoding="utf-8")
    (task / "judge.py").write_text(
        """\
import json
print(json.dumps({"passed": True, "score": 1, "metrics": {"cases": 1}}))
""",
        encoding="utf-8",
    )
    return task


def _client(monkeypatch, root: Path) -> TestClient:
    monkeypatch.setattr(research_router, "get_research_root", lambda: root)
    research_router._reset_runs_for_tests()
    app = FastAPI()
    app.include_router(research_router.router)
    return TestClient(app)


def _make_run_state(
    run_id: str,
    *,
    status: str = "running",
    events=(),
):
    return research_router.ResearchRunState(
        id=run_id,
        task_id="demo",
        agent_id="default",
        owner_agent_id="default",
        owner_user_id=None,
        owner_session_id=None,
        status=status,
        rounds=3,
        completed_rounds=0,
        outcomes=(),
        created_at="2026-01-01T00:00:00+00:00",
        events=events,
        current_round=None,
        phase="running",
        updated_at="2026-01-01T00:00:00+00:00",
    )


def test_list_and_get_research_task(monkeypatch, tmp_path: Path) -> None:
    _make_task(tmp_path)
    monkeypatch.setenv("QWENPAW_UNSAFE_RESEARCH", "1")

    with _client(monkeypatch, tmp_path) as client:
        listing = client.get("/research/tasks")
        detail = client.get("/research/tasks/demo")

    assert listing.status_code == 200
    assert listing.json()[0]["title"] == "Demo task"
    assert detail.status_code == 200
    assert detail.json()["evaluation"]["passed"] is True
    assert detail.json()["solution"] == "printf ok\n"


def test_get_task_does_not_execute_judge_without_opt_in(
    monkeypatch,
    tmp_path: Path,
) -> None:
    _make_task(tmp_path)
    monkeypatch.delenv("QWENPAW_UNSAFE_RESEARCH", raising=False)

    def unexpected_evaluation(*_args, **_kwargs):
        raise AssertionError("judge must not run")

    monkeypatch.setattr(research_router, "evaluate_candidate", unexpected_evaluation)

    with _client(monkeypatch, tmp_path) as client:
        response = client.get("/research/tasks/demo")

    assert response.status_code == 200
    assert response.json()["evaluation"]["score"] is None
    assert "QWENPAW_UNSAFE_RESEARCH=1" in response.json()["evaluation"]["error"]


def test_research_task_id_cannot_escape_root(monkeypatch, tmp_path: Path) -> None:
    with _client(monkeypatch, tmp_path) as client:
        response = client.get("/research/tasks/..%2Foutside")

    assert response.status_code == 404


def test_start_run_and_poll_round_progress(monkeypatch, tmp_path: Path) -> None:
    _make_task(tmp_path)
    monkeypatch.setenv("QWENPAW_UNSAFE_RESEARCH", "1")
    outcome = ResearchOutcome(1, "kept", True, 1.0, 2.0, 1.0, {"cases": 1})

    async def fake_run(*_args, **kwargs):
        kwargs["on_progress"]((1, "proposing", "AI 分析代码中"))
        kwargs["on_outcome"](outcome)
        return (outcome,)

    run_mock = AsyncMock(side_effect=fake_run)
    monkeypatch.setattr(research_router, "_run_with_qwenpaw", run_mock)

    with _client(monkeypatch, tmp_path) as client:
        started = client.post(
            "/research/tasks/demo/runs",
            json={"rounds": 1, "agent_id": "default"},
        )
        assert started.status_code == 202
        run_id = started.json()["id"]

        payload = started.json()
        for _ in range(50):
            payload = client.get(f"/research/runs/{run_id}").json()
            if payload["status"] == "completed":
                break
            time.sleep(0.01)
        task_payload = client.get("/research/tasks/demo").json()

    assert payload["status"] == "completed"
    assert payload["completed_rounds"] == 1
    assert payload["outcomes"][0]["status"] == "kept"
    assert [event["phase"] for event in payload["events"]] == [
        "queued",
        "starting",
        "proposing",
        "kept",
        "completed",
    ]
    assert task_payload["active_run_id"] is None
    assert task_payload["latest_run_id"] == run_id
    assert run_mock.await_args.args[0].name == "demo"


def test_rejects_a_second_active_run(monkeypatch, tmp_path: Path) -> None:
    _make_task(tmp_path)
    monkeypatch.setenv("QWENPAW_UNSAFE_RESEARCH", "1")

    async def slow_run(*_args, **_kwargs):
        await research_router.asyncio.sleep(10)

    monkeypatch.setattr(research_router, "_run_with_qwenpaw", slow_run)

    with _client(monkeypatch, tmp_path) as client:
        first = client.post("/research/tasks/demo/runs", json={"rounds": 1})
        second = client.post("/research/tasks/demo/runs", json={"rounds": 1})

    assert first.status_code == 202
    assert second.status_code == 409


def test_cancel_active_research_run(monkeypatch, tmp_path: Path) -> None:
    _make_task(tmp_path)
    monkeypatch.setenv("QWENPAW_UNSAFE_RESEARCH", "1")

    async def slow_run(*_args, **_kwargs):
        await research_router.asyncio.sleep(10)

    monkeypatch.setattr(research_router, "_run_with_qwenpaw", slow_run)

    with _client(monkeypatch, tmp_path) as client:
        started = client.post("/research/tasks/demo/runs", json={"rounds": 1})
        run_id = started.json()["id"]
        cancelled = client.post(f"/research/runs/{run_id}/cancel")

    assert cancelled.status_code == 202
    assert cancelled.json()["status"] == "cancelled"
    assert cancelled.json()["events"][-1]["phase"] == "cancelled"


def test_research_run_is_private_to_its_owner(monkeypatch, tmp_path: Path) -> None:
    _make_task(tmp_path)
    monkeypatch.setenv("QWENPAW_UNSAFE_RESEARCH", "1")
    owner = "owner-agent"
    monkeypatch.setattr(research_router, "get_current_agent_id", lambda: owner)
    monkeypatch.setattr(research_router, "get_current_user_id", lambda: "user-1")
    monkeypatch.setattr(
        research_router,
        "get_current_session_id",
        lambda: "session-1",
    )

    async def slow_run(*_args, **_kwargs):
        await research_router.asyncio.sleep(10)

    monkeypatch.setattr(research_router, "_run_with_qwenpaw", slow_run)

    with _client(monkeypatch, tmp_path) as client:
        denied = client.post(
            "/research/tasks/demo/runs",
            json={"rounds": 1, "agent_id": "worker-agent"},
        )
        assert denied.status_code == 403

        started = client.post(
            "/research/tasks/demo/runs",
            json={"rounds": 1, "agent_id": owner},
        )
        assert started.json()["agent_id"] == owner
        assert not any(key.startswith("owner_") for key in started.json())
        run_id = started.json()["id"]
        monkeypatch.setattr(
            research_router,
            "get_current_agent_id",
            lambda: "other-agent",
        )

        assert client.get(f"/research/runs/{run_id}").status_code == 404
        assert client.post(f"/research/runs/{run_id}/cancel").status_code == 404
        other_summary = client.get("/research/tasks/demo").json()
        assert other_summary["active_run_id"] is None
        assert other_summary["latest_run_id"] is None

        monkeypatch.setattr(research_router, "get_current_agent_id", lambda: owner)
        monkeypatch.setattr(
            research_router,
            "get_current_session_id",
            lambda: "session-2",
        )
        assert client.get(f"/research/runs/{run_id}").status_code == 404

        monkeypatch.setattr(
            research_router,
            "get_current_session_id",
            lambda: "session-1",
        )
        owner_summary = client.get("/research/tasks/demo").json()
        assert owner_summary["active_run_id"] == run_id
        assert client.post(f"/research/runs/{run_id}/cancel").status_code == 202


def test_research_execution_is_disabled_without_explicit_opt_in(
    monkeypatch,
    tmp_path: Path,
) -> None:
    _make_task(tmp_path)
    monkeypatch.delenv("QWENPAW_UNSAFE_RESEARCH", raising=False)

    with _client(monkeypatch, tmp_path) as client:
        response = client.post("/research/tasks/demo/runs", json={"rounds": 1})

    assert response.status_code == 503
    assert "QWENPAW_UNSAFE_RESEARCH=1" in response.json()["detail"]


def test_phase_error_does_not_submit_or_complete(
    monkeypatch,
    tmp_path: Path,
) -> None:
    _make_task(tmp_path)
    monkeypatch.setenv("QWENPAW_UNSAFE_RESEARCH", "1")
    outcome = ResearchOutcome(
        1, "phase_error", False, 1.0, None, 0.0, {}, "program phase exhausted",
    )

    async def failed_run(*_args, **kwargs):
        kwargs["on_outcome"](outcome)
        return (outcome,)

    create_pr = AsyncMock()
    monkeypatch.setattr(
        research_router,
        "_run_with_qwenpaw",
        AsyncMock(side_effect=failed_run),
    )
    monkeypatch.setattr(research_router, "_try_create_pr", create_pr)

    with _client(monkeypatch, tmp_path) as client:
        started = client.post("/research/tasks/demo/runs", json={"rounds": 3})
        run_id = started.json()["id"]
        payload = started.json()
        for _ in range(50):
            payload = client.get(f"/research/runs/{run_id}").json()
            if payload["status"] in {"failed", "completed"}:
                break
            time.sleep(0.01)

    assert payload["status"] == "failed"
    assert payload["error"] == "program phase exhausted"
    create_pr.assert_not_awaited()


def test_cancelled_outcome_does_not_submit_or_complete(
    monkeypatch,
    tmp_path: Path,
) -> None:
    _make_task(tmp_path)
    monkeypatch.setenv("QWENPAW_UNSAFE_RESEARCH", "1")
    outcome = ResearchOutcome(
        1, "cancelled", False, 1.0, None, 0.0, {}, "run cancelled",
    )

    async def cancelled_run(*_args, **kwargs):
        kwargs["on_outcome"](outcome)
        return (outcome,)

    create_pr = AsyncMock()
    monkeypatch.setattr(
        research_router,
        "_run_with_qwenpaw",
        AsyncMock(side_effect=cancelled_run),
    )
    monkeypatch.setattr(research_router, "_try_create_pr", create_pr)

    with _client(monkeypatch, tmp_path) as client:
        started = client.post("/research/tasks/demo/runs", json={"rounds": 3})
        run_id = started.json()["id"]
        payload = started.json()
        for _ in range(50):
            payload = client.get(f"/research/runs/{run_id}").json()
            if payload["status"] in {"cancelled", "completed"}:
                break
            time.sleep(0.01)

    assert payload["status"] == "cancelled"
    create_pr.assert_not_awaited()


def test_pr_finalization_precedes_completed_event(
    monkeypatch,
    tmp_path: Path,
) -> None:
    _make_task(tmp_path)
    monkeypatch.setenv("QWENPAW_UNSAFE_RESEARCH", "1")
    outcome = ResearchOutcome(1, "rejected", True, 1.0, 1.0, 0.0, {})

    async def successful_run(*_args, **kwargs):
        kwargs["on_outcome"](outcome)
        return (outcome,)

    async def create_pr(run_id: str, _task_id: str) -> None:
        research_router._report_event(
            run_id,
            "pr_created",
            detail="https://example/pr/1",
        )

    monkeypatch.setattr(
        research_router,
        "_run_with_qwenpaw",
        AsyncMock(side_effect=successful_run),
    )
    monkeypatch.setattr(research_router, "_try_create_pr", create_pr)

    with _client(monkeypatch, tmp_path) as client:
        started = client.post("/research/tasks/demo/runs", json={"rounds": 1})
        run_id = started.json()["id"]
        payload = started.json()
        for _ in range(50):
            payload = client.get(f"/research/runs/{run_id}").json()
            if payload["status"] == "completed":
                break
            time.sleep(0.01)

    phases = [event["phase"] for event in payload["events"]]
    assert phases.index("pr_created") < phases.index("completed")


def test_live_event_broadcast_contains_sequence_and_timestamp(monkeypatch) -> None:
    run_id = "live-sequence"
    research_router._runs[run_id] = _make_run_state(run_id)
    captured: list[dict] = []
    monkeypatch.setattr(
        research_router,
        "_sse_broadcast",
        lambda _run_id, event: captured.append(event),
    )

    research_router._report_event_sse(run_id, "planning", 1, "ready")

    assert captured == [
        {
            "type": "event",
            "phase": "planning",
            "round": 1,
            "detail": "ready",
            "timestamp": research_router._runs[run_id].events[-1].timestamp,
            "sequence": 0,
        }
    ]


def test_terminal_stream_closes_immediately(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(research_router, "get_current_agent_id", lambda: "default")
    monkeypatch.setattr(research_router, "get_current_user_id", lambda: None)
    monkeypatch.setattr(research_router, "get_current_session_id", lambda: None)

    with _client(monkeypatch, tmp_path) as client:
        run_id = "terminal-stream"
        research_router._runs[run_id] = _make_run_state(
            run_id,
            status="completed",
        )
        response = client.get(f"/research/runs/{run_id}/stream")

    assert response.status_code == 200
    assert '"type": "run.completed"' in response.text
    assert '"type": "done"' in response.text


@pytest.mark.asyncio
async def test_stream_refreshes_snapshot_after_subscriber_registration(
    monkeypatch,
) -> None:
    first = _make_run_state("fresh-stream")
    event = research_router.ResearchRunEvent(
        "planning",
        "2026-01-01T00:00:01+00:00",
        1,
        "fresh",
        0,
    )
    fresh = replace(first, events=(event,))
    snapshots = iter((first, fresh))
    monkeypatch.setattr(research_router, "_owned_run", lambda _run_id: next(snapshots))

    class RequestStub:
        async def is_disconnected(self) -> bool:
            return False

    response = await research_router.stream_run(
        "fresh-stream",
        RequestStub(),
    )
    body = response.body_iterator
    first_chunk = await body.__anext__()
    await body.aclose()

    assert '"sequence": 0' in first_chunk
    assert '"timestamp": "2026-01-01T00:00:01+00:00"' in first_chunk


@pytest.mark.asyncio
async def test_ledger_restores_completed_run(monkeypatch, tmp_path: Path) -> None:
    task_root = _make_task(tmp_path)
    database_url = f"sqlite+aiosqlite:///{tmp_path / 'research.db'}"
    run_id = "persisted-run"
    queued = research_router.ResearchRunEvent(
        "queued",
        "2026-01-01T00:00:00+00:00",
        sequence=0,
    )
    state = replace(
        _make_run_state(run_id, events=(queued,)),
        task_id="demo",
        status="queued",
        phase="queued",
        research_brief={
            "goal": "remove duplicate writes",
            "candidate_directions": [],
        },
    )

    await research_router.initialize_research_ledger(database_url)
    try:
        research_router._runs[run_id] = state
        await research_router._persist_new_run(state, task_root)
        research_router._report_event(run_id, "planning", 1, "ready")
        research_router._report_outcome(
            run_id,
            ResearchOutcome(1, "kept", True, 1.0, 2.0, 1.0, {"cases": 1}),
        )
        research_router._finish_run(run_id, "completed")
        await research_router._flush_ledger(run_id)
        await research_router.close_research_ledger()

        research_router._runs.clear()
        await research_router.initialize_research_ledger(database_url)

        restored = research_router._runs[run_id]
        assert restored.status == "completed"
        assert restored.owner_agent_id == "default"
        assert [event.sequence for event in restored.events] == [0, 1, 2, 3]
        assert restored.outcomes[0].status == "kept"
        assert restored.outcomes[0].metrics == {"cases": 1}
        assert restored.research_brief == {
            "goal": "remove duplicate writes",
            "candidate_directions": [],
        }
    finally:
        await research_router.close_research_ledger()
