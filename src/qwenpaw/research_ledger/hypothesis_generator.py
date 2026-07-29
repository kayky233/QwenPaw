"""Generate structured optimization hypotheses for experiments."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Hypothesis:
    title: str
    problem: str
    proposed_change: str
    metric: str
    expected_improvement: float | None = None


class HypothesisGenerator:
    """First version uses explicit signals; LLM planners can plug in later."""

    def generate(
        self,
        problem: str,
        proposed_change: str,
        metric: str,
        expected_improvement: float | None = None,
    ) -> Hypothesis:
        return Hypothesis(
            title=f"Optimize: {problem}",
            problem=problem,
            proposed_change=proposed_change,
            metric=metric,
            expected_improvement=expected_improvement,
        )
