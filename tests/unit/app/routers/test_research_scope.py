from types import ModuleType, SimpleNamespace

import pytest

from qwenpaw.app.routers.research_scope import (
    ResearchPlanScopeError,
    approved_plan_paths,
    build_plan_revision_proposal,
    frozen_plan_paths,
    install_research_scope_policy,
    parse_research_plan_scope,
)


def test_scope_uses_only_modifiable_files_section() -> None:
    plan = """# Plan

Mention `src/not-approved.py` in the description.

## Modifiable Files

- `src/approved.py`
- tests/unit/test_approved.py

## Frozen Files

- `src/frozen.py`

## Verification

Run `tests/integration/test_not_approved.py`.
"""

    assert approved_plan_paths(plan) == {
        "src/approved.py",
        "tests/unit/test_approved.py",
    }
    assert frozen_plan_paths(plan) == {"src/frozen.py"}


def test_scope_rejects_modifiable_and_frozen_overlap() -> None:
    plan = """# Plan

## Modifiable Files
- src/shared.py

## Frozen Files
- src/shared.py
"""

    with pytest.raises(ResearchPlanScopeError, match="both modifiable and frozen"):
        parse_research_plan_scope(plan)


@pytest.mark.parametrize(
    "path",
    [
        "../src/escape.py",
        "/tmp/absolute.py",
        "C:/temp/absolute.py",
        "src/**/*.py",
        "src/module/",
    ],
)
def test_scope_rejects_unsafe_paths(path: str) -> None:
    plan = f"# Plan\n\n## Modifiable Files\n- `{path}`\n"

    with pytest.raises(ResearchPlanScopeError):
        parse_research_plan_scope(plan)


def test_scope_rejects_duplicate_paths() -> None:
    plan = """# Plan

## Modifiable Files
- src/fix.py
- `src/fix.py`
"""

    with pytest.raises(ResearchPlanScopeError, match="Duplicate path"):
        parse_research_plan_scope(plan)


def test_legacy_modify_line_remains_narrowly_compatible() -> None:
    plan = """# Plan

Modify `src/fix.py` and `tests/unit/test_fix.py`.
Run `tests/integration/test_unapproved.py` after the change.
"""

    scope = parse_research_plan_scope(plan)

    assert scope.legacy_syntax is True
    assert scope.modifiable_paths == {
        "src/fix.py",
        "tests/unit/test_fix.py",
    }


def test_revision_proposal_updates_modifiable_section_not_free_text() -> None:
    plan = """# Plan

## Modifiable Files
- `src/fix.py`

## Frozen Files
- `src/frozen.py`
"""
    dialog = SimpleNamespace(
        plan_markdown=plan,
        unapproved_paths=["tests/unit/test_fix.py"],
        verification_summary="",
        reproduction_summary="",
        error="",
    )

    proposal, reason = build_plan_revision_proposal(dialog)

    assert reason == "根据未审批变更路径生成"
    assert "- `src/fix.py`" in proposal
    assert "- `tests/unit/test_fix.py`" in proposal
    assert "## Frozen Files\n- `src/frozen.py`" in proposal
    assert "Proposed Scope Revision" not in proposal


def test_revision_proposal_migrates_legacy_plan_to_structured_scope() -> None:
    dialog = SimpleNamespace(
        plan_markdown="# Plan\n\nModify `src/fix.py`.",
        unapproved_paths=["tests/unit/test_fix.py"],
        verification_summary="",
        reproduction_summary="",
        error="",
    )

    proposal, _ = build_plan_revision_proposal(dialog)

    assert "## Modifiable Files" in proposal
    assert "- `src/fix.py`" in proposal
    assert "- `tests/unit/test_fix.py`" in proposal


def test_install_policy_rejects_invalid_scope_as_incompatible() -> None:
    research_module = ModuleType("research_for_scope_test")

    def compatibility(
        _plan: str,
        *,
        host_platform: str | None = None,
        host_machine: str | None = None,
    ) -> tuple[str, str, str]:
        del host_platform, host_machine
        return "compatible", "macOS (arm64)", "environment is compatible"

    research_module._plan_environment_compatibility = compatibility
    install_research_scope_policy(research_module)

    status, environment, reason = research_module._plan_environment_compatibility(
        "# Plan\n\n## Modifiable Files\n- `../escape.py`\n",
    )

    assert status == "incompatible"
    assert environment == "macOS (arm64)"
    assert "计划文件范围无效" in reason


def test_install_policy_keeps_valid_environment_result() -> None:
    research_module = ModuleType("research_for_scope_test")

    def compatibility(
        _plan: str,
        *,
        host_platform: str | None = None,
        host_machine: str | None = None,
    ) -> tuple[str, str, str]:
        del host_platform, host_machine
        return "unknown", "Linux (x86_64)", "manual review required"

    research_module._plan_environment_compatibility = compatibility
    install_research_scope_policy(research_module)

    result = research_module._plan_environment_compatibility(
        "# Plan\n\n## Modifiable Files\n- `src/fix.py`\n",
    )

    assert result == (
        "unknown",
        "Linux (x86_64)",
        "manual review required",
    )
