from qwenpaw.research_ledger.contracts import ResearchArtifactType
from qwenpaw.research_ledger.git_artifact_collector import GitArtifactCollector


def test_git_diff_artifact_type():
    artifact = GitArtifactCollector("r1").collect_diff("s1")
    assert artifact.artifact_type == ResearchArtifactType.CODE_DIFF
