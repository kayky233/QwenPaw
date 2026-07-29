from qwenpaw.research_ledger.experiment_contract import ExperimentContract, ExperimentStatus
from qwenpaw.research_ledger.experiment_judge import ExperimentJudge


def test_improved_experiment_is_kept():
    result = ExperimentJudge().evaluate(
        ExperimentContract(
            "e1",
            "r1",
            "reduce latency",
            "cache change",
            "latency",
            100,
            80,
        )
    )
    assert result.status == ExperimentStatus.KEPT


def test_regression_is_reverted():
    result = ExperimentJudge().evaluate(
        ExperimentContract(
            "e2",
            "r1",
            "reduce latency",
            "bad change",
            "latency",
            100,
            120,
        )
    )
    assert result.status == ExperimentStatus.REVERTED
