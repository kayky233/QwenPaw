"""Select focused tests from an ImpactSet."""

from __future__ import annotations

from dataclasses import dataclass

from .impact_analysis import ImpactSet


@dataclass(frozen=True)
class SelectedTest:
    path: str
    reason: str


class GraphTestSelector:
    def select(self, impact: ImpactSet, limit: int = 20) -> tuple[SelectedTest, ...]:
        selected: list[SelectedTest] = []
        seen: set[str] = set()
        for path in impact.test_paths:
            if path in seen:
                continue
            seen.add(path)
            selected.append(
                SelectedTest(
                    path=path,
                    reason="repository graph links this test to affected source paths",
                )
            )
            if len(selected) >= limit:
                break
        return tuple(selected)
