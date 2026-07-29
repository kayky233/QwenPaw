"""Derive affected source and test paths from repository graph context."""

from __future__ import annotations

from dataclasses import dataclass

from .repository_analyzer import RepositoryAnalysis


@dataclass(frozen=True)
class ImpactSet:
    source_paths: tuple[str, ...]
    test_paths: tuple[str, ...]
    symbols: tuple[str, ...]
    reasons: tuple[str, ...]


class ImpactSetBuilder:
    def build(self, analysis: RepositoryAnalysis) -> ImpactSet:
        symbols = tuple(
            dict.fromkeys(node.name for node in analysis.candidate_nodes if node.name)
        )
        test_paths = tuple(
            dict.fromkeys(node.path for node in analysis.test_nodes if node.path)
        )
        reasons = tuple(
            f"anchor:{anchor}" for anchor in analysis.anchors
        )
        if analysis.downgrade_reason:
            reasons = (*reasons, f"downgrade:{analysis.downgrade_reason}")
        return ImpactSet(
            source_paths=analysis.affected_paths,
            test_paths=test_paths,
            symbols=symbols,
            reasons=reasons,
        )
