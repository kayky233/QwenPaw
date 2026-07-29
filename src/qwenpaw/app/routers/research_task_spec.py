"""Typed task and validation contracts for supervised AutoResearch."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass
from enum import Enum
from types import ModuleType
from typing import Any


class ResearchTaskType(str, Enum):
    """Supported repository research task categories."""

    BUG_FIX = "bug_fix"
    FEATURE = "feature"
    REFACTOR = "refactor"
    PERFORMANCE = "performance"
    RESEARCH = "research"


class BaselineExpectation(str, Enum):
    """How the validation pipeline should interpret baseline executions."""

    MUST_FAIL = "must_fail"
    MUST_PASS = "must_pass"
    INFORMATIONAL = "informational"
    NOT_REQUIRED = "not_required"


class DeliveryMode(str, Enum):
    """The intended externally visible deliverable."""

    PULL_REQUEST = "pull_request"
    REPORT_ONLY = "report_only"


@dataclass(frozen=True)
class ValidationContract:
    """Evidence required before a task may be delivered."""

    baseline_expectation: BaselineExpectation
    candidate_must_pass: bool
    requires_focused_tests: bool
    requires_acceptance_criteria: bool
    requires_metric_evidence: bool = False
    requires_behavior_invariants: bool = False
    requires_experiment_evidence: bool = False


@dataclass(frozen=True)
class DeliveryContract:
    """Branch, commit, and pull-request policy for one task type."""

    mode: DeliveryMode
    branch_prefix: str
    commit_prefix: str
    pull_request_prefix: str
    allow_auto_pr: bool


@dataclass(frozen=True)
class TaskSpec:
    """Versioned task classification and its immutable contracts."""

    task_type: ResearchTaskType
    validation: ValidationContract
    delivery: DeliveryContract
    source: str
    confidence: float
    rationale: str
    basis_hash: str
    schema_version: int = 1

    def to_dict(self) -> dict[str, Any]:
        """Return a stable JSON-compatible representation."""

        payload = asdict(self)
        payload["task_type"] = self.task_type.value
        payload["validation"]["baseline_expectation"] = (
            self.validation.baseline_expectation.value
        )
        payload["delivery"]["mode"] = self.delivery.mode.value
        return payload

    def to_json(self) -> str:
        """Serialize the specification deterministically for snapshots."""

        return json.dumps(
            self.to_dict(),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "TaskSpec":
        """Validate and decode a stored task specification."""

        if int(payload.get("schema_version", 0)) != 1:
            raise ValueError("Unsupported TaskSpec schema version")
        validation = dict(payload.get("validation") or {})
        delivery = dict(payload.get("delivery") or {})
        return cls(
            task_type=ResearchTaskType(str(payload["task_type"])),
            validation=ValidationContract(
                baseline_expectation=BaselineExpectation(
                    str(validation["baseline_expectation"]),
                ),
                candidate_must_pass=bool(
                    validation["candidate_must_pass"],
                ),
                requires_focused_tests=bool(
                    validation["requires_focused_tests"],
                ),
                requires_acceptance_criteria=bool(
                    validation["requires_acceptance_criteria"],
                ),
                requires_metric_evidence=bool(
                    validation.get("requires_metric_evidence", False),
                ),
                requires_behavior_invariants=bool(
                    validation.get("requires_behavior_invariants", False),
                ),
                requires_experiment_evidence=bool(
                    validation.get("requires_experiment_evidence", False),
                ),
            ),
            delivery=DeliveryContract(
                mode=DeliveryMode(str(delivery["mode"])),
                branch_prefix=str(delivery["branch_prefix"]),
                commit_prefix=str(delivery["commit_prefix"]),
                pull_request_prefix=str(delivery["pull_request_prefix"]),
                allow_auto_pr=bool(delivery["allow_auto_pr"]),
            ),
            source=str(payload["source"]),
            confidence=float(payload["confidence"]),
            rationale=str(payload["rationale"]),
            basis_hash=str(payload["basis_hash"]),
            schema_version=1,
        )

    @classmethod
    def from_json(cls, raw: str) -> "TaskSpec":
        """Decode a specification persisted in runtime context."""

        payload = json.loads(raw)
        if not isinstance(payload, dict):
            raise ValueError("TaskSpec JSON must contain an object")
        return cls.from_dict(payload)


_EXPLICIT_TASK_TYPE_RE = re.compile(
    r"(?im)^\s*(?:Research\s+)?Task\s+Type\s*:\s*"
    r"`?([a-z][a-z0-9 _-]*)`?\s*$",
)
_ISSUE_RE = re.compile(r"(?:issues?/|#)(\d{1,10})", re.IGNORECASE)

_TASK_TYPE_ALIASES = {
    "bug": ResearchTaskType.BUG_FIX,
    "bug fix": ResearchTaskType.BUG_FIX,
    "bug_fix": ResearchTaskType.BUG_FIX,
    "bugfix": ResearchTaskType.BUG_FIX,
    "fix": ResearchTaskType.BUG_FIX,
    "feature": ResearchTaskType.FEATURE,
    "feature development": ResearchTaskType.FEATURE,
    "enhancement": ResearchTaskType.FEATURE,
    "refactor": ResearchTaskType.REFACTOR,
    "refactoring": ResearchTaskType.REFACTOR,
    "performance": ResearchTaskType.PERFORMANCE,
    "optimization": ResearchTaskType.PERFORMANCE,
    "optimisation": ResearchTaskType.PERFORMANCE,
    "research": ResearchTaskType.RESEARCH,
    "investigation": ResearchTaskType.RESEARCH,
    "experiment": ResearchTaskType.RESEARCH,
}

_TASK_PATTERNS: tuple[tuple[ResearchTaskType, re.Pattern[str]], ...] = (
    (
        ResearchTaskType.PERFORMANCE,
        re.compile(
            r"\b(?:performance|latency|throughput|benchmark|profil(?:e|ing)|"
            r"optimi[sz](?:e|ation)|memory\s+usage|cpu\s+usage)\b",
            re.IGNORECASE,
        ),
    ),
    (
        ResearchTaskType.REFACTOR,
        re.compile(
            r"\b(?:refactor|restructure|reorganize|cleanup|clean\s+up|"
            r"decouple|extract\s+(?:module|service|class))\b",
            re.IGNORECASE,
        ),
    ),
    (
        ResearchTaskType.RESEARCH,
        re.compile(
            r"\b(?:research|investigate|compare|evaluate|experiment|study|"
            r"explore|feasibility)\b",
            re.IGNORECASE,
        ),
    ),
    (
        ResearchTaskType.FEATURE,
        re.compile(
            r"\b(?:feature|enhancement|implement|add|support|capability|"
            r"introduce|new\s+api)\b",
            re.IGNORECASE,
        ),
    ),
    (
        ResearchTaskType.BUG_FIX,
        re.compile(
            r"\b(?:bug|fix|defect|regression|crash|error|failure|broken|"
            r"incorrect|does\s+not|doesn't|cannot|can't)\b",
            re.IGNORECASE,
        ),
    ),
)


def task_spec_basis(goal: str, plan_markdown: str = "") -> str:
    """Hash the exact text used to infer a TaskSpec."""

    return hashlib.sha256(
        f"{goal.strip()}\n---PLAN---\n{plan_markdown.strip()}".encode(
            "utf-8",
        ),
    ).hexdigest()


def _normalized_explicit_type(value: str) -> ResearchTaskType | None:
    normalized = re.sub(r"[\s-]+", " ", value.strip().casefold())
    direct = _TASK_TYPE_ALIASES.get(normalized)
    if direct is not None:
        return direct
    underscored = normalized.replace(" ", "_")
    try:
        return ResearchTaskType(underscored)
    except ValueError:
        return None


def infer_task_type(
    goal: str,
    plan_markdown: str = "",
) -> tuple[ResearchTaskType, str, float, str]:
    """Infer a task type, preferring an explicit plan declaration."""

    explicit = _EXPLICIT_TASK_TYPE_RE.search(plan_markdown)
    if explicit is not None:
        task_type = _normalized_explicit_type(explicit.group(1))
        if task_type is not None:
            return (
                task_type,
                "explicit_plan",
                1.0,
                "The approved plan declares an explicit Task Type.",
            )

    text = f"{goal}\n{plan_markdown}"
    for task_type, pattern in _TASK_PATTERNS:
        if pattern.search(text):
            return (
                task_type,
                "inferred_text",
                0.8,
                f"Matched {task_type.value} intent in the goal or plan.",
            )

    if _ISSUE_RE.search(text):
        return (
            ResearchTaskType.BUG_FIX,
            "legacy_issue_default",
            0.65,
            "Issue-oriented tasks retain the historical bug-fix contract.",
        )
    return (
        ResearchTaskType.BUG_FIX,
        "legacy_default",
        0.5,
        "No explicit type was found; the legacy bug-fix contract was retained.",
    )


def validation_contract_for(
    task_type: ResearchTaskType,
) -> ValidationContract:
    """Return the immutable evidence contract for a task category."""

    if task_type is ResearchTaskType.BUG_FIX:
        return ValidationContract(
            baseline_expectation=BaselineExpectation.MUST_FAIL,
            candidate_must_pass=True,
            requires_focused_tests=True,
            requires_acceptance_criteria=True,
        )
    if task_type is ResearchTaskType.FEATURE:
        return ValidationContract(
            baseline_expectation=BaselineExpectation.INFORMATIONAL,
            candidate_must_pass=True,
            requires_focused_tests=True,
            requires_acceptance_criteria=True,
        )
    if task_type is ResearchTaskType.REFACTOR:
        return ValidationContract(
            baseline_expectation=BaselineExpectation.MUST_PASS,
            candidate_must_pass=True,
            requires_focused_tests=True,
            requires_acceptance_criteria=True,
            requires_behavior_invariants=True,
        )
    if task_type is ResearchTaskType.PERFORMANCE:
        return ValidationContract(
            baseline_expectation=BaselineExpectation.MUST_PASS,
            candidate_must_pass=True,
            requires_focused_tests=True,
            requires_acceptance_criteria=True,
            requires_metric_evidence=True,
        )
    return ValidationContract(
        baseline_expectation=BaselineExpectation.NOT_REQUIRED,
        candidate_must_pass=False,
        requires_focused_tests=False,
        requires_acceptance_criteria=False,
        requires_experiment_evidence=True,
    )


def delivery_contract_for(task_type: ResearchTaskType) -> DeliveryContract:
    """Return branch and external delivery policy for a task category."""

    if task_type is ResearchTaskType.BUG_FIX:
        return DeliveryContract(
            mode=DeliveryMode.PULL_REQUEST,
            branch_prefix="issue",
            commit_prefix="fix",
            pull_request_prefix="fix",
            allow_auto_pr=True,
        )
    if task_type is ResearchTaskType.FEATURE:
        return DeliveryContract(
            mode=DeliveryMode.PULL_REQUEST,
            branch_prefix="feature",
            commit_prefix="feat",
            pull_request_prefix="feat",
            allow_auto_pr=True,
        )
    if task_type is ResearchTaskType.REFACTOR:
        return DeliveryContract(
            mode=DeliveryMode.PULL_REQUEST,
            branch_prefix="refactor",
            commit_prefix="refactor",
            pull_request_prefix="refactor",
            allow_auto_pr=True,
        )
    if task_type is ResearchTaskType.PERFORMANCE:
        return DeliveryContract(
            mode=DeliveryMode.PULL_REQUEST,
            branch_prefix="performance",
            commit_prefix="perf",
            pull_request_prefix="perf",
            allow_auto_pr=True,
        )
    return DeliveryContract(
        mode=DeliveryMode.REPORT_ONLY,
        branch_prefix="research",
        commit_prefix="research",
        pull_request_prefix="research",
        allow_auto_pr=False,
    )


def build_task_spec(goal: str, plan_markdown: str = "") -> TaskSpec:
    """Build a complete immutable TaskSpec from current task text."""

    task_type, source, confidence, rationale = infer_task_type(
        goal,
        plan_markdown,
    )
    return TaskSpec(
        task_type=task_type,
        validation=validation_contract_for(task_type),
        delivery=delivery_contract_for(task_type),
        source=source,
        confidence=confidence,
        rationale=rationale,
        basis_hash=task_spec_basis(goal, plan_markdown),
    )


def resolve_task_spec(
    dialog: Any,
    runtime_context: dict[str, Any],
) -> TaskSpec:
    """Return a cached TaskSpec, refreshing it when plan content changes."""

    goal = str(getattr(dialog, "goal", "") or "")
    plan_markdown = str(getattr(dialog, "plan_markdown", "") or "")
    expected_hash = task_spec_basis(goal, plan_markdown)
    cached = runtime_context.get("task_spec")
    if isinstance(cached, str) and cached.strip():
        try:
            spec = TaskSpec.from_json(cached)
        except (KeyError, TypeError, ValueError, json.JSONDecodeError):
            spec = None
        if spec is not None and spec.basis_hash == expected_hash:
            return spec

    spec = build_task_spec(goal, plan_markdown)
    runtime_context["task_spec"] = spec.to_json()
    return spec


def task_contract_instructions(spec: TaskSpec) -> str:
    """Render task-specific planning and execution requirements."""

    common = [
        "## Task Contract",
        f"Task Type: {spec.task_type.value}",
        f"Delivery Mode: {spec.delivery.mode.value}",
    ]
    if spec.task_type is ResearchTaskType.BUG_FIX:
        details = [
            "- The baseline test must fail for the approved observable defect.",
            "- The candidate must pass the same focused regression test.",
            "- Include Reproduction, Expected Failure, and Verification sections.",
        ]
    elif spec.task_type is ResearchTaskType.FEATURE:
        details = [
            "- Baseline behavior is informational and does not need to fail.",
            "- The candidate must pass focused acceptance tests for the new behavior.",
            "- Include explicit Acceptance Criteria and Verification sections.",
        ]
    elif spec.task_type is ResearchTaskType.REFACTOR:
        details = [
            "- Baseline and candidate focused tests must both pass.",
            "- Include Behavior Invariants that must remain unchanged.",
            "- Do not introduce user-visible behavior changes.",
        ]
    elif spec.task_type is ResearchTaskType.PERFORMANCE:
        details = [
            "- Baseline and candidate correctness tests must both pass.",
            "- Include benchmark command, metric, baseline value, and target value.",
            "- Delivery remains blocked until trusted metric evidence is recorded.",
        ]
    else:
        details = [
            "- Produce hypotheses, an experiment matrix, evidence, and a decision.",
            "- Do not claim a code fix without implementation and validation evidence.",
            "- This task is report-only until an explicit implementation task is approved.",
        ]
    return "\n".join([*common, *details])


def install_research_task_spec(research_module: ModuleType) -> None:
    """Expose TaskSpec helpers and add contracts to planning prompts."""

    base_discovery_prompt = research_module._build_discovery_prompt
    base_plan_prompt = research_module._build_plan_only_prompt

    def discovery_prompt(
        goal: str,
        rounds: int,
        issue_evidence: str = "",
    ) -> str:
        spec = build_task_spec(goal)
        return (
            base_discovery_prompt(goal, rounds, issue_evidence)
            + "\n\n"
            + task_contract_instructions(spec)
            + "\nReturn candidate directions compatible with this task contract."
        )

    def plan_prompt(
        goal: str,
        brief: Any = None,
        issue_evidence: str = "",
    ) -> str:
        spec = build_task_spec(goal)
        return (
            base_plan_prompt(goal, brief, issue_evidence)
            + "\n\n"
            + task_contract_instructions(spec)
            + "\nThe generated program.md MUST contain the exact Task Type line."
        )

    research_module.ResearchTaskType = ResearchTaskType
    research_module.TaskSpec = TaskSpec
    research_module._build_task_spec = build_task_spec
    research_module._resolve_task_spec = resolve_task_spec
    research_module._task_contract_instructions = task_contract_instructions
    research_module._build_discovery_prompt = discovery_prompt
    research_module._build_plan_only_prompt = plan_prompt
