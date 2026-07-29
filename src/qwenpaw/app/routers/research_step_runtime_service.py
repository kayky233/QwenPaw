"""Connect the existing dialog runtime to durable Step/Artifact contracts.

The monolithic router remains the execution authority for compatibility. This
service observes its existing events and projects them into the new ledger so
status APIs, snapshots, and later autonomous orchestration share one evidence
model.
"""

from __future__ import annotations

import json
from dataclasses import asdict
from hashlib import sha256
from types import ModuleType
from typing import Any

from ...research_ledger.contracts import (
    ResearchArtifactContract,
    ResearchArtifactType,
    ResearchStepContract,
    ResearchStepStatus,
    ResearchStepType,
    required_artifacts_for_step,
)
from ...research_ledger.event_store import ResearchEventStore
from ...research_ledger.step_events import ResearchStepEvent
from ...research_ledger.step_repository import (
    ResearchArtifactRepository,
    ResearchStepRepository,
)

_STEP_ORDER = {
    ResearchStepType.DISCOVERY: 0,
    ResearchStepType.PLAN: 1,
    ResearchStepType.IMPLEMENT: 2,
    ResearchStepType.TEST: 3,
    ResearchStepType.REVIEW: 4,
    ResearchStepType.DELIVERY: 5,
}

_PHASE_TO_STEP = {
    "accepted": ResearchStepType.DISCOVERY,
    "planning": ResearchStepType.DISCOVERY,
    "discovering": ResearchStepType.DISCOVERY,
    "brief_ready": ResearchStepType.DISCOVERY,
    "plan_ready": ResearchStepType.PLAN,
    "awaiting_approval": ResearchStepType.PLAN,
    "plan_updated": ResearchStepType.PLAN,
    "revision_proposed": ResearchStepType.PLAN,
    "approved": ResearchStepType.PLAN,
    "preparing_worktree": ResearchStepType.IMPLEMENT,
    "implementing": ResearchStepType.IMPLEMENT,
    "reproducing": ResearchStepType.TEST,
    "validating": ResearchStepType.TEST,
    "report_ready": ResearchStepType.REVIEW,
    "validation_needs_revision": ResearchStepType.REVIEW,
    "pushing": ResearchStepType.DELIVERY,
    "creating_pr": ResearchStepType.DELIVERY,
    "pr_created": ResearchStepType.DELIVERY,
    "completed": ResearchStepType.DELIVERY,
}

_TERMINAL_STEP_STATUSES = {
    ResearchStepStatus.COMPLETED,
    ResearchStepStatus.FAILED,
    ResearchStepStatus.BLOCKED,
    ResearchStepStatus.CANCELLED,
}


