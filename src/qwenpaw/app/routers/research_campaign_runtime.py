"""Production composition for host-verified GitHub Issue Campaigns."""

from __future__ import annotations

import asyncio
import json
import os
import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Callable
from urllib.error import HTTPError, URLError
from urllib.request import Request as UrlRequest, urlopen

from ...research_ledger.agent_management_transport import (
    AgentManagementTaskTransport,
)
from ...research_ledger.campaign_delivery import (
    CampaignChangeRequestDeliverer,
)
from ...research_ledger.change_request_providers import (
    GitHubChangeRequestProvider,
)
from ...research_ledger.collaboration_contracts import (
    ResearchAgentRole,
    ReviewDecision,
    ReviewVerdict,
    TaskEnvelope,
)
from ...research_ledger.context_pack import RepositoryContextPack
from ...research_ledger.contracts import (
    ResearchArtifactContract,
    ResearchArtifactType,
)
from ...research_ledger.episode_package import EpisodeCommand, EpisodePackage
from ...research_ledger.execution_runner import LocalSubprocessRunner
from ...research_ledger.impact_analysis import ImpactSet
from ...research_ledger.issue_campaign import (
    CampaignCandidate,
    CampaignReview,
    IssueCampaignOutcome,
    IssueCampaignRequest,
    IssueCampaignRunner,
)
from ...research_ledger.issue_context_planner import ContextualIssueSolvePlan
from ...research_ledger.issue_fetcher import (
    IssueComment,
    IssueEvidence,
)
from ...research_ledger.issue_solver import IssueSolvePlan, IssueTask
from ...research_ledger.issue_solver_service import PreparedIssueSolve
from ...research_ledger.repository_analyzer import RepositoryAnalysis
from ...research_ledger.worktree_campaign import (
    GitWorktreeCampaignExecutor,
    GitWorktreeCampaignPublisher,
)

EmitCampaignEvent = Callable[[str, str], None]


@dataclass(frozen=True)
class CampaignRuntimeResult:
    outcome: IssueCampaignOutcome
    worktree: str
    branch: str
    base_branch: str


class _PreparedIssueService:
    def __init__(self, prepared: PreparedIssueSolve) -> None:
        self.prepared = prepared

    def prepare(self, repository: str, issue_number: int) -> PreparedIssueSolve:
        evidence = self.prepared.evidence
        if evidence.repository != repository or evidence.number != issue_number:
            raise RuntimeError("prepared issue does not match campaign request")
        return self.prepared


class _GitHubChangeRequestClient:
    def __init__(
        self,
        *,
        environment: dict[str, str] | None = None,
        urlopen_func: Any = urlopen,
    ) -> None:
        self.environment = environment if environment is not None else os.environ
        self.urlopen_func = urlopen_func

    def create_pull_request(
        self,
        *,
        repository: str,
        base: str,
        head: str,
        title: str,
        body: str,
        draft: bool,
    ) -> dict[str, Any]:
        if shutil.which("gh") is not None:
            with tempfile.TemporaryDirectory(
                prefix="qwenpaw-campaign-pr-",
            ) as temp:
                body_file = Path(temp) / "body.md"
                body_file.write_text(body, encoding="utf-8")
                argv = [
                    "gh",
                    "pr",
                    "create",
                    "--repo",
                    repository,
                    "--base",
                    base,
                    "--head",
                    head,
                    "--title",
                    title,
                    "--body-file",
                    str(body_file),
                ]
                if draft:
                    argv.append("--draft")
                completed = subprocess.run(
                    argv,
                    capture_output=True,
                    text=True,
                    check=False,
                    timeout=120,
                )
            if completed.returncode != 0:
                raise RuntimeError(
                    "GitHub CLI PR creation failed: "
                    + (completed.stderr or completed.stdout).strip()
                )
            pr_url = completed.stdout.strip().splitlines()[-1].strip()
            if not pr_url:
                raise RuntimeError("GitHub CLI did not return a PR URL")
            number_match = re.search(r"/pull/(\d+)(?:$|[/?#])", pr_url)
            return {
                "html_url": pr_url,
                "number": (
                    int(number_match.group(1)) if number_match else None
                ),
            }

        token = self.environment.get("GITHUB_TOKEN") or self.environment.get(
            "GH_TOKEN"
        )
        if not token:
            raise RuntimeError(
                "campaign delivery requires authenticated gh CLI or "
                "GITHUB_TOKEN/GH_TOKEN"
            )
        payload = {
            "title": title,
            "head": head,
            "base": base,
            "body": body,
            "draft": draft,
        }
        return _github_json_request(
            f"https://api.github.com/repos/{repository}/pulls",
            token=token,
            method="POST",
            payload=payload,
            urlopen_func=self.urlopen_func,
        )


