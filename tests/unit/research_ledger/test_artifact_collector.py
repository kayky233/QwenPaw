from qwenpaw.research_ledger.artifact_collector import ArtifactCollector
from qwenpaw.research_ledger.contracts import ResearchArtifactType


def test_collect_text_creates_hash():
    artifact = ArtifactCollector("r1").from_text(
        "s1",
        "a1",
        ResearchArtifactType.REPORT,
        "report.md",
        "hello",
    )
    assert len(artifact.content_hash) == 64
