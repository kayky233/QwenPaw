from __future__ import annotations

import json
from pathlib import Path

import pytest

from qwenpaw.research_ledger.campaign_manifest import (
    CampaignManifest,
    CampaignManifestCommand,
)


def test_manifest_parses_issue_and_builds_api_payload() -> None:
    manifest = CampaignManifest(
        issue_url="https://github.com/owner/repository/issues/42",
        acceptance_criteria=("The regression is fixed",),
        modifiable_files=("src/fix.py", "tests/test_fix.py"),
        commands=(
            CampaignManifestCommand.from_shell(
                "pytest -q tests/test_fix.py",
                index=1,
            ),
        ),
        implementer_agent_id="coder",
        reviewer_agent_id="reviewer",
    )

    payload = manifest.to_api_payload()

    assert manifest.repository == "owner/repository"
    assert manifest.issue_number == 42
    assert payload["delivery_mode"] == "local"
    assert payload["commands"][0]["argv"] == [
        "pytest",
        "-q",
        "tests/test_fix.py",
    ]


def test_manifest_template_can_be_written_before_scope_is_completed(
    tmp_path: Path,
) -> None:
    path = tmp_path / "campaign.json"
    manifest = CampaignManifest(
        issue_url="https://github.com/owner/repository/issues/7",
    )

    manifest.write(path)
    loaded = CampaignManifest.read(path)

    assert loaded.issue_number == 7
    with pytest.raises(ValueError, match="modifiable file"):
        loaded.to_api_payload()


def test_manifest_rejects_scope_overlap_and_same_agent() -> None:
    with pytest.raises(ValueError, match="must be different"):
        CampaignManifest(
            issue_url="https://github.com/owner/repository/issues/7",
            implementer_agent_id="agent",
            reviewer_agent_id="agent",
        ).validate(ready=False)

    with pytest.raises(ValueError, match="overlap"):
        CampaignManifest(
            issue_url="https://github.com/owner/repository/issues/7",
            modifiable_files=("src/fix.py",),
            frozen_files=("src/fix.py",),
        ).validate(ready=False)


def test_manifest_round_trip_preserves_command_contract(tmp_path: Path) -> None:
    target = tmp_path / "campaign.json"
    original = CampaignManifest(
        issue_url="https://github.com/owner/repository/issues/9",
        task_type="feature",
        acceptance_criteria=("New behavior works", "Old behavior remains"),
        modifiable_files=("src/feature.py", "tests/test_feature.py"),
        frozen_files=("pyproject.toml",),
        commands=(
            CampaignManifestCommand(
                command_id="focused",
                stage="unit",
                argv=("pytest", "-q", "tests/test_feature.py"),
                timeout_seconds=600,
            ),
        ),
        delivery_mode="draft_pr",
        metadata={"owner": "test"},
    )

    original.write(target)
    loaded = CampaignManifest.read(target)

    assert loaded == original
    raw = json.loads(target.read_text(encoding="utf-8"))
    assert raw["commands"][0]["timeout_seconds"] == 600
