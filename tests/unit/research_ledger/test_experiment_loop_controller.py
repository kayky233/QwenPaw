import pytest

from qwenpaw.research_ledger.experiment_contract import ExperimentContract, ExperimentStatus
from qwenpaw.research_ledger.experiment_loop import ExperimentLoopController, should_continue


class Runner:
    async def run(self, experiment, executor):
        return await executor(experiment)


class Registry:
    pass


@pytest.mark.asyncio
async def test_multi_round_controller():
    controller = ExperimentLoopController(Runner(), Registry())

    async def executor(exp):
        return ExperimentContract(
            exp.experiment_id,
            exp.run_id,
            exp.hypothesis,
            exp.change_summary,
            exp.metric_name,
            100,
            90,
            ExperimentStatus.KEPT,
        )

    results = await controller.run_rounds(
        [ExperimentContract("e", "r", "h", "c", "latency")],
        executor,
    )
    assert should_continue(results[0].experiment)
