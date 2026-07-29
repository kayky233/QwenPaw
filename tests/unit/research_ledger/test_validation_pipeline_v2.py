from qwenpaw.research_ledger.execution_runner import CommandResult, ExecutionCapabilities
from qwenpaw.research_ledger.validation_contract import ValidationCommand, ValidationPlan, ValidationStage
from qwenpaw.research_ledger.validation_pipeline_v2 import ValidationPipelineV2


class Runner:
    capabilities = ExecutionCapabilities("test", False, True, True)

    def __init__(self, exit_codes):
        self.exit_codes = iter(exit_codes)

    def run(self, request):
        code = next(self.exit_codes)
        return CommandResult(
            argv=request.argv,
            cwd=request.cwd,
            exit_code=code,
            stdout="ok" if code == 0 else "",
            stderr="failed" if code else "",
            duration_seconds=0.1,
            runner_name="Runner",
        )


def test_pipeline_stops_after_required_failure(tmp_path):
    plan = ValidationPlan((
        ValidationCommand(ValidationStage.FOCUSED, ("pytest",), str(tmp_path)),
        ValidationCommand(ValidationStage.BUILD, ("npm", "run", "build"), str(tmp_path)),
    ))
    result = ValidationPipelineV2(Runner([1, 0]), "run").execute("step", plan)
    assert not result.succeeded
    assert len(result.stages) == 1
    assert not result.stages[0].artifact.verified


def test_optional_failure_does_not_fail_pipeline(tmp_path):
    plan = ValidationPlan((
        ValidationCommand(
            ValidationStage.QUALITY,
            ("pre-commit",),
            str(tmp_path),
            required=False,
        ),
    ))
    result = ValidationPipelineV2(Runner([1]), "run").execute("step", plan)
    assert result.succeeded
