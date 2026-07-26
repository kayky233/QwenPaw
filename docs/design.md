# AutoResearch Chat-Mode Design

## Goal

Make AutoResearch a supervised long-running task mode started and controlled
from chat. Keep `/auto-research` as a full detail view, not the primary entry.

## Current Runtime

- Each round runs `PLAN -> PROGRAM -> JUDGE -> REVIEW`.
- Dialog research first produces a validated `ResearchBrief`.
- GitHub issue dialog research stops after producing `program.md` and enters
  `awaiting_approval`; it does not generate a synthetic `solution.py` or start
  the legacy single-file evaluator before approval.
- Candidate writes are atomic and judge execution uses best-effort isolation.
- Run snapshots, briefs, events, and outcomes are restored from the Research
  Ledger after restart.
- Chat and the side panel share one authenticated, sequence-aware run stream.

## Required Flow

```text
chat command
-> discovery / ResearchBrief
-> repository-level proposal
-> awaiting user approval
-> existing planner and agent runtime (execution phase)
-> iterative research rounds
-> durable ledger snapshot + ordered events
-> shared frontend store
-> chat milestones + side panel + detail page
```

## Constraints

- Snapshot is the state source of truth; SSE events are ordered increments.
- Connection state must not overwrite run state.
- One browser tab opens at most one connection per run.
- Fatal outcomes never submit artifacts, create PRs, or become `completed`.
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
- Approval-triggered repository execution remains deferred until the runtime
  has a durable repository/worktree checkpoint; the UI must not pretend that
  approval already changed source files.
- Optional GitHub PR finalization remains supported after successful runs.
