# AutoResearch Chat-Mode Design

## Goal

Make AutoResearch a supervised long-running task mode started and controlled
from chat. Keep `/auto-research` as a full detail view, not the primary entry.

## Environment-Compatible Approval Requirement

- **Problem:** issue triage could select a platform-specific report that the
  current host cannot reproduce, producing a test-only PR instead of a fix.
- **Goal:** present the selected issue and complete plan for explicit user
  approval, while preventing execution when its required platform or artifact
  is unavailable on the current host.
- **Non-goal:** emulate Windows installers, drivers, hardware, or foreign
  operating systems from a macOS/Linux host.
- **Constraints:** plan revisions remain editable; approval stays bound to the
  exact revision and content hash; environment rejection cannot be bypassed by
  setting dialog state directly.
- **Acceptance:** Discovery receives the current host environment, incompatible
  issues are excluded from ranking, the side panel explains compatibility,
  and both approval and execution reject an explicit platform mismatch.
- **Risks:** incomplete issue metadata may yield `unknown`; that state is shown
  for manual review, while explicit mismatches are hard-blocked.
- **Dependencies:** issue evidence, the plan's `Reproduction Environment`
  section, authenticated approval APIs, and the existing Research side panel.

## Recoverable Validation Requirement

- **Problem:** a validation gate such as an unapproved changed path previously
  collapsed the dialog into terminal `failed`, discarded structured evidence,
  and made the preserved worktree impossible to resume.
- **Goal:** retain every validation attempt and let the user revise and
  reapprove the exact plan before continuing from the existing worktree.
- **Non-goal:** automatically widen approved file scope or bypass environment,
  authentication, sandbox, Git, or GitHub failures.
- **Constraints:** scope mismatches require a new plan revision and content
  hash; no recoverable failure may commit, push, or create a pull request.
- **Acceptance:** recoverable validation returns `needs_revision`, exposes
  changed and unapproved paths, preserves the report, events, branch, worktree,
  and runtime context, and appends subsequent attempts without deleting prior
  evidence.
- **Risks:** worktree recovery is process-resident until dialog state moves
  into the Research Ledger; a missing preserved worktree fails closed.
- **Dependencies:** the existing plan editor and approval hash, dialog polling,
  confined execution prompt, and validation report UI.

## Supervised Plan Revision Requirement

- **Problem:** recoverable validation currently sends users back to a large
  Markdown editor without proposing a concrete correction; repeating approval
  on the same revision returns a hidden conflict and chat cannot revise plans.
- **Goal:** turn validation feedback into a non-binding revision proposal that
  the user can accept or reject, then keep execution approval as a separate
  explicit decision. Allow the same proposal decisions from chat commands.
- **Non-goal:** automatically approve execution, silently widen file scope, or
  treat arbitrary chat text as an approval.
- **Constraints:** proposals are bound to the current revision; accepting a
  proposal creates a new revision and content hash but does not approve it;
  rejecting preserves the current plan and all validation evidence.
- **Acceptance:** recoverable failures expose a suggested plan, the side panel
  offers accept/reject controls, `/research revise`, `/research accept`, and
  `/research reject` operate on the active plan, and approval remains a
  separate action after an accepted revision.
- **Risks:** deterministic suggestions may need further user editing; stale
  proposal decisions must fail closed when the underlying revision changes.
- **Dependencies:** dialog ownership, revision/hash concurrency checks,
  researchStore, the chat command interceptor, and preserved validation data.

## Current Runtime

- Each round runs `PLAN -> PROGRAM -> JUDGE -> REVIEW`.
- Dialog research first produces a validated `ResearchBrief`.
- GitHub issue dialog research stops after producing `program.md` and enters
  `awaiting_approval`; it does not generate a synthetic `solution.py` or start
  the legacy single-file evaluator before approval.
- Plans are editable and approval is bound to an exact revision, SHA-256
  digest, actor, timestamp, and idempotency key.
- Discovery is host-aware. Plans expose `compatible`, `incompatible`, or
  `unknown` environment status; explicit platform mismatches cannot be
  approved or executed.
- Dialog ownership uses authenticated server-side identity. A legacy client
  `user_id` field is accepted for compatibility but never trusted for
  authorization.
- Approved GitHub work runs in a dedicated Git worktree. File tools are
  confined to that worktree, the implementation Agent has no shell tool, and
  host-run regression tests execute with a credential-free environment,
  network disabled, and deny-default filesystem access.
