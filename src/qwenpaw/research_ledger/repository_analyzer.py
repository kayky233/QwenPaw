"""Repository analysis for issue-driven AutoResearch tasks."""

from __future__ import annotations

from dataclasses import dataclass
import re

from .repository_graph import RepositoryGraphNode, RepositoryGraphProvider

_PATH_RE = re.compile(r"(?<![\w.-])(?:src|tests?|packages?|apps?)/[\w./-]+")
_SYMBOL_RE = re.compile(r"`([A-Za-z_][A-Za-z0-9_.:]*)`")
_ERROR_RE = re.compile(r"(?:error|exception|failed|traceback)[:\s]+([^\n]{3,160})", re.IGNORECASE)


@dataclass(frozen=True)
class RepositoryAnalysis:
    anchors: tuple[str, ...]
    candidate_nodes: tuple[RepositoryGraphNode, ...]
    affected_paths: tuple[str, ...]
    test_nodes: tuple[RepositoryGraphNode, ...]
    graph_available: bool
    downgrade_reason: str | None = None


class RepositoryAnalyzer:
    def __init__(self, provider: RepositoryGraphProvider):
        self.provider = provider

    def extract_anchors(self, issue_text: str) -> tuple[str, ...]:
        anchors: list[str] = []
        for value in _PATH_RE.findall(issue_text):
            anchors.append(value.rstrip(".,;:)"))
        anchors.extend(_SYMBOL_RE.findall(issue_text))
        anchors.extend(match.strip() for match in _ERROR_RE.findall(issue_text))
        return tuple(dict.fromkeys(item for item in anchors if item))

    def analyze(self, issue_text: str, limit: int = 40) -> RepositoryAnalysis:
        anchors = self.extract_anchors(issue_text)
        nodes: list[RepositoryGraphNode] = []
        seen: set[str] = set()
        for anchor in anchors:
            for node in self.provider.search_symbols(anchor, limit=limit):
                if node.node_id not in seen:
                    seen.add(node.node_id)
                    nodes.append(node)
                    if len(nodes) >= limit:
                        break
            if len(nodes) >= limit:
                break

        affected_paths = tuple(dict.fromkeys(node.path for node in nodes if node.path))
        tests = tuple(self.provider.find_tests(affected_paths)) if affected_paths else ()
        graph_available = bool(nodes or tests)
        return RepositoryAnalysis(
            anchors=anchors,
            candidate_nodes=tuple(nodes),
            affected_paths=affected_paths,
            test_nodes=tests,
            graph_available=graph_available,
            downgrade_reason=None if graph_available else "repository_graph_unavailable_or_no_matches",
        )
