from __future__ import annotations

from types import SimpleNamespace

from qwenpaw.app.routers import research_services


def test_campaign_services_install_in_dependency_order(monkeypatch) -> None:
    calls: list[str] = []
    names = [
        name
        for name in vars(research_services)
        if name.startswith("install_research_")
        or name == "enable_execution_ledger_snapshot_persistence"
    ]
    for name in names:
        monkeypatch.setattr(
            research_services,
            name,
            lambda *args, _name=name, **kwargs: calls.append(_name),
        )

    research_services.install_research_services(SimpleNamespace())

    direct = calls.index("install_research_campaign_direct_service")
    capability = calls.index("install_research_campaign_capability_service")
    revision = calls.index("install_research_campaign_revision_service")
    monitor = calls.index("install_research_campaign_monitor_service")
    refresh = calls.index("install_research_campaign_refresh_service")
    control = calls.index("install_research_campaign_worktree_control_service")

    assert direct < capability < revision < monitor
    assert monitor < refresh < control
    assert calls.index("install_research_campaign_service") < direct
    assert calls.index("install_research_campaign_delivery_mode_service") < direct
