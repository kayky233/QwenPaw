"""Delivery-mode composition for API-driven Issue Campaigns."""

from __future__ import annotations

import asyncio
import os
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import Any

from ...agents.tools.agent_management import list_agents_data
from ...research_ledger.agent_management_transport import (
    AgentManagementTaskTransport,
)
from ...research_ledger.campaign_delivery import CampaignChangeRequestDeliverer
from ...research_ledger.change_request_providers import (
    GitHubChangeRequestProvider,
)
from ...research_ledger.collaboration_contracts import (
    ResearchAgentRole,
    TaskEnvelope,
)
from ...research_ledger.episode_package import EpisodeCommand, EpisodePackage
from ...research_ledger.execution_runner import LocalSubprocessRunner
from ...research_ledger.issue_campaign import (
    IssueCampaignRequest,
    IssueCampaignRunner,
)
from ...research_ledger.local_change_request import LocalChangeRequestProvider
from ...research_ledger.worktree_campaign import (
    GitWorktreeCampaignExecutor,
    GitWorktreeCampaignPublisher,
)
from . import research_campaign_runtime as campaign_runtime


async def _resolve_implementer_workspace(
    research_module: Any,
    campaign_id: str,
    implementer_agent_id: str,
) -> None:
    data = await asyncio.to_thread(list_agents_data)
    agents = data.get("agents", []) if isinstance(data, dict) else []
    implementer = next(
        (
            item
            for item in agents
            if isinstance(item, dict)
            and str(item.get("id", "")) == implementer_agent_id
        ),
        None,
    )
    if implementer is None:
        raise RuntimeError(
            "configured implementer agent was not found: "
            + implementer_agent_id
        )
    workspace_dir = (
        implementer.get("workspace_dir")
        or implementer.get("workspace")
        or implementer.get("working_dir")
    )
    if not isinstance(workspace_dir, str) or not workspace_dir.strip():
        raise RuntimeError(
            "implementer agent does not expose a workspace directory: "
            + implementer_agent_id
        )
    context = research_module._dialog_runtime_context.setdefault(campaign_id, {})
    context["workspace"] = SimpleNamespace(workspace_dir=workspace_dir)
    context["campaign_workspace_agent_id"] = implementer_agent_id


def _repository_parts(repository: str) -> tuple[str, str]:
    parts = repository.split("/", 1)
    if len(parts) != 2 or not all(item.strip() for item in parts):
        raise RuntimeError(f"invalid Campaign repository identity: {repository!r}")
    return parts[0], parts[1]