class _AgentReviewer:
    def __init__(
        self,
        transport: AgentManagementTaskTransport,
        agent_id: str,
        *,
        from_agent: str,
        root_session_id: str | None,
        emit: EmitCampaignEvent,
    ) -> None:
        self.transport = transport
        self.agent_id = agent_id
        self.from_agent = from_agent
        self.root_session_id = root_session_id
        self.emit = emit

    async def review(
        self,
        episode: EpisodePackage,
        candidate: CampaignCandidate,
        artifacts: tuple[ResearchArtifactContract, ...],
    ) -> CampaignReview:
        self.emit("reviewing", f"Reviewer agent {self.agent_id} started")
        evidence = [
            {
                "artifact_id": artifact.artifact_id,
                "type": artifact.artifact_type.value,
                "path": artifact.path,
                "verified": artifact.verified,
                "content_hash": artifact.content_hash,
                "metadata": artifact.metadata,
            }
            for artifact in artifacts
        ]
        envelope = TaskEnvelope(
            task_id=episode.episode_id,
            run_id=episode.run_id,
            step_id=f"{episode.run_id}-review",
            role=ResearchAgentRole.REVIEWER,
            objective=(
                "Review the host-verified candidate diff and validation evidence. "
                "Do not modify files. Return exactly one JSON object with keys "
                "verdict (approve, request_changes, or blocked), findings (array), "
                "and required_changes (array).\n\n"
                f"Goal: {episode.goal}\n"
                f"Allowed paths: {list(episode.modifiable_files)}\n"
                f"Candidate tree: {candidate.checkpoint.tree_revision}\n"
                f"Evidence: {json.dumps(evidence, ensure_ascii=False)}"
            ),
            expected_artifact_types=(ResearchArtifactType.REPORT,),
            allowed_paths=episode.modifiable_files,
            read_only=True,
        )
        result = await self.transport.send(
            self.agent_id,
            envelope,
            from_agent=self.from_agent,
            root_session_id=self.root_session_id,
        )
        payload = _extract_review_payload(result.text)
        decision = ReviewDecision(
            run_id=episode.run_id,
            step_id=envelope.step_id,
            verdict=ReviewVerdict(str(payload["verdict"])),
            findings=tuple(str(item) for item in payload.get("findings", ())),
            required_changes=tuple(
                str(item) for item in payload.get("required_changes", ())
            ),
        )
        report = ResearchArtifactContract(
            artifact_id=f"{candidate.checkpoint.candidate_id}-review",
            run_id=episode.run_id,
            step_id=envelope.step_id,
            artifact_type=ResearchArtifactType.REPORT,
            path=f"agent://{self.agent_id}/{result.session_id}",
            content_hash=ResearchArtifactContract.hash_content(result.text),
            verified=True,
            metadata={
                "agent_id": self.agent_id,
                "session_id": result.session_id,
                "verdict": decision.verdict.value,
                "findings": list(decision.findings),
                "required_changes": list(decision.required_changes),
            },
        )
        self.emit("reviewed", f"Reviewer verdict: {decision.verdict.value}")
        return CampaignReview(decision, report)


