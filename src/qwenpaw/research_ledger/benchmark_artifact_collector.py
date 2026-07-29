"""Benchmark evidence collection for AutoResearch experiments."""

from __future__ import annotations

import hashlib
import json

from .contracts import ResearchArtifactContract, ResearchArtifactType


class BenchmarkArtifactCollector:
    def __init__(self, run_id: str):
        self.run_id = run_id

    def collect(
        self,
        step_id: str,
        baseline: float,
        candidate: float,
        metric: str,
        lower_is_better: bool = True,
    ) -> ResearchArtifactContract:
        improvement = (
            (baseline - candidate) / baseline
            if lower_is_better
            else (candidate - baseline) / baseline
        )
        payload = {
            "metric": metric,
            "baseline": baseline,
            "candidate": candidate,
            "improvement": improvement,
        }
        content = json.dumps(payload, sort_keys=True)
        return ResearchArtifactContract(
            artifact_id=f"{step_id}-benchmark",
            run_id=self.run_id,
            step_id=step_id,
            artifact_type=ResearchArtifactType.BENCHMARK_RESULT,
            path="benchmark.json",
            content_hash=hashlib.sha256(content.encode()).hexdigest(),
            verified=True,
            metadata=payload,
        )
