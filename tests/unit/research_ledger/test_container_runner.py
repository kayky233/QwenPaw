from qwenpaw.research_ledger.container_runner import (
    ContainerExecutionRunner,
    ContainerPolicy,
)
from qwenpaw.research_ledger.execution_runner import CommandRequest


def test_container_runner_disables_network_and_limits_resources(tmp_path):
    runner = ContainerExecutionRunner(ContainerPolicy(image="python:3.12"))
    command = runner.build_command(
        CommandRequest(
            argv=("pytest", "-q"),
            cwd=str(tmp_path),
            environment={"PYTHONUNBUFFERED": "1"},
        )
    )
    assert "--network" in command
    assert command[command.index("--network") + 1] == "none"
    assert "--read-only" in command
    assert "--memory" in command
    assert runner.capabilities.containerized
