from __future__ import annotations

import time
from pathlib import Path
from unittest.mock import AsyncMock

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
