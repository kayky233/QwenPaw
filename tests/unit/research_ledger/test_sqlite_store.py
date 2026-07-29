import sqlite3

import pytest

from qwenpaw.research_ledger.contracts import (
    ResearchArtifactContract,
    ResearchArtifactType,
    ResearchStepContract,
    ResearchStepStatus,
    ResearchStepType,
)
from qwenpaw.research_ledger.sqlite_store import SQLiteResearchLedgerStore
from qwenpaw.research_ledger.step_events import ResearchStepEvent


def _step(status: ResearchStepStatus = ResearchStepStatus.PENDING):
    return ResearchStepContract(
        step_id="s1",
        run_id="r1",
        step_type=ResearchStepType.PLAN,
        index=0,
        executor="planner",
        required_artifacts=(ResearchArtifactType.PLAN,),
        input_artifacts=("issue",),
        validation_rules=("scope-approved",),
        status=status,
    )


def test_sqlite_store_round_trips_step_contract():
    store = SQLiteResearchLedgerStore(sqlite3.connect(":memory:"))
    store.save_step(_step())

    restored = store.get_step("s1")

    assert restored == _step()
    assert store.list_steps("r1") == [_step()]
    assert store.count_steps("r1") == 1


def test_sqlite_store_uses_non_conflicting_table_names():
    connection = sqlite3.connect(":memory:")
    connection.execute(
        "CREATE TABLE research_artifacts (id INTEGER PRIMARY KEY, path TEXT)"
    )

    SQLiteResearchLedgerStore(connection)

    names = {
        row[0]
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    }
    assert "research_artifacts" in names
    assert "research_execution_artifacts" in names


def test_sqlite_store_round_trips_artifacts_and_events():
    store = SQLiteResearchLedgerStore(sqlite3.connect(":memory:"))
    artifact = ResearchArtifactContract(
        artifact_id="a1",
        run_id="r1",
        step_id="s1",
        artifact_type=ResearchArtifactType.PLAN,
        path="program.md",
        content_hash="a" * 64,
        verified=True,
        metadata={"revision": 2},
    )
    event = ResearchStepEvent.create(
        "artifact_verified",
        "r1",
        "s1",
        {"artifact_id": "a1"},
    )

    store.save_artifact(artifact)
    store.append_event(event)

    assert store.get_artifact("a1") == artifact
    assert store.list_artifacts(step_id="s1") == [artifact]
    assert store.list_artifacts(run_id="r1") == [artifact]
    assert store.list_events("r1") == [event]


@pytest.mark.parametrize("kwargs", [{}, {"run_id": "r1", "step_id": "s1"}])
def test_list_artifacts_requires_exactly_one_filter(kwargs):
    store = SQLiteResearchLedgerStore(sqlite3.connect(":memory:"))

    with pytest.raises(ValueError, match="exactly one"):
        store.list_artifacts(**kwargs)


def test_recover_interrupted_steps_blocks_running_steps_and_records_event():
    store = SQLiteResearchLedgerStore(sqlite3.connect(":memory:"))
    store.save_step(_step(ResearchStepStatus.RUNNING))

    recovered = store.recover_interrupted_steps("r1")

    assert recovered[0].status == ResearchStepStatus.BLOCKED
    assert store.get_step("s1").status == ResearchStepStatus.BLOCKED
    event = store.list_events("r1")[0]
    assert event.event_type == "step_recovered"
    assert event.payload["previous_status"] == "running"
