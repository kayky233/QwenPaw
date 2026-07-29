"""Repository graph contracts used by AutoResearch code localization."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class RepositoryGraphNode:
    node_id: str
    kind: str
    name: str
    path: str
    line: int | None = None
    summary: str = ""


@dataclass(frozen=True)
class RepositoryGraphEdge:
    source: str
    target: str
    relation: str


class RepositoryGraphProvider(Protocol):
    def search_symbols(self, query: str, limit: int = 20) -> list[RepositoryGraphNode]: ...

    def neighbors(self, node_id: str, depth: int = 1) -> list[RepositoryGraphNode]: ...

    def find_tests(self, paths: tuple[str, ...]) -> list[RepositoryGraphNode]: ...


class NoopRepositoryGraphProvider:
    """Explicit downgrade path when no repository graph is available."""

    def search_symbols(self, query: str, limit: int = 20) -> list[RepositoryGraphNode]:
        return []

    def neighbors(self, node_id: str, depth: int = 1) -> list[RepositoryGraphNode]:
        return []

    def find_tests(self, paths: tuple[str, ...]) -> list[RepositoryGraphNode]:
        return []
