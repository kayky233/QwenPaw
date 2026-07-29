import { beforeEach, describe, expect, it, vi } from "vitest";

import type { ResearchRunState } from "@/api/modules/research";

import {
  reduceResearchMessage,
  resetResearchStoreForTests,
  useResearchStore,
} from "./researchStore";

const {
  getRun,
  cancelRun,
  editDialogPlan,
  proposeDialogPlanRevision,
  acceptDialogPlanRevision,
  rejectDialogPlanRevision,
  approveDialogPlan,
  rejectDialogPlan,
} = vi.hoisted(() => ({
  getRun: vi.fn(),
  cancelRun: vi.fn(),
  editDialogPlan: vi.fn(),
  proposeDialogPlanRevision: vi.fn(),
  acceptDialogPlanRevision: vi.fn(),
  rejectDialogPlanRevision: vi.fn(),
  approveDialogPlan: vi.fn(),
  rejectDialogPlan: vi.fn(),
}));

vi.mock("@/api", () => ({
  api: {
    getRun,
    cancelRun,
    editDialogPlan,
    proposeDialogPlanRevision,
    acceptDialogPlanRevision,
    rejectDialogPlanRevision,
    approveDialogPlan,
    rejectDialogPlan,
  },
}));

vi.mock("@/api/sse", () => ({
  SseHttpError: class extends Error {},
  streamJsonSse: vi.fn(),
}));

function snapshot(status = "completed"): ResearchRunState {
  return {
    id: "run-1",
    task_id: "task-1",
    agent_id: "default",
    status,
    rounds: 3,
    completed_rounds: 0,
    outcomes: [],
    created_at: "2026-01-01T00:00:00Z",
    events: [],
    current_round: null,
    phase: status,
    updated_at: "2026-01-01T00:00:00Z",
    started_at: null,
    finished_at: null,
    error: "",
  };
}

