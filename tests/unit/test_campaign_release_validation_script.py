from __future__ import annotations

import importlib.util
import sys
from pathlib import Path


def _module():
    root = Path(__file__).resolve().parents[2]
    path = root / "scripts" / "validate_campaign_release.py"
    spec = importlib.util.spec_from_file_location(
        "qwenpaw_campaign_release_validation",
        path,
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_campaign_unit_test_paths_are_expanded_without_globs() -> None:
    module = _module()
    root = Path(__file__).resolve().parents[2]

    paths = module._campaign_unit_tests(root)

    assert "tests/unit/research_ledger" in paths
    assert any("test_research_campaign" in path for path in paths)
    assert any("test_campaign" in path and "/cli/" in path for path in paths)
    assert all("*" not in path for path in paths)


def test_release_validator_defaults_to_complete_local_validation() -> None:
    module = _module()

    args = module._parser().parse_args([])

    assert args.skip_broad is False
    assert args.skip_quality is False
    assert args.fail_fast is False
    assert "test_campaign_same_pr_revision_e2e.py" in module.TARGETED_TESTS
    assert "research_campaign_revision_service.py" in module.FEATURE_FILES
