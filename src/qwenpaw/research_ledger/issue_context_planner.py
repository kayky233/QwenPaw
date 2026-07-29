"""Connect GitHub issue planning with repository graph localization."""

from __future__ import annotations

from dataclasses import dataclass

from .context_pack import RepositoryContextPack, RepositoryContextPackBuilder
from .issue_solver import IssueSolvePlan, IssueSolverPlanner, IssueTask
from .repository_analyzer import RepositoryAnalysis, RepositoryAnalyzer


@dataclass(frozen=True)
class ContextualIssueSolvePlan:
    plan: IssueSolvePlan
    analysis: RepositoryAnalysis
    context_pack: RepositoryContextPack


class ContextualIssueSolverPlanner:
    def __init__(
        self,
        analyzer: RepositoryAnalyzer,
        context_builder: RepositoryContextPackBuilder,
        planner: IssueSolverPlanner | None = None,
    ) -> None:
        self.analyzer = analyzer
        self.context_builder = context_builder
        self.planner = planner or IssueSolverPlanner()

    def create_plan(self, issue: IssueTask) -> ContextualIssueSolvePlan:
        issue_text = f"{issue.title}\n{issue.description}"
        analysis = self.analyzer.analyze(issue_text)
        context_pack = self.context_builder.build(analysis)
        return ContextualIssueSolvePlan(
            plan=self.planner.create_plan(issue),
            analysis=analysis,
            context_pack=context_pack,
        )