describe("researchStore", () => {
  beforeEach(() => {
    resetResearchStoreForTests();
    vi.clearAllMocks();
  });

  it("opens a run from a backend snapshot", async () => {
    getRun.mockResolvedValue(snapshot());

    await useResearchStore.getState().openRun("run-1");

    const state = useResearchStore.getState();
    expect(state.activeRunId).toBe("run-1");
    expect(state.panelOpen).toBe(true);
    expect(state.snapshots["run-1"].status).toBe("completed");
  });

  it("opens planning immediately before a run exists", () => {
    useResearchStore.getState().openPlan("plan-1", "Fix duplicate writes");

    const state = useResearchStore.getState();
    expect(state.activePlanId).toBe("plan-1");
    expect(state.activeRunId).toBeNull();
    expect(state.panelOpen).toBe(true);
    expect(state.plans["plan-1"].status).toBe("accepted");
    expect(state.plans["plan-1"].reproduction_status).toBe("pending");
    expect(state.plans["plan-1"].verification_status).toBe("pending");
    expect(state.plans["plan-1"].validation_report).toBe("");
  });

  it("persists the active plan id for refresh recovery", () => {
    useResearchStore.getState().openPlan("plan-recover", "Fix issue 6470");

    const persisted = JSON.parse(
      localStorage.getItem("qwenpaw-active-research") ?? "{}",
    ) as { state?: { activePlanId?: string } };

    expect(persisted.state?.activePlanId).toBe("plan-recover");
  });

  it("saves and approves the exact current plan revision", async () => {
    useResearchStore.getState().openPlan("plan-1", "Fix issue 6470");
    useResearchStore.getState().updatePlan({
      ...useResearchStore.getState().plans["plan-1"],
      status: "awaiting_approval",
      plan_markdown: "# Initial plan",
      revision: 1,
      content_hash: "a".repeat(64),
    });
    editDialogPlan.mockResolvedValue({
      ...useResearchStore.getState().plans["plan-1"],
      status: "awaiting_approval",
      plan_markdown: "# Edited plan",
      revision: 2,
      content_hash: "b".repeat(64),
    });
    approveDialogPlan.mockResolvedValue({
      ...useResearchStore.getState().plans["plan-1"],
      status: "approved",
      plan_markdown: "# Edited plan",
      revision: 2,
      content_hash: "b".repeat(64),
      approved_revision: 2,
      approved_content_hash: "b".repeat(64),
    });

    await useResearchStore.getState().savePlan("plan-1", "# Edited plan");
    await useResearchStore.getState().approvePlan("plan-1");

    expect(editDialogPlan).toHaveBeenCalledWith("plan-1", {
      plan_markdown: "# Edited plan",
      expected_revision: 1,
    });
    expect(approveDialogPlan).toHaveBeenCalledWith(
      "plan-1",
      expect.objectContaining({
        expected_revision: 2,
        content_hash: "b".repeat(64),
      }),
    );
    expect(useResearchStore.getState().plans["plan-1"].status).toBe("approved");
  });

  it("keeps validation evidence while revising a recoverable failure", async () => {
    useResearchStore.getState().openPlan("plan-1", "Fix issue 6470");
    useResearchStore.getState().updatePlan({
      ...useResearchStore.getState().plans["plan-1"],
      status: "needs_revision",
      plan_markdown: "# Initial plan",
      revision: 1,
      content_hash: "a".repeat(64),
      changed_paths: ["src/fix.py", "tests/skills/test_fix.py"],
      unapproved_paths: ["tests/skills/test_fix.py"],
      validation_failure_category: "scope_mismatch",
      validation_report: "# Validation attempt 1",
      validation_attempts: [
        {
          attempt: 1,
          revision: 1,
          timestamp: "2026-01-01T00:00:00Z",
          failure_category: "scope_mismatch",
          changed_paths: ["src/fix.py", "tests/skills/test_fix.py"],
          unapproved_paths: ["tests/skills/test_fix.py"],
          reproduction_status: "pending",
          reproduction_summary: "",
          verification_status: "blocked",
          verification_summary: "Plan revision required.",
          validation_report: "# Validation attempt 1",
        },
      ],
    });
    editDialogPlan.mockResolvedValue({
      ...useResearchStore.getState().plans["plan-1"],
      status: "awaiting_approval",
      plan_markdown: "# Revised plan",
      revision: 2,
      content_hash: "b".repeat(64),
    });

    await useResearchStore.getState().savePlan("plan-1", "# Revised plan");

    const plan = useResearchStore.getState().plans["plan-1"];
    expect(plan.status).toBe("awaiting_approval");
    expect(plan.validation_report).toBe("# Validation attempt 1");
    expect(plan.validation_attempts).toHaveLength(1);
    expect(plan.unapproved_paths).toEqual(["tests/skills/test_fix.py"]);
  });

  it("keeps revision proposals separate from execution approval", async () => {
    useResearchStore.getState().openPlan("plan-1", "Fix issue 6470");
    const initial = {
      ...useResearchStore.getState().plans["plan-1"],
      status: "needs_revision",
      plan_markdown: "# Initial plan",
      revision: 1,
      content_hash: "a".repeat(64),
      validation_report: "# Preserved validation",
    };
    useResearchStore.getState().updatePlan(initial);

    proposeDialogPlanRevision.mockResolvedValue({
      ...initial,
      revision_proposal: "# Revised plan",
      revision_proposal_reason: "根据最近一次验证反馈生成",
      revision_proposal_revision: 1,
    });
    acceptDialogPlanRevision.mockResolvedValue({
      ...initial,
      status: "awaiting_approval",
      plan_markdown: "# Revised plan",
      revision: 2,
      content_hash: "b".repeat(64),
      revision_proposal: "",
      revision_proposal_reason: "",
      revision_proposal_revision: null,
    });

    await useResearchStore.getState().proposePlanRevision("plan-1", "");
    expect(useResearchStore.getState().plans["plan-1"].plan_markdown).toBe(
      "# Initial plan",
    );

    await useResearchStore.getState().acceptPlanRevision("plan-1");

    const accepted = useResearchStore.getState().plans["plan-1"];
    expect(accepted.plan_markdown).toBe("# Revised plan");
    expect(accepted.status).toBe("awaiting_approval");
    expect(accepted.approved_revision).not.toBe(2);
    expect(accepted.validation_report).toBe("# Preserved validation");
    expect(approveDialogPlan).not.toHaveBeenCalled();
  });

  it("rejects a revision proposal without changing the current plan", async () => {
    useResearchStore.getState().openPlan("plan-1", "Fix issue 6470");
    const proposed = {
      ...useResearchStore.getState().plans["plan-1"],
      status: "needs_revision",
      plan_markdown: "# Initial plan",
      revision: 1,
      content_hash: "a".repeat(64),
      validation_report: "# Preserved validation",
      revision_proposal: "# Unwanted revision",
      revision_proposal_reason: "自动建议",
      revision_proposal_revision: 1,
    };
    useResearchStore.getState().updatePlan(proposed);
    rejectDialogPlanRevision.mockResolvedValue({
      ...proposed,
      revision_proposal: "",
      revision_proposal_reason: "",
      revision_proposal_revision: null,
    });

    await useResearchStore.getState().rejectPlanRevision("plan-1");

    const rejected = useResearchStore.getState().plans["plan-1"];
    expect(rejected.plan_markdown).toBe("# Initial plan");
    expect(rejected.revision).toBe(1);
    expect(rejected.validation_report).toBe("# Preserved validation");
    expect(approveDialogPlan).not.toHaveBeenCalled();
  });

  it("rejects through the research plan API", async () => {
    useResearchStore.getState().openPlan("plan-1", "Fix issue 6470");
    useResearchStore.getState().updatePlan({
      ...useResearchStore.getState().plans["plan-1"],
      status: "awaiting_approval",
      revision: 1,
      content_hash: "a".repeat(64),
    });
    rejectDialogPlan.mockResolvedValue({
      ...useResearchStore.getState().plans["plan-1"],
      status: "rejected",
      revision: 1,
      content_hash: "a".repeat(64),
      rejection_reason: "Need a smaller issue",
    });

    await useResearchStore
      .getState()
      .rejectPlan("plan-1", "Need a smaller issue");

    expect(rejectDialogPlan).toHaveBeenCalledWith("plan-1", {
      expected_revision: 1,
      reason: "Need a smaller issue",
    });
    expect(useResearchStore.getState().plans["plan-1"].status).toBe("rejected");
  });

  it("does not fake cancelled before the backend confirms it", async () => {
    cancelRun.mockResolvedValue(snapshot("running"));

    await useResearchStore.getState().stopRun("run-1");

    expect(useResearchStore.getState().snapshots["run-1"].status).toBe(
      "running",
    );
  });

  it("deduplicates live events by sequence", () => {
    const initial = {
      ...snapshot("running"),
      events: [
        {
          phase: "planning",
          timestamp: "2026-01-01T00:00:00Z",
          round: 1,
          detail: "",
          sequence: 3,
        },
      ],
    };

    const duplicate = reduceResearchMessage(initial, {
      type: "event",
      phase: "judging",
      sequence: 3,
    });
    const fresh = reduceResearchMessage(initial, {
      type: "event",
      phase: "judging",
      timestamp: "2026-01-01T00:00:01Z",
      sequence: 4,
    });

    expect(duplicate).toBe(initial);
    expect(fresh.events).toHaveLength(2);
    expect(fresh.phase).toBe("judging");
  });
});
