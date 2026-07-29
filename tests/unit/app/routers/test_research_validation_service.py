from pathlib import Path
from types import SimpleNamespace

import pytest

from qwenpaw.app.routers.research_validation_service import (
    changed_paths_from_porcelain,
    expanded_changed_paths,
    focused_test_paths,
    install_research_validation_service,
    is_ignored_research_runtime_path,
    plan_markdown_section,
    reproduction_baseline_ref,
)


def test_changed_paths_parse_stripped_first_porcelain_column() -> None:
    paths = changed_paths_from_porcelain(
        "M src/qwenpaw/fix.py\n M tests/unit/test_fix.py",
    )

    assert paths == ["src/qwenpaw/fix.py", "tests/unit/test_fix.py"]


def test_changed_paths_parse_rename_destination_and_quotes() -> None:
    paths = changed_paths_from_porcelain(
        'R  "src/old.py" -> "src/new.py"\n?? tests/test_new.py',
    )

    assert paths == ["src/new.py", "tests/test_new.py"]


def test_changed_paths_accept_nul_separated_entries() -> None:
    paths = changed_paths_from_porcelain(
        " M src/fix.py\0?? tests/test_fix.py\0",
    )

    assert paths == ["src/fix.py", "tests/test_fix.py"]


def test_untracked_directory_is_expanded_to_exact_files(tmp_path: Path) -> None:
    nested = tmp_path / "tests" / "skills" / "browser"
    nested.mkdir(parents=True)
    (tmp_path / "tests" / "skills" / "test_one.py").write_text(
        "pass\n",
        encoding="utf-8",
    )
    (nested / "test_two.py").write_text("pass\n", encoding="utf-8")

    assert expanded_changed_paths(
        tmp_path,
        ["tests/skills/", "src/fix.py"],
    ) == [
        "tests/skills/browser/test_two.py",
        "tests/skills/test_one.py",
        "src/fix.py",
    ]


def test_runtime_metadata_filter_is_narrow() -> None:
    assert is_ignored_research_runtime_path(".skill.json.lock")
    assert is_ignored_research_runtime_path("skill.json")
    assert is_ignored_research_runtime_path(".codegraph/index.db")
    assert is_ignored_research_runtime_path("research/run.json")
    assert not is_ignored_research_runtime_path("src/research/runtime.py")
    assert not is_ignored_research_runtime_path("tests/unit/test_research.py")


def test_focused_test_paths_select_python_and_node_tests() -> None:
    changed = [
        "src/fix.py",
        "tests/unit/test_fix.py",
        "console/src/fix.test.ts",
        "console/src/fix.test.tsx",
        "console/src/fix.test.js",
        "console/src/fix.test.jsx",
        "tests/fixtures/example.txt",
    ]

    assert focused_test_paths(changed) == changed[1:6]


def test_reproduction_baseline_defaults_to_head() -> None:
    assert reproduction_baseline_ref("# Plan") == "HEAD"


@pytest.mark.parametrize(
    "ref",
    [
        "main",
        "release/1.2.3",
        "abc123def456",
        "refs/tags/v1.0.0",
    ],
)
def test_reproduction_baseline_accepts_safe_refs(ref: str) -> None:
    plan = f"## Reproduction\n\nBaseline Ref: `{ref}`\n"

    assert reproduction_baseline_ref(plan) == ref


@pytest.mark.parametrize(
    "ref",
    [
        "../main",
        "-dangerous",
        "main@{1}",
        "refs//heads/main",
        "refs/heads/main/",
        "refs/heads/main.lock",
        "main branch",
    ],
)
def test_reproduction_baseline_rejects_unsafe_refs(ref: str) -> None:
    plan = f"## Reproduction\n\nBaseline Ref: `{ref}`\n"

    with pytest.raises(RuntimeError, match="Invalid reproduction baseline ref"):
        reproduction_baseline_ref(plan)


def test_plan_markdown_section_returns_exact_section_body() -> None:
    plan = """# Plan

## Reproduction

Baseline Ref: `main`
Steps:
1. Run the regression test.

## Verification

Expected Pass: focused tests pass.
"""

    assert plan_markdown_section(plan, "Reproduction") == (
        "Baseline Ref: `main`\nSteps:\n1. Run the regression test."
    )
    assert plan_markdown_section(plan, "Missing") == (
        "Not specified in the approved plan."
    )


def test_installer_exposes_validation_helpers() -> None:
    module = SimpleNamespace()

    install_research_validation_service(module)

    assert module._changed_paths_from_porcelain is changed_paths_from_porcelain
    assert module._expanded_changed_paths is expanded_changed_paths
    assert module._is_ignored_research_runtime_path is (
        is_ignored_research_runtime_path
    )
    assert module._focused_test_paths is focused_test_paths
    assert module._reproduction_baseline_ref is reproduction_baseline_ref
    assert module._plan_markdown_section is plan_markdown_section


def test_real_router_uses_extracted_validation_helpers() -> None:
    from qwenpaw.app.routers import research as research_module

    assert research_module._changed_paths_from_porcelain is (
        changed_paths_from_porcelain
    )
    assert research_module._expanded_changed_paths is expanded_changed_paths
    assert research_module._is_ignored_research_runtime_path is (
        is_ignored_research_runtime_path
    )
    assert research_module._focused_test_paths is focused_test_paths
    assert research_module._reproduction_baseline_ref is reproduction_baseline_ref
    assert research_module._plan_markdown_section is plan_markdown_section
