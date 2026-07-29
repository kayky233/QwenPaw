"""Composition root for extracted AutoResearch router services."""

from __future__ import annotations

from types import ModuleType

from .research_dialog_service import install_research_dialog_service
from .research_github_service import install_research_github_service
from .research_scope import install_research_scope_policy
from .research_state_machine import install_research_state_machine
from .research_validation_service import install_research_validation_service
from .research_worktree_service import install_research_worktree_service


def install_research_services(research_module: ModuleType) -> None:
    """Install extracted services in dependency-safe order."""

    install_research_scope_policy(research_module)
    install_research_state_machine(research_module)
    install_research_dialog_service(research_module)
    install_research_github_service(research_module)
    install_research_validation_service(research_module)
    install_research_worktree_service(research_module)
