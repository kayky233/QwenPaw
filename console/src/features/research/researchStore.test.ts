import { beforeEach, describe, expect, it, vi } from "vitest";

import type { ResearchRunState } from "@/api/modules/research";

import {
  reduceResearchMessage,
  resetResearchStoreForTests,
  useResearchStore,
} from "./researchStore";

const { getRun, cancelRun } = vi.hoisted(() => ({
  getRun: vi.fn(),
  cancelRun: vi.fn(),
}));

vi.mock("@/api", () => ({
  api: { getRun, cancelRun },
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
