"""Build bounded repository context for planners and implementation agents."""

from __future__ import annotations

from dataclasses import dataclass

from .repository_analyzer import RepositoryAnalysis
from .repository_graph import RepositoryGraphNode


@dataclass(frozen=True)
class ContextPackItem:
    path: str
    name: str
    kind: str
    summary: str
    estimated_tokens: int


@dataclass(frozen=True)
class RepositoryContextPack:
    items: tuple[ContextPackItem, ...]
    affected_paths: tuple[str, ...]
    test_paths: tuple[str, ...]
    estimated_tokens: int
    truncated: bool
    graph_available: bool
    downgrade_reason: str | None = None


class RepositoryContextPackBuilder:
    def __init__(self, token_budget: int = 4000):
        if token_budget <= 0:
            raise ValueError("token_budget must be positive")
        self.token_budget = token_budget

    @staticmethod
    def _estimate(node: RepositoryGraphNode) -> int:
        text = " ".join((node.name, node.path, node.summary))
        return max(1, (len(text) + 3) // 4)

    def build(self, analysis: RepositoryAnalysis) -> RepositoryContextPack:
        selected: list[ContextPackItem] = []
        used = 0
        candidates = (*analysis.candidate_nodes, *analysis.test_nodes)
        truncated = False
        seen: set[tuple[str, str]] = set()
        for node in candidates:
            key = (node.path, node.name)
            if key in seen:
                continue
            seen.add(key)
            estimated = self._estimate(node)
            if used + estimated > self.token_budget:
                truncated = True
                continue
            selected.append(
                ContextPackItem(
                    path=node.path,
                    name=node.name,
                    kind=node.kind,
                    summary=node.summary,
                    estimated_tokens=estimated,
                )
            )
            used += estimated

        return RepositoryContextPack(
            items=tuple(selected),
            affected_paths=analysis.affected_paths,
            test_paths=tuple(dict.fromkeys(node.path for node in analysis.test_nodes if node.path)),
            estimated_tokens=used,
            truncated=truncated,
            graph_available=analysis.graph_available,
            downgrade_reason=analysis.downgrade_reason,
        )
