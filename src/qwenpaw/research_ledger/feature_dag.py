"""Dependency graph for staged feature implementation."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class FeatureNodeType(str, Enum):
    DESIGN = "design"
    DOMAIN = "domain"
    CORE = "core"
    INTEGRATION = "integration"
    MIGRATION = "migration"
    TESTS = "tests"
    E2E = "e2e"
    DOCUMENTATION = "documentation"


@dataclass(frozen=True)
class FeatureNode:
    node_id: str
    node_type: FeatureNodeType
    objective: str
    dependencies: tuple[str, ...] = ()
    allowed_paths: tuple[str, ...] = ()


class FeatureExecutionDAG:
    def __init__(self, nodes: tuple[FeatureNode, ...]) -> None:
        self.nodes = nodes
        self._by_id = {node.node_id: node for node in nodes}
        if len(self._by_id) != len(nodes):
            raise ValueError("feature DAG node ids must be unique")
        unknown = {
            dependency
            for node in nodes
            for dependency in node.dependencies
            if dependency not in self._by_id
        }
        if unknown:
            raise ValueError(
                "feature DAG contains unknown dependencies: "
                + ", ".join(sorted(unknown))
            )
        self.topological_order()

    def topological_order(self) -> tuple[FeatureNode, ...]:
        visiting: set[str] = set()
        visited: set[str] = set()
        ordered: list[FeatureNode] = []

        def visit(node_id: str) -> None:
            if node_id in visited:
                return
            if node_id in visiting:
                raise ValueError("feature DAG contains a cycle")
            visiting.add(node_id)
            node = self._by_id[node_id]
            for dependency in node.dependencies:
                visit(dependency)
            visiting.remove(node_id)
            visited.add(node_id)
            ordered.append(node)

        for node in self.nodes:
            visit(node.node_id)
        return tuple(ordered)

    def ready_nodes(self, completed: set[str]) -> tuple[FeatureNode, ...]:
        return tuple(
            node
            for node in self.topological_order()
            if node.node_id not in completed
            and set(node.dependencies).issubset(completed)
        )
