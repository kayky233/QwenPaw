"""Activation hook for durable Campaign worktree checkpoints."""

from __future__ import annotations

from types import ModuleType

from .research_campaign_worktree_checkpoint_service import (
    install_research_campaign_worktree_checkpoint_service,
)


def install(research_module: ModuleType) -> None:
    """Activate worktree checkpoint persistence after Campaign state exists."""

    install_research_campaign_worktree_checkpoint_service(research_module)
