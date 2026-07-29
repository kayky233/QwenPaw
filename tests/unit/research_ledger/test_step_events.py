from qwenpaw.research_ledger.event_store import ResearchEventStore
from qwenpaw.research_ledger.step_events import ResearchStepEvent


def test_event_store_filters_run():
    store = ResearchEventStore()
    store.append(ResearchStepEvent.create("step_started", "run1", "s1"))
    store.append(ResearchStepEvent.create("step_started", "run2", "s2"))
    assert len(store.list_for_run("run1")) == 1
    assert store.latest("run1").step_id == "s1"
