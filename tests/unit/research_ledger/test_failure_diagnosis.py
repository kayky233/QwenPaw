from qwenpaw.research_ledger.diagnosis import DiagnosisBuilder, RepairBudget, RepairPolicy
from qwenpaw.research_ledger.failure_signature import (
    FailureCategory,
    build_failure_signature,
)


def test_pytest_failure_signature_extracts_test_and_exception():
    signature = build_failure_signature(
        "FAILED tests/test_cache.py::test_cache - AssertionError: expected hit"
    )
    assert signature.category == FailureCategory.IMPLEMENTATION
    assert signature.test_id == "tests/test_cache.py::test_cache"
    assert signature.exception_type == "AssertionError"
    assert len(signature.fingerprint) == 64


def test_scope_failure_requires_human():
    signature = build_failure_signature("changed path is outside approved scope")
    diagnosis = DiagnosisBuilder().build(signature)
    assert diagnosis.requires_human
    assert not RepairPolicy().may_repair(diagnosis, 0, 0)


def test_implementation_repairs_are_bounded():
    diagnosis = DiagnosisBuilder().build(build_failure_signature("AssertionError"))
    policy = RepairPolicy(RepairBudget(max_repairs=2))
    assert policy.may_repair(diagnosis, 1, 0)
    assert not policy.may_repair(diagnosis, 2, 0)
