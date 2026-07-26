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
  approveDialogPlan,
  rejectDialogPlan,
} = vi.hoisted(() => ({
  getRun: vi.fn(),
  cancelRun: vi.fn(),
  editDialogPlan: vi.fn(),
  approveDialogPlan: vi.fn(),
  rejectDialogPlan: vi.fn(),
}));

vi.mock("@/api", () => ({
  api: {
    getRun,
    cancelRun,
    editDialogPlan,
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