async def execute_issue_campaign(
    research_module: Any,
    campaign_id: str,
    body: Any,
    *,
    owner_agent_id: str,
    owner_session_id: str | None,
    emit: EmitCampaignEvent,
) -> CampaignRuntimeResult:
    """Execute one API campaign with the existing router infrastructure."""

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
    if push_repository.casefold() != upstream_repository.casefold():
        raise RuntimeError(
            "campaign Router currently requires same-repository push; "
            "fork delivery is not yet enabled"
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

    emit("loading_issue", f"Loading GitHub issue #{body.issue_number}")
    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    evidence = await asyncio.to_thread(
        _fetch_github_issue,
        repository,
        int(body.issue_number),
        token,
        research_module.urlopen,
    )
    prepared = _prepared_issue(evidence, tuple(body.modifiable_files))

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
    publisher = GitWorktreeCampaignPublisher(
        worktree,
        branch,
        research_module._run_process,
    )
    reviewer = _AgentReviewer(
        transport,
        str(body.reviewer_agent_id),
        from_agent=owner_agent_id,
        root_session_id=owner_session_id,
        emit=emit,
    )
    provider = GitHubChangeRequestProvider(
        _GitHubChangeRequestClient(
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
        environment={"worktree": str(worktree)},
        max_attempts=int(body.max_attempts),
        modifiable_files=tuple(body.modifiable_files),
    )
    emit("running", "Starting bounded Issue Campaign")
    outcome = await IssueCampaignRunner(
        _PreparedIssueService(prepared),
        LocalSubprocessRunner(),
    ).run(
        request,
        executor=executor,
        reviewer=reviewer,
        deliverer=deliverer,
    )
    return CampaignRuntimeResult(
        outcome=outcome,
        worktree=str(worktree),
        branch=branch,
        base_branch=base_branch,
    )


def _prepared_issue(
    evidence: IssueEvidence,
    modifiable_files: tuple[str, ...],
) -> PreparedIssueSolve:
    source_paths = tuple(
        path for path in modifiable_files if not path.startswith("test")
    )
    test_paths = tuple(
        path for path in modifiable_files if path.startswith("test")
    )
    issue = IssueTask(
        repository=evidence.repository,
        issue_number=evidence.number,
        title=evidence.title,
        description=evidence.body,
    )
    context = RepositoryContextPack(
        items=(),
        affected_paths=source_paths,
        test_paths=test_paths,
        estimated_tokens=0,
        truncated=False,
        graph_available=False,
        downgrade_reason="explicit_scope_approval",
    )
    plan = IssueSolvePlan(
        issue=issue,
        analysis="Explicitly approved campaign scope",
        implementation_steps=("implement the approved issue change",),
        validation_steps=("execute the Episode validation contract",),
    )
    return PreparedIssueSolve(
        evidence=evidence,
        contextual_plan=ContextualIssueSolvePlan(
            plan=plan,
            analysis=RepositoryAnalysis(
                anchors=(),
                candidate_nodes=(),
                affected_paths=source_paths,
                test_nodes=(),
                graph_available=False,
                downgrade_reason="explicit_scope_approval",
            ),
            context_pack=context,
            impact_set=ImpactSet(
                source_paths=source_paths,
                test_paths=test_paths,
                symbols=(),
                reasons=("explicit_scope_approval",),
            ),
            selected_tests=(),
        ),
    )


def _fetch_github_issue(
    repository: str,
    number: int,
    token: str | None,
    urlopen_func: Any,
) -> IssueEvidence:
    issue = _github_json_request(
        f"https://api.github.com/repos/{repository}/issues/{number}",
        token=token,
        method="GET",
        urlopen_func=urlopen_func,
    )
    if "pull_request" in issue:
        raise RuntimeError("campaign target is a pull request, not an issue")
    comments_raw = _github_json_request(
        f"https://api.github.com/repos/{repository}/issues/{number}/comments",
        token=token,
        method="GET",
        urlopen_func=urlopen_func,
    )
    comments = tuple(
        IssueComment(
            author=str((item.get("user") or {}).get("login", "unknown")),
            body=str(item.get("body") or ""),
            created_at=(
                str(item.get("created_at"))
                if item.get("created_at") is not None
                else None
            ),
        )
        for item in comments_raw
        if isinstance(item, dict)
    )
    labels = tuple(
        str(item.get("name", ""))
        if isinstance(item, dict)
        else str(item)
        for item in issue.get("labels", ())
    )
    return IssueEvidence(
        repository=repository,
        number=number,
        title=str(issue.get("title") or ""),
        body=str(issue.get("body") or ""),
        state=str(issue.get("state") or "unknown"),
        labels=tuple(item for item in labels if item),
        comments=comments,
    )


def _github_json_request(
    url: str,
    *,
    token: str | None,
    method: str,
    urlopen_func: Any,
    payload: dict[str, Any] | None = None,
) -> Any:
    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": "QwenPaw-AutoResearch",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"
    data = None
    if payload is not None:
        headers["Content-Type"] = "application/json"
        data = json.dumps(payload).encode("utf-8")
    request = UrlRequest(url, data=data, headers=headers, method=method)
    try:
        with urlopen_func(request, timeout=120) as response:
            raw = response.read()
    except HTTPError as exc:
        raise RuntimeError(
            f"GitHub API request failed with HTTP {exc.code} ({exc.reason})"
        ) from None
    except URLError as exc:
        raise RuntimeError(f"GitHub API request failed: {exc.reason}") from None
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise RuntimeError("GitHub API returned invalid JSON") from exc


def _extract_review_payload(text: str) -> dict[str, Any]:
    stripped = text.strip()
    fenced = re.findall(
        r"```(?:json)?\s*(\{.*?\})\s*```",
        stripped,
        flags=re.DOTALL | re.IGNORECASE,
    )
    candidates = [*reversed(fenced)]
    first = stripped.find("{")
    last = stripped.rfind("}")
    if first >= 0 and last > first:
        candidates.append(stripped[first : last + 1])
    for candidate in candidates:
        try:
            payload = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if not isinstance(payload, dict):
            continue
        verdict = str(payload.get("verdict", ""))
        if verdict not in {item.value for item in ReviewVerdict}:
            continue
        for key in ("findings", "required_changes"):
            value = payload.get(key, [])
            if not isinstance(value, list):
                raise RuntimeError(f"review field {key!r} must be an array")
        return payload
    raise RuntimeError("reviewer did not return a valid verdict JSON object")