- Validation rejects files not explicitly listed in the approved plan, then
  commits and pushes a dedicated `autoresearch/issue-*` branch.
- Recoverable validation outcomes use `needs_revision`. The side panel keeps
  the current and historical reports, exposes offending paths, and requires a
  revised approval for scope changes before reusing the same worktree.
- Recoverable validation produces a revision proposal without changing the
  approved plan. Accepting or rejecting the proposal is distinct from
  approving execution, and the same actions are available through explicit
  `/research` chat commands.
- Process-resident legacy scope failures are upgraded on read to
  `needs_revision` when their preserved worktree is still available.
- Repository validation is evidence-gated: focused regression tests are first
  overlaid onto the approved baseline ref and must reproduce the reported
  failure, then the same tests must pass in the candidate worktree. A passing
  baseline is classified as `not_reproduced` and cannot be presented as a fix.
- Every repository run produces a Markdown validation report containing the
  requested environment, baseline ref, changed files, reproduction command and
  output, candidate verification command and output, conclusion, and sandbox
  limitations. The report is returned in the dialog snapshot and downloadable
  from the Research side panel.
- Repository execution records the upstream issue repository separately from
  the local `origin` fork used for branch pushes. For this project the target
  is `agentscope-ai/QwenPaw` and the push fork is `kayky233/QwenPaw`.
- Candidate writes are atomic and judge execution uses best-effort isolation.
- Run snapshots, briefs, events, and outcomes are restored from the Research
  Ledger after restart.
- Chat and the side panel share one authenticated, sequence-aware run stream.

## Required Flow

```text
chat command
-> discovery / ResearchBrief
-> repository-level proposal
-> host environment compatibility check
-> awaiting explicit user approval
-> confined repository implementation
-> sandboxed baseline reproduction (expected failure)
-> sandboxed candidate verification (expected pass)
-> reproducibility and fix-validation report
-> commit + fork branch push
-> pull request finalization
-> durable ledger snapshot + ordered events
-> shared frontend store
-> chat milestones + side panel + detail page
```

## Constraints

- Snapshot is the state source of truth; SSE events are ordered increments.
- Connection state must not overwrite run state.
- One browser tab opens at most one connection per run.
- Fatal outcomes never submit artifacts, create PRs, or become `completed`.
- `not_reproduced`, reproduction infrastructure errors, and candidate test
  failures never commit or push repository changes. Scope mismatches,
  `not_reproduced`, and ordinary candidate failures are recoverable; explicit
  environment, authentication, sandbox infrastructure, Git, and GitHub
  failures remain terminal.
- Approval does not grant unconstrained shell or host filesystem access.
- Judge isolation remains explicit opt-in because it is not a security boundary.
- Existing `/auto-research?taskId=...&runId=...` links remain compatible.

## Phases

1. Runtime stabilization and clean-clone verification.
2. Ordered SSE recovery and Research Ledger integration.
3. Shared `researchStore`, connection manager, and reusable side panel.
4. Chat commands: start, status, open, stop.
5. Discovery and structured `ResearchBrief`.
6. History recovery and safe runtime controls.

## Design State

- External benchmark/LeetCode submission is **not implemented** in the committed
  runtime. It may return after a concrete adapter and tests exist.
- Chat supports `/research <goal>`, `status`, `open`, and `stop`.
- Discovery, shared run state, the side panel, and milestone projection are
  implemented.
- The proposal is persisted and shown in chat/the side panel while the dialog
  waits for approval.
- Active-run and deep-link recovery are implemented through persisted run IDs,
  Ledger snapshots, and sequence-aware SSE replay.
- Approval-triggered repository execution, focused tests, commit, and branch
  push are implemented.
- When `auto_pr` is enabled, the runtime creates the upstream pull request
  after pushing the fork branch. The complete validation report is supplied
  as the PR body. It uses an authenticated GitHub CLI when available, then
  falls back to the GitHub REST API with `GITHUB_TOKEN` or `GH_TOKEN`. Tokens
  are sent only in the authorization header and are excluded from process
  arguments, logs, reports, and surfaced errors. PR creation failure keeps the
  dialog out of `completed`.
- Dialog approval state is currently process-resident. Browser refresh can
  recover an active plan ID, but server-restart rehydration into the Research
  Ledger remains the next durability phase.
