import json

from qwenpaw.research_ledger.delivery_lifecycle import CICheckStatus
from qwenpaw.research_ledger.github_delivery_monitor import (
    parse_github_delivery_snapshot,
)


def test_parse_successful_ci_and_approved_review():
    snapshot = parse_github_delivery_snapshot(
        json.dumps(
            {
                "headRefOid": "abc",
                "reviewDecision": "APPROVED",
                "statusCheckRollup": [
                    {
                        "name": "tests",
                        "status": "COMPLETED",
                        "conclusion": "SUCCESS",
                    }
                ],
            }
        )
    )
    assert snapshot.ci_report.status == CICheckStatus.PASSED
    assert snapshot.review_threads == ()


def test_changes_requested_creates_unresolved_review_blocker():
    snapshot = parse_github_delivery_snapshot(
        json.dumps(
            {
                "headRefOid": "abc",
                "reviewDecision": "CHANGES_REQUESTED",
                "statusCheckRollup": [
                    {
                        "name": "tests",
                        "status": "COMPLETED",
                        "conclusion": "SUCCESS",
                    }
                ],
            }
        )
    )
    assert len(snapshot.review_threads) == 1
    assert not snapshot.review_threads[0].resolved
