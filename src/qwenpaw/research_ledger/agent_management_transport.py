"""Task Envelope transport over QwenPaw's existing agent-management API."""

from __future__ import annotations

import asyncio
import json
from dataclasses import asdict, dataclass
from hashlib import sha256
from typing import Any, Callable

from ..agents.tools.agent_management import (
    build_agent_chat_request,
    collect_final_agent_chat_response,
    extract_agent_text_content,
)
from .collaboration_contracts import TaskEnvelope
from .contracts import ResearchArtifactContract, ResearchArtifactType


@dataclass(frozen=True)
class AgentTransportResult:
    agent_id: str
    session_id: str
    text: str
    raw_response: dict[str, Any]


class AgentManagementTaskTransport:
    def __init__(
        self,
        *,
        base_url: str | None = None,
        timeout: int = 900,
        response_collector: Callable[..., dict[str, Any] | None] = (
            collect_final_agent_chat_response
        ),
    ) -> None:
        self.base_url = base_url
        self.timeout = timeout
        self.response_collector = response_collector

    @staticmethod
    def build_prompt(envelope: TaskEnvelope) -> str:
        payload = asdict(envelope)
        payload["role"] = envelope.role.value
        payload["expected_artifact_types"] = [
            item.value for item in envelope.expected_artifact_types
        ]
        if envelope.deadline is not None:
            payload["deadline"] = envelope.deadline.isoformat()
        return (
            "Execute the following AutoResearch Task Envelope. Respect the "
            "allowed paths and read_only flag. Do not commit, push, or create "
            "a pull request. Return a concise summary of actions, files, test "
            "evidence, and blockers.\n\n"
            + json.dumps(payload, ensure_ascii=False, indent=2)
        )

    async def send(
        self,
        agent_id: str,
        envelope: TaskEnvelope,
        *,
        from_agent: str = "autoresearch-supervisor",
        root_session_id: str | None = None,
    ) -> AgentTransportResult:
        session_id, request_payload, _ = build_agent_chat_request(
            to_agent=agent_id,
            text=self.build_prompt(envelope),
            from_agent=from_agent,
            root_session_id=root_session_id,
        )
        response = await asyncio.to_thread(
            self.response_collector,
            self.base_url,
            request_payload,
            agent_id,
            self.timeout,
        )
        if response is None:
            raise RuntimeError(f"agent {agent_id} returned no final response")
        text = extract_agent_text_content(response)
        if not text:
            raise RuntimeError(f"agent {agent_id} returned no text evidence")
        return AgentTransportResult(
            agent_id=agent_id,
            session_id=session_id,
            text=text,
            raw_response=response,
        )

    @staticmethod
    def result_artifact(
        envelope: TaskEnvelope,
        result: AgentTransportResult,
    ) -> ResearchArtifactContract:
        content = result.text
        return ResearchArtifactContract(
            artifact_id=f"{envelope.step_id}-{result.agent_id}-response",
            run_id=envelope.run_id,
            step_id=envelope.step_id,
            artifact_type=ResearchArtifactType.LOG,
            path=f"agent://{result.agent_id}/{result.session_id}",
            content_hash=sha256(content.encode("utf-8")).hexdigest(),
            verified=bool(content),
            metadata={
                "agent_id": result.agent_id,
                "session_id": result.session_id,
                "role": envelope.role.value,
            },
        )