async def _execute_issue_campaign_with_delivery(
    research_module: Any,
    campaign_id: str,
    body: Any,
    *,
    owner_agent_id: str,
    owner_session_id: str | None,
    emit: Any,
    delivery_mode: str,
) -> campaign_runtime.CampaignRuntimeResult:
    repository = str(body.repository)
    issue_url = f"https://github.com/{repository}/issues/{body.issue_number}"
    approved_scope = "\n".join(
        f"- `{path}`" for path in body.modifiable_files
    )
    dialog = SimpleNamespace(
        plan_id=campaign_id,
        goal=issue_url,
        plan_markdown=(
            f"## Modifiable Files\n{approved_scope}\n\n"
            "## Frozen Files\n"
            + "\n".join(f"- `{path}`" for path in body.frozen_files)
        ),
        worktree_path="",
        branch="",
        upstream_repository="",
        push_repository="",
    )

    emit("preparing_worktree", "Preparing isolated Git worktree")
    worktree, branch, upstream_repository, push_repository = (
        await research_module._prepare_research_worktree(dialog)
    )
    if upstream_repository.casefold() != repository.casefold():
        raise RuntimeError(
            "prepared worktree repository does not match campaign repository"
        )
    push_owner, push_name = _repository_parts(push_repository)
    _, upstream_name = _repository_parts(upstream_repository)
    if push_name.casefold() != upstream_name.casefold():
        raise RuntimeError(
            "Campaign push repository must be the upstream repository or a "
            "same-name fork"
        )

    base_revision = (
        await research_module._run_process(
            ["git", "rev-parse", "HEAD"],
            cwd=worktree,
            timeout=30,
        )
    ).strip()
    runtime_context = research_module._dialog_runtime_context.setdefault(
        campaign_id,
        {},
    )
    base_branch = str(runtime_context.get("base_branch") or "").strip()
    if not base_branch:
        raise RuntimeError("campaign worktree did not resolve a base branch")
    runtime_context["campaign_upstream_repository"] = upstream_repository
    runtime_context["campaign_push_repository"] = push_repository

    emit("loading_issue", f"Loading GitHub issue #{body.issue_number}")
    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    evidence = await asyncio.to_thread(
        campaign_runtime._fetch_github_issue,
        repository,
        int(body.issue_number),
        token,
        research_module.urlopen,
    )
    prepared = campaign_runtime._prepared_issue(
        evidence,
        tuple(body.modifiable_files),
    )
    transport = AgentManagementTaskTransport(timeout=900)

    async def implementer(
        episode: EpisodePackage,
        attempt: int,
        feedback: str,
        target_worktree: Path,
    ) -> None:
        emit(
            "implementing",
            f"Implementer agent {body.implementer_agent_id}, attempt {attempt}",
        )
        envelope = TaskEnvelope(
            task_id=episode.episode_id,
            run_id=episode.run_id,
            step_id=f"{episode.run_id}-implement-{attempt}",
            role=ResearchAgentRole.IMPLEMENTER,
            objective=(
                "Modify the repository directly in the absolute worktree below. "
                "Do not commit, push, or create a PR. Stay strictly inside the "
                "approved paths. The host will inspect Git and validate all changes.\n\n"
                f"Worktree: {target_worktree}\n"
                f"Goal: {episode.goal}\n"
                f"Acceptance criteria: {list(episode.acceptance_criteria)}\n"
                f"Approved paths: {list(episode.modifiable_files)}\n"
                f"Repair feedback: {feedback or 'none'}"
            ),
            allowed_paths=episode.modifiable_files,
        )
        await transport.send(
            str(body.implementer_agent_id),
            envelope,
            from_agent=owner_agent_id,
            root_session_id=owner_session_id,
        )

    executor = GitWorktreeCampaignExecutor(
        worktree,
        implementer,
        research_module._run_process,
        artifact_root=(
            Path(research_module.WORKING_DIR)
            / ".qwenpaw"
            / "research-campaign-artifacts"
        ),
    )
    reviewer = campaign_runtime._AgentReviewer(
        transport,
        str(body.reviewer_agent_id),
        from_agent=owner_agent_id,
        root_session_id=owner_session_id,
        emit=emit,
    )

    same_repository = (
        push_repository.casefold() == upstream_repository.casefold()
    )
    change_request_head = (
        branch if same_repository else f"{push_owner}:{branch}"
    )
    publisher = GitWorktreeCampaignPublisher(
        worktree,
        branch,
        research_module._run_process,
        push=delivery_mode == "draft_pr",
        change_request_head=change_request_head,
    )
    if delivery_mode == "local":
        provider = LocalChangeRequestProvider(campaign_id)
    else:
        provider = GitHubChangeRequestProvider(
            campaign_runtime._GitHubChangeRequestClient(
                environment=os.environ,
                urlopen_func=research_module.urlopen,
            )
        )
    deliverer = CampaignChangeRequestDeliverer(
        provider,
        publisher,
        base_branch=base_branch,
        draft=True,
    )
    request = IssueCampaignRequest(
        repository=repository,
        issue_number=int(body.issue_number),
        run_id=campaign_id,
        base_revision=base_revision,
        task_type=str(body.task_type),
        workspace=str(worktree),
        acceptance_criteria=tuple(body.acceptance_criteria),
        commands=tuple(
            EpisodeCommand(
                command_id=str(command.command_id),
                stage=str(command.stage),
                argv=tuple(command.argv),
                cwd=str(command.cwd),
                timeout_seconds=float(command.timeout_seconds),
                required=bool(command.required),
            )
            for command in body.commands
        ),
        frozen_files=tuple(body.frozen_files),
        environment={
            "worktree": str(worktree),
            "delivery_mode": delivery_mode,
            "upstream_repository": upstream_repository,
            "push_repository": push_repository,
            "change_request_head": change_request_head,
        },
        max_attempts=int(body.max_attempts),
        modifiable_files=tuple(body.modifiable_files),
    )
    emit(
        "running",
        f"Starting bounded {delivery_mode} Issue Campaign",
    )
    outcome = await IssueCampaignRunner(
        campaign_runtime._PreparedIssueService(prepared),
        LocalSubprocessRunner(),
    ).run(
        request,
        executor=executor,
        reviewer=reviewer,
        deliverer=deliverer,
    )
    return campaign_runtime.CampaignRuntimeResult(
        outcome=outcome,
        worktree=str(worktree),
        branch=branch,
        base_branch=base_branch,
    )


def install_research_campaign_delivery_mode_service(
    research_module: ModuleType,
) -> None:
    """Install local and Draft PR delivery, including safe Fork PR heads."""

    if getattr(research_module, "_campaign_delivery_mode_installed", False):
        return

    async def execute_with_delivery_mode(
        module: Any,
        campaign_id: str,
        body: Any,
        **kwargs: Any,
    ) -> campaign_runtime.CampaignRuntimeResult:
        mode = str(getattr(body, "delivery_mode", "draft_pr")).strip()
        if mode not in {"local", "draft_pr"}:
            raise ValueError(f"unsupported Campaign delivery mode: {mode!r}")
        await _resolve_implementer_workspace(
            module,
            campaign_id,
            str(body.implementer_agent_id),
        )
        return await _execute_issue_campaign_with_delivery(
            module,
            campaign_id,
            body,
            delivery_mode=mode,
            **kwargs,
        )

    research_module._execute_issue_campaign = execute_with_delivery_mode
    research_module._campaign_delivery_mode_installed = True
