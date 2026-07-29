import sqlite3

from qwenpaw.research_ledger.contracts import (
    ResearchStepContract,
    ResearchStepType,
)
from qwenpaw.research_ledger.sqlite_store import SQLiteResearchLedgerStore


def test_sqlite_store_persists_steps():
    store = SQLiteResearchLedgerStore(sqlite3.connect(":memory:"))
    store.save_step(
        ResearchStepContract(
            step_id="s1",
            run_id="r1",
            step_type=ResearchStepType.PLAN,
            index=0,
            executor="planner",
        )
    )
    assert store.count_steps("r1") == 1