class DialogExecutionLedger:
    """Per-dialog projection of execution steps, artifacts, and events."""

    def __init__(self, plan_id: str) -> None:
        self.plan_id = plan_id
        self.steps = ResearchStepRepository()
        self.artifacts = ResearchArtifactRepository()
        self.events = ResearchEventStore()

    def _step_id(self, step_type: ResearchStepType) -> str:
        return f"{self.plan_id}-{step_type.value}"

    def _ensure_step(
        self,
        step_type: ResearchStepType,
        dialog: Any,
    ) -> ResearchStepContract:
        step_id = self._step_id(step_type)
        existing = self.steps.get(step_id)
        if existing is not None:
            return existing
        required = required_artifacts_for_step(step_type)
        if step_type == ResearchStepType.DELIVERY and not getattr(
            dialog,
            "auto_pr",
            True,
        ):
            required = tuple(
                item
                for item in required
                if item != ResearchArtifactType.PULL_REQUEST
            )
        return self.steps.create(
            ResearchStepContract(
                step_id=step_id,
                run_id=self.plan_id,
                step_type=step_type,
                index=_STEP_ORDER[step_type],
                executor=step_type.value,
                required_artifacts=required,
            )
        )

    def _complete_prior_steps(self, index: int) -> None:
        for step in self.steps.list_for_run(self.plan_id):
            if (
                step.index < index
                and step.status == ResearchStepStatus.RUNNING
            ):
                self.steps.update_status(
                    step.step_id,
                    ResearchStepStatus.COMPLETED,
                )

    def _add_artifact(
        self,
        step_type: ResearchStepType,
        artifact_type: ResearchArtifactType,
        content: str,
        *,
        path: str,
        verified: bool,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        if not content:
            return
        step_id = self._step_id(step_type)
        artifact_id = f"{step_id}-{artifact_type.value}"
        self.artifacts.add(
            ResearchArtifactContract(
                artifact_id=artifact_id,
                run_id=self.plan_id,
                step_id=step_id,
                artifact_type=artifact_type,
                path=path,
                content_hash=sha256(content.encode("utf-8")).hexdigest(),
                verified=verified,
                metadata=metadata or {},
            )
        )

    def _collect_dialog_artifacts(self, phase: str, dialog: Any) -> None:
        if phase in {"plan_ready", "awaiting_approval", "approved"}:
            plan = str(getattr(dialog, "plan_markdown", "") or "")
            self._add_artifact(
                ResearchStepType.PLAN,
                ResearchArtifactType.PLAN,
                plan,
                path="program.md",
                verified=bool(plan),
                metadata={
                    "revision": getattr(dialog, "revision", 0),
                    "content_hash": getattr(dialog, "content_hash", ""),
                },
            )

        if phase in {"report_ready", "validation_needs_revision", "completed"}:
            changed_paths = tuple(getattr(dialog, "changed_paths", ()) or ())
            commit_sha = str(getattr(dialog, "commit_sha", "") or "")
            diff_evidence = json.dumps(
                {"changed_paths": changed_paths, "commit_sha": commit_sha},
                sort_keys=True,
            )
            self._add_artifact(
                ResearchStepType.IMPLEMENT,
                ResearchArtifactType.CODE_DIFF,
                diff_evidence,
                path=f"git://{commit_sha or 'worktree'}",
                verified=bool(changed_paths and commit_sha),
                metadata={"changed_paths": list(changed_paths)},
            )

            validation_report = str(
                getattr(dialog, "validation_report", "") or ""
            )
            verification_status = str(
                getattr(dialog, "verification_status", "pending") or "pending"
            )
            reproduction_status = str(
                getattr(dialog, "reproduction_status", "pending") or "pending"
            )
            self._add_artifact(
                ResearchStepType.TEST,
                ResearchArtifactType.TEST_RESULT,
                validation_report,
                path="validation-report.md",
                verified=verification_status == "passed",
                metadata={
                    "reproduction_status": reproduction_status,
                    "verification_status": verification_status,
                    "failure_category": getattr(
                        dialog,
                        "validation_failure_category",
                        "",
                    ),
                },
            )
            self._add_artifact(
                ResearchStepType.REVIEW,
                ResearchArtifactType.REPORT,
                validation_report,
                path="validation-report.md",
                verified=bool(validation_report),
            )

        if phase in {"pushing", "creating_pr", "pr_created", "completed"}:
            commit_sha = str(getattr(dialog, "commit_sha", "") or "")
            self._add_artifact(
                ResearchStepType.DELIVERY,
                ResearchArtifactType.COMMIT,
                commit_sha,
                path=f"git://{commit_sha}",
                verified=bool(commit_sha),
            )
            pr_url = str(getattr(dialog, "pr_url", "") or "")
            self._add_artifact(
                ResearchStepType.DELIVERY,
                ResearchArtifactType.PULL_REQUEST,
                pr_url,
                path=pr_url,
                verified=bool(pr_url),
            )

    def observe(self, phase: str, detail: str, dialog: Any) -> None:
        step_type = _PHASE_TO_STEP.get(phase)
        if step_type is None and phase not in {"failed", "cancelled", "rejected"}:
            return

        if step_type is not None:
            step = self._ensure_step(step_type, dialog)
            self._complete_prior_steps(step.index)
            if step.status not in _TERMINAL_STEP_STATUSES:
                self.steps.update_status(step.step_id, ResearchStepStatus.RUNNING)
            self._collect_dialog_artifacts(phase, dialog)

            if phase == "completed":
                delivery = self.steps.get(step.step_id)
                if delivery is not None:
                    ready = self.artifacts.all_verified(
                        delivery.step_id,
                        delivery.required_artifacts,
                    )
                    self.steps.update_status(
                        delivery.step_id,
                        ResearchStepStatus.COMPLETED
                        if ready
                        else ResearchStepStatus.BLOCKED,
                    )
            elif phase == "validation_needs_revision":
                review_id = self._step_id(ResearchStepType.REVIEW)
                if self.steps.get(review_id) is not None:
                    self.steps.update_status(
                        review_id,
                        ResearchStepStatus.BLOCKED,
                    )
            elif phase == "report_ready":
                test_id = self._step_id(ResearchStepType.TEST)
                test_step = self.steps.get(test_id)
                if test_step is not None:
                    verified = self.artifacts.all_verified(
                        test_id,
                        test_step.required_artifacts,
                    )
                    self.steps.update_status(
                        test_id,
                        ResearchStepStatus.COMPLETED
                        if verified
                        else ResearchStepStatus.BLOCKED,
                    )

            self.events.append(
                ResearchStepEvent.create(
                    event_type=phase,
                    run_id=self.plan_id,
                    step_id=step.step_id,
                    payload={"detail": detail},
                )
            )
            return

        active = [
            step
            for step in self.steps.list_for_run(self.plan_id)
            if step.status == ResearchStepStatus.RUNNING
        ]
        if active:
            status = {
                "failed": ResearchStepStatus.FAILED,
                "cancelled": ResearchStepStatus.CANCELLED,
                "rejected": ResearchStepStatus.CANCELLED,
            }[phase]
            self.steps.update_status(active[-1].step_id, status)
            self.events.append(
                ResearchStepEvent.create(
                    event_type=phase,
                    run_id=self.plan_id,
                    step_id=active[-1].step_id,
                    payload={"detail": detail},
                )
            )

    def to_payload(self) -> dict[str, Any]:
        steps = self.steps.list_for_run(self.plan_id)
        artifacts = [
            artifact
            for step in steps
            for artifact in self.artifacts.list_for_step(step.step_id)
        ]
        return {
            "schema_version": 1,
            "plan_id": self.plan_id,
            "steps": [asdict(step) for step in steps],
            "artifacts": [asdict(artifact) for artifact in artifacts],
            "events": [
                event.to_dict()
                for event in self.events.list_for_run(self.plan_id)
            ],
        }

    def to_json(self) -> str:
        return json.dumps(
            self.to_payload(),
            sort_keys=True,
            default=lambda value: value.value if hasattr(value, "value") else str(value),
        )

    @classmethod
    def from_json(cls, raw: str) -> "DialogExecutionLedger":
        payload = json.loads(raw)
        plan_id = str(payload["plan_id"])
        ledger = cls(plan_id)
        for item in payload.get("steps", ()):
            ledger.steps.create(
                ResearchStepContract(
                    step_id=str(item["step_id"]),
                    run_id=str(item["run_id"]),
                    step_type=ResearchStepType(item["step_type"]),
                    index=int(item["index"]),
                    executor=str(item["executor"]),
                    required_artifacts=tuple(
                        ResearchArtifactType(value)
                        for value in item.get("required_artifacts", ())
                    ),
                    input_artifacts=tuple(item.get("input_artifacts", ())),
                    validation_rules=tuple(item.get("validation_rules", ())),
                    status=ResearchStepStatus(item["status"]),
                )
            )
        for item in payload.get("artifacts", ()):
            ledger.artifacts.add(
                ResearchArtifactContract(
                    artifact_id=str(item["artifact_id"]),
                    run_id=str(item["run_id"]),
                    step_id=str(item["step_id"]),
                    artifact_type=ResearchArtifactType(item["artifact_type"]),
                    path=str(item["path"]),
                    content_hash=str(item["content_hash"]),
                    verified=bool(item.get("verified")),
                    metadata=dict(item.get("metadata") or {}),
                )
            )
        for item in payload.get("events", ()):
            ledger.events.append(
                ResearchStepEvent(
                    event_type=str(item["event_type"]),
                    run_id=str(item["run_id"]),
                    step_id=str(item["step_id"]),
                    timestamp=str(item["timestamp"]),
                    payload=dict(item.get("payload") or {}),
                )
            )
        return ledger


def install_research_step_runtime_service(research_module: ModuleType) -> None:
    """Install the Step/Artifact projection around existing dialog behavior."""

    if getattr(research_module, "_step_runtime_service_installed", False):
        return

    research_module._dialog_execution_ledgers = {}

    def get_ledger(plan_id: str) -> DialogExecutionLedger:
        existing = research_module._dialog_execution_ledgers.get(plan_id)
        if existing is not None:
            return existing
        raw = research_module._dialog_runtime_context.get(plan_id, {}).get(
            "execution_ledger"
        )
        if isinstance(raw, str) and raw.strip():
            try:
                existing = DialogExecutionLedger.from_json(raw)
            except (KeyError, TypeError, ValueError, json.JSONDecodeError):
                research_module._log.exception(
                    "Ignoring invalid execution ledger for %s",
                    plan_id,
                )
                existing = None
        if existing is None:
            existing = DialogExecutionLedger(plan_id)
        research_module._dialog_execution_ledgers[plan_id] = existing
        return existing

    base_emit = research_module._dialog_emit

    def emit(plan_id: str, phase: str, detail: str = "") -> None:
        base_emit(plan_id, phase, detail)
        dialog = research_module._dialog_runs.get(plan_id)
        if dialog is None:
            return
        ledger = get_ledger(plan_id)
        ledger.observe(phase, detail, dialog)
        context = research_module._dialog_runtime_context.setdefault(plan_id, {})
        context["execution_ledger"] = ledger.to_json()
        queue_snapshot = getattr(
            research_module,
            "_queue_dialog_snapshot",
            None,
        )
        if callable(queue_snapshot):
            queue_snapshot(dialog)

    base_payload = research_module._dialog_payload

    def payload(dialog: Any) -> dict[str, Any]:
        result = base_payload(dialog)
        result["execution_ledger"] = get_ledger(dialog.plan_id).to_payload()
        return result

    base_close = research_module.close_research_ledger

    async def close() -> None:
        await base_close()
        research_module._dialog_execution_ledgers.clear()

    research_module._get_dialog_execution_ledger = get_ledger
    research_module._dialog_emit = emit
    research_module._dialog_payload = payload
    research_module.close_research_ledger = close
    research_module._step_runtime_service_installed = True
