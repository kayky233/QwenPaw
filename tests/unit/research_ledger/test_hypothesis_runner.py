import pytest

from qwenpaw.research_ledger.experiment_contract import ExperimentContract, ExperimentStatus
from qwenpaw.research_ledger.experiment_judge import ExperimentJudge
from qwenpaw.research_ledger.experiment_runner import ExperimentRunner
from qwenpaw.research_ledger.hypothesis_generator import HypothesisGenerator


def test_hypothesis_generation():
    result = HypothesisGenerator().generate("slow memory", "add cache", "latency")
    assert result.metric == "latency"


@pytest.mark.asyncio
async def test_runner_judges_result():
    runner = ExperimentRunner(ExperimentJudge())

    async def execute(exp):
        return ExperimentContract(
            exp.experiment_id,
            exp.run_id,
            exp.hypothesis,
            exp.change_summary,
            exp.metric_name,
            100,
            90,
            exp.status,
        )

    result = await runner.run(
        ExperimentContract("e", "r", "h", "c", "latency"),
        execute,
    )
    assert result.status == ExperimentStatus.KEPT
