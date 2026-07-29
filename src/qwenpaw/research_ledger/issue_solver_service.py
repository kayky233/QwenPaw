"""Application service for exact issue intake and repository localization."""

from __future__ import annotations

from dataclasses import dataclass

from .issue_context_planner import ContextualIssueSolvePlan, ContextualIssueSolverPlanner
from .issue_fetcher import IssueEvidence, IssueProvider
from .issue_solver import IssueTask


@dataclass(frozen=True)
class PreparedIssueSolve:
    evidence: IssueEvidence
    contextual_plan: ContextualIssueSolvePlan


class IssueSolverService:
    def __init__(
        self,
        issue_provider: IssueProvider,
        planner: ContextualIssueSolverPlanner,
    ) -> None:
        self.issue_provider = issue_provider
        self.planner = planner

    def prepare(self, repository: str, issue_number: int) -> PreparedIssueSolve:
        evidence = self.issue_provider.fetch_issue(repository, issue_number)
        comments = "\n\n".join(
            f"Comment by {comment.author}:\n{comment.body}"
            for comment in evidence.comments
        )
        description = evidence.body
        if comments:
            description = f"{description}\n\n{comments}".strip()
        task = IssueTask(
            repository=evidence.repository,
            issue_number=evidence.number,
            title=evidence.title,
            description=description,
        )
        return PreparedIssueSolve(
            evidence=evidence,
            contextual_plan=self.planner.create_plan(task),
        )
