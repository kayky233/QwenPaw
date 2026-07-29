"""Workflow contract for autonomous GitHub issue solving."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class IssueTask:
    repository: str
    issue_number: int
    title: str
    description: str


@dataclass(frozen=True)
class IssueSolvePlan:
    issue: IssueTask
    analysis: str
    implementation_steps: tuple[str, ...]
    validation_steps: tuple[str, ...]


class IssueSolverPlanner:
    """Planning boundary before connecting repository tools and agents."""

    def create_plan(self, issue: IssueTask) -> IssueSolvePlan:
        return IssueSolvePlan(
            issue=issue,
            analysis=f"Analyze issue: {issue.title}",
            implementation_steps=(
                "inspect repository",
                "implement change",
                "run validation",
            ),
            validation_steps=(
                "unit tests",
                "integration tests",
                "generate report",
            ),
        )
