from __future__ import annotations

from qwenpaw.research_ledger.agent_management_transport import (
    AgentManagementTaskTransport,
    AgentTransportResult,
)
from qwenpaw.research_ledger.collaboration_contracts import (
    ResearchAgentRole,
    TaskEnvelope,
)
from qwenpaw.research_ledger.contracts import ResearchArtifactType


def _envelope(*, role: ResearchAgentRole, objective: str) -> TaskEnvelope:
    return TaskEnvelope(
        task_id="task-1",
        run_id="run-1",
        step_id="run-1-step",
        role=role,
        objective=objective,
        expected_artifact_types=(ResearchArtifactType.REPORT,),
        allowed_paths=(),
        read_only=role == ResearchAgentRole.REVIEWER,
    )


def test_transport_preserves_reviewer_json_response_contract() -> None:
    prompt = AgentManagementTaskTransport.build_prompt(
        _envelope(
            role=ResearchAgentRole.REVIEWER,
            objective="Return exactly one JSON object and no prose.",
        )
    )

    assert "return only that requested structure" in prompt
    assert "Return exactly one JSON object and no prose" in prompt
    assert '"read_only": true' in prompt


def test_transport_keeps_general_summary_fallback_for_implementer() -> None:
    prompt = AgentManagementTaskTransport.build_prompt(
        _envelope(
            role=ResearchAgentRole.IMPLEMENTER,
            objective="Implement the approved change.",
        )
    )

    assert "Otherwise return a concise summary" in prompt
    assert "Do not commit, push, or create a pull request" in prompt


def test_agent_response_can_only_be_recorded_as_log_evidence() -> None:
    envelope = _envelope(
        role=ResearchAgentRole.IMPLEMENTER,
        objective="Implement the approved change.",
    )
    result = AgentTransportResult(
        agent_id="coder",
        session_id="session-1",
        text="I changed src/fix.py and tests passed.",
        raw_response={},
    )

    artifact = AgentManagementTaskTransport.result_artifact(envelope, result)

    assert artifact.artifact_type == ResearchArtifactType.LOG
    assert artifact.verified is True
    assert artifact.metadata["agent_id"] == "coder"
