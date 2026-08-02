"""Attach direct recovery commands to the main ``campaign`` command group."""

from __future__ import annotations

import click

from .campaign_observer_cmd import (
    campaign_cleanup_worktree_cmd,
    campaign_recovery_cmd,
    campaign_revise_cmd,
)
from .campaign_setup_cmd import campaign_setup_cmd


def install_campaign_group_extensions(group: click.Group) -> click.Group:
    """Install stable grouped aliases while preserving top-level compatibility."""

    commands = {
        "setup-agents": campaign_setup_cmd,
        "revise": campaign_revise_cmd,
        "recovery": campaign_recovery_cmd,
        "cleanup-worktree": campaign_cleanup_worktree_cmd,
    }
    for name, command in commands.items():
        if name not in group.commands:
            group.add_command(command, name)
    return group
