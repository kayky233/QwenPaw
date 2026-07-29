from qwenpaw.research_ledger.best_registry import BestExperiment, BestExperimentRegistry
from qwenpaw.research_ledger.patch_manager import PatchManager


def test_best_registry_keeps_improved_result():
    registry = BestExperimentRegistry()
    assert registry.update(BestExperiment("e1", "latency", 100, "a"))
    assert registry.update(BestExperiment("e2", "latency", 80, "b"))
    assert registry.get("latency").revision == "b"


def test_patch_checkpoint():
    checkpoint = PatchManager().create_checkpoint("e1", "abc")
    assert checkpoint.base_revision == "abc"
