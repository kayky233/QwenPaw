"""Unified direct-use Campaign command entrypoint."""

from __future__ import annotations

from .campaign_cmd import campaign_cmd as _campaign_cmd
from .campaign_group_extensions import install_campaign_group_extensions

campaign_cmd = install_campaign_group_extensions(_campaign_cmd)
