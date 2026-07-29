import pytest

from qwenpaw.research_ledger.contracts import ResearchStepType
from qwenpaw.research_ledger.step_runtime_adapter import resolve_step_type


def test_program_maps_to_implementation():
    assert resolve_step_type("PROGRAM") == ResearchStepType.IMPLEMENT


def test_judge_maps_to_test():
    assert resolve_step_type("JUDGE") == ResearchStepType.TEST


def test_unknown_phase_rejected():
    with pytest.raises(ValueError):
        resolve_step_type("UNKNOWN")
