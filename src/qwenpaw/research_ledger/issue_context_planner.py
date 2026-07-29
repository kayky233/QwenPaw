"""Connect GitHub issue planning with repository graph localization."""

from __future__ import annotations

from dataclasses import dataclass

from .context_pack import RepositoryContextPack, RepositoryContextPackBuilder
from .graph_test_selector import GraphTestSelector, SelectedTest
from .impact_analysis import ImpactSet, ImpactSetBuilder
from .issue_solver import IssueSolvePlan, IssueSolverPlanner, IssueTask
from .repository_analyzer import RepositoryAnalysis, RepositoryAnalyzer


@dataclass(frozen=True)
class ContextualIssueSolvePlan:
    plan: IssueSolvePlan
    analysis: RepositoryAnalysis
    context_pack: RepositoryContextPack
    impact_set: ImpactSet
    selected_tests: tuple[SelectedTest, ...]


class ContextualIssueSolverPlanner:
    def __init__(
        self,
        analyzer: RepositoryAnalyzer,
        context_builder: RepositoryContextPackBuilder,
        planner: IssueSolverPlanner | None = None,
        impact_builder: ImpactSetBuilder | None = None,
        test_selector: GraphTestSelector | None = None,
    ) -> None:
        self.analyzer = analyzer
        self.context_builder = context_builder
        self.planner = planner or IssueSolverPlanner()
        self.impact_builder = impact_builder or ImpactSetBuilder()
        self.test_selector = test_selector or GraphTestSelector()

    def create_plan(self, issue: IssueTask) -> ContextualIssueSolvePlan:
        issue_text = f"{issue.title}\n{issue.description}"
        analysis = self.analyzer.analyze(issue_text)
        context_pack = self.context_builder.build(analysis)
        impact_set = self.impact_builder.build(analysis)
        selected_tests = self.test_selector.select(impact_set)
        return ContextualIssueSolvePlan(
            plan=self.planner.create_plan(issue),
            analysis=analysis,
            context_pack=context_pack,
            impact_set=impact_set,
            selected_tests=selected_tests,
        )
