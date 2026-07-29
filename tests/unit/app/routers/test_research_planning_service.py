from pathlib import Path
from types import SimpleNamespace

from qwenpaw.app.routers.research_planning_service import (
    current_research_environment,
    derive_task_id,
    execution_prompt,
    extract_phase_content,
    install_research_planning_service,
    plan_environment_compatibility,
    task_title,
)


def test_current_research_environment_normalizes_supported_hosts() -> None:
    assert current_research_environment(
        "win32",
        "AMD64",
    ) == ("windows", "Windows (AMD64)")
    assert current_research_environment(
        "darwin",
        "arm64",
    ) == ("macos", "macOS (arm64)")
    assert current_research_environment(
        "linux",
        "x86_64",
    ) == ("linux", "Linux (x86_64)")


def test_plan_environment_requires_declared_environment() -> None:
    result = plan_environment_compatibility(
        "# Plan\n",
        environment_resolver=lambda *_: (
            "linux",
            "Linux (x86_64)",
        ),
    )

    assert result[0] == "unknown"
    assert result[1] == "Linux (x86_64)"
    assert "Reproduction Environment" in result[2]


def test_cross_platform_plan_is_compatible() -> None:
    plan = """# Plan

## Reproduction Environment
Cross-platform Python 3.12
"""

    result = plan_environment_compatibility(
        plan,
        environment_resolver=lambda *_: (
            "macos",
            "macOS (arm64)",
        ),
    )

    assert result[0] == "compatible"
    assert "跨平台" in result[2]


def test_plan_environment_matches_one_of_multiple_platforms() -> None:
    plan = """# Plan

## Reproduction Environment
Ubuntu Linux or macOS
"""

    result = plan_environment_compatibility(
        plan,
        environment_resolver=lambda *_: (
            "linux",
            "Linux (x86_64)",
        ),
    )

    assert result[0] == "compatible"
    assert "包含当前" in result[2]


def test_plan_environment_rejects_incompatible_platform() -> None:
    plan = """# Plan

## Reproduction Environment
Windows 11 AMD64, exe installer
"""

    result = plan_environment_compatibility(
        plan,
        environment_resolver=lambda *_: (
            "macos",
            "macOS (arm64)",
        ),
    )

    assert result[0] == "incompatible"
    assert "Windows" in result[2]
    assert "macOS" in result[2]


def test_task_title_uses_first_markdown_heading() -> None:
    program = "Intro text\n\n##   Fix stream recovery\n"

    assert task_title(program, "fallback") == (
        "Fix stream recovery"
    )
    assert task_title("plain text", "fallback") == "fallback"


def test_derive_task_id_keeps_current_bounded_behavior() -> None:
    assert derive_task_id(
        "Fix Research Runtime Approval State",
    ) == "fix-research-runtime-approval-state"
    assert derive_task_id("修复 研究 流程") == "auto-task"
    assert len(derive_task_id("word " * 40)) <= 60


def test_extract_phase_content_prefers_file_markers() -> None:
    response = """Preamble
<<<FILE:program.md>>>
# Approved plan
<<<END>>>
Trailing text
"""

    assert extract_phase_content(
        response,
        "program.md",
    ) == "# Approved plan"


def test_extract_phase_content_supports_fenced_blocks() -> None:
    response = """Preamble
```program.md
# Fenced plan
```
"""

    assert extract_phase_content(
        response,
        "program.md",
    ) == "# Fenced plan"


def test_extract_phase_content_returns_raw_response_without_artifact() -> None:
    response = "No structured artifact was returned."

    assert extract_phase_content(response, "program.md") == response


def test_execution_prompt_contains_approval_and_scope_contract(
    tmp_path: Path,
) -> None:
    dialog = SimpleNamespace(
        approved_revision=3,
        approved_content_hash="a" * 64,
        plan_markdown=(
            "## Modifiable Files\n"
            "- src/fix.py\n"
        ),
        validation_attempts=[],
        validation_report="",
        unapproved_paths=[],
    )

    prompt = execution_prompt(dialog, tmp_path)

    assert "Approved revision: 3" in prompt
    assert "Approved SHA-256" in prompt
    assert str(tmp_path) in prompt
    assert "Only create or edit files listed" in prompt
    assert "Do not commit, push" in prompt


def test_execution_prompt_preserves_previous_validation_feedback(
    tmp_path: Path,
) -> None:
    dialog = SimpleNamespace(
        approved_revision=4,
        approved_content_hash="b" * 64,
        plan_markdown="# Revised plan",
        validation_attempts=[{"attempt": 1}],
        validation_report="Candidate test failed",
        unapproved_paths=["tests/test_fix.py"],
    )

    prompt = execution_prompt(dialog, tmp_path)

    assert "Preserve valid changes already present" in prompt
    assert "tests/test_fix.py" in prompt
    assert "Candidate test failed" in prompt


def test_installer_uses_router_environment_test_seam() -> None:
    module = SimpleNamespace(
        _current_research_environment=(
            current_research_environment
        ),
    )
    install_research_planning_service(module)
    module._current_research_environment = lambda *_: (
        "windows",
        "Windows (AMD64)",
    )

    result = module._plan_environment_compatibility(
        "## Reproduction Environment\nWindows 11\n",
    )

    assert result[0] == "compatible"
    assert result[1] == "Windows (AMD64)"


def test_real_router_uses_extracted_planning_service() -> None:
    from qwenpaw.app.routers import research as research_module

    assert research_module._current_research_environment is (
        current_research_environment
    )
    assert research_module._task_title is task_title
    assert research_module._derive_task_id is derive_task_id
    assert research_module._extract_phase_content is (
        extract_phase_content
    )
    assert research_module._execution_prompt is execution_prompt


def test_real_router_keeps_scope_guard_over_environment_policy() -> None:
    from qwenpaw.app.routers import research as research_module

    result = research_module._plan_environment_compatibility(
        """# Plan

## Reproduction Environment
Cross-platform

## Modifiable Files
- ../escape.py
""",
        host_platform="linux",
        host_machine="x86_64",
    )

    assert result[0] == "incompatible"
    assert "文件范围无效" in result[2]
