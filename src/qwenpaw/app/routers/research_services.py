"""Composition root for extracted AutoResearch router services."""

from __future__ import annotations

from types import ModuleType

from .research_campaign_delivery_mode_service import (
    install_research_campaign_delivery_mode_service,
)
from .research_campaign_direct_service import (
    install_research_campaign_direct_service,
)
from .research_campaign_history_service import (
    install_research_campaign_history_service,
)
from .research_campaign_monitor_service import (
    install_research_campaign_monitor_service,
)
from .research_campaign_refresh_service import (
    install_research_campaign_refresh_service,
)
from .research_campaign_service import install_research_campaign_service
from .research_campaign_workspace_service import (
    install_research_campaign_workspace_service,
)
from .research_delivery_lifecycle_service import (
    install_research_delivery_lifecycle_service,
)
from .research_delivery_monitor_service import (
    install_research_delivery_monitor_service,
)
from .research_delivery_service import install_research_delivery_service
from .research_dialog_persistence import (
    install_research_dialog_persistence,
)
from .research_dialog_service import install_research_dialog_service
from .research_github_service import install_research_github_service
from .research_planning_service import install_research_planning_service
from .research_scope import install_research_scope_policy
from .research_state_machine import install_research_state_machine
from .research_step_runtime_persistence import (
    enable_execution_ledger_snapshot_persistence,
)
from .research_step_runtime_service import (
    install_research_step_runtime_service,
)
from .research_task_spec import install_research_task_spec
from .research_task_spec_api import install_research_task_spec_api
from .research_task_spec_inference import (
    install_research_task_spec_inference,
)
from .research_task_spec_runtime import install_research_task_spec_runtime
from .research_validation_pipeline import (
    install_research_validation_pipeline,
)
from .research_validation_service import (
    install_research_validation_service,
)
from .research_worktree_service import install_research_worktree_service


def install_research_services(research_module: ModuleType) -> None:
    """Install extracted services in dependency-safe order."""

    install_research_planning_service(research_module)
    install_research_scope_policy(research_module)
    install_research_state_machine(research_module)
    install_research_dialog_service(research_module)
    install_research_github_service(research_module)
    install_research_validation_service(research_module)
    install_research_validation_pipeline(research_module)
    install_research_worktree_service(research_module)
    install_research_delivery_service(research_module)
    enable_execution_ledger_snapshot_persistence()
    install_research_dialog_persistence(research_module)
    install_research_task_spec(research_module)
    install_research_task_spec_inference(research_module)
    install_research_task_spec_runtime(research_module)
    install_research_task_spec_api(research_module)
    install_research_step_runtime_service(research_module)
    install_research_delivery_lifecycle_service(research_module)
    install_research_delivery_monitor_service(research_module)
    install_research_campaign_service(research_module)
    install_research_campaign_workspace_service(research_module)
    install_research_campaign_delivery_mode_service(research_module)
    install_research_campaign_direct_service(research_module)
    install_research_campaign_monitor_service(research_module)
    install_research_campaign_history_service(research_module)
    install_research_campaign_refresh_service(research_module)
