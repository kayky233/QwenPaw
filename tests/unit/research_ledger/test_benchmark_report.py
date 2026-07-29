from qwenpaw.research_ledger.benchmark_artifact_collector import BenchmarkArtifactCollector
from qwenpaw.research_ledger.contracts import ResearchArtifactType
from qwenpaw.research_ledger.report_generator import ExperimentReportGenerator


def test_benchmark_collects_improvement():
    artifact = BenchmarkArtifactCollector("r1").collect(
        "s1", 100, 80, "latency"
    )
    assert artifact.artifact_type == ResearchArtifactType.BENCHMARK_RESULT
    assert artifact.metadata["improvement"] == 0.2


def test_report_generation():
    report = ExperimentReportGenerator().generate("h", "KEEP", [])
    assert report.artifact_type == ResearchArtifactType.REPORT
