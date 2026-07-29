from qwenpaw.research_ledger.contracts import ResearchArtifactType
from qwenpaw.research_ledger.execution_evidence import ExecutionEvidenceCollector
from qwenpaw.research_ledger.execution_runner import CommandRequest, LocalSubprocessRunner


def test_local_runner_and_evidence(tmp_path):
    runner = LocalSubprocessRunner()
    result = runner.run(
        CommandRequest(
            argv=("python", "-c", "print('ok')"),
            cwd=str(tmp_path),
            timeout_seconds=5,
        )
    )
    artifact = ExecutionEvidenceCollector("run").collect(
        "step",
        "command",
        ResearchArtifactType.LOG,
        result,
    )
    assert result.succeeded
    assert artifact.verified
    assert artifact.metadata["exit_code"] == 0
