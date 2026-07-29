"""Validation adapters for common QwenPaw toolchains."""

from __future__ import annotations

from .validation_contract import ValidationCommand, ValidationStage


class PytestValidationAdapter:
    def command(self, cwd: str, targets: tuple[str, ...], required: bool = True) -> ValidationCommand:
        return ValidationCommand(
            stage=ValidationStage.FOCUSED,
            argv=("pytest", "-q", *targets),
            cwd=cwd,
            required=required,
        )


class VitestValidationAdapter:
    def command(self, cwd: str, targets: tuple[str, ...], required: bool = True) -> ValidationCommand:
        return ValidationCommand(
            stage=ValidationStage.FOCUSED,
            argv=("npm", "run", "test", "--", *targets),
            cwd=cwd,
            required=required,
        )


class TypeScriptValidationAdapter:
    def command(self, cwd: str, required: bool = True) -> ValidationCommand:
        return ValidationCommand(
            stage=ValidationStage.QUALITY,
            argv=("npm", "run", "typecheck"),
            cwd=cwd,
            required=required,
        )


class PreCommitValidationAdapter:
    def command(self, cwd: str, files: tuple[str, ...], required: bool = True) -> ValidationCommand:
        return ValidationCommand(
            stage=ValidationStage.QUALITY,
            argv=("pre-commit", "run", "--files", *files),
            cwd=cwd,
            required=required,
        )


class FrontendBuildValidationAdapter:
    def command(self, cwd: str, required: bool = True) -> ValidationCommand:
        return ValidationCommand(
            stage=ValidationStage.BUILD,
            argv=("npm", "run", "build"),
            cwd=cwd,
            required=required,
            timeout_seconds=900,
        )
