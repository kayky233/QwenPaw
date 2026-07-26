import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { resetResearchStoreForTests, useResearchStore } from "./researchStore";
import { ResearchSidePanel } from "./ResearchSidePanel";

const { editDialogPlan, approveDialogPlan } = vi.hoisted(() => ({
  editDialogPlan: vi.fn(),
  approveDialogPlan: vi.fn(),
}));

vi.mock("@/api", () => ({
  api: {
    editDialogPlan,
    approveDialogPlan,
    dialogStatus: vi.fn().mockRejectedValue(new Error("stop polling")),
  },
}));

vi.mock("@/api/sse", () => ({
  SseHttpError: class extends Error {},
  streamJsonSse: vi.fn(),
}));

describe("ResearchSidePanel approval", () => {
  beforeEach(() => {
    resetResearchStoreForTests();
    vi.clearAllMocks();
    useResearchStore.getState().openPlan("plan-1", "Fix issue 6470");
    useResearchStore.getState().updatePlan({
      ...useResearchStore.getState().plans["plan-1"],
      status: "awaiting_approval",
      plan_markdown: "# Complete plan\n\nRun focused tests.",
      revision: 1,
      content_hash: "a".repeat(64),
    });
  });

  it("edits, saves, and approves the complete plan", async () => {
    editDialogPlan.mockResolvedValue({
      ...useResearchStore.getState().plans["plan-1"],
      status: "awaiting_approval",
      plan_markdown: "# Edited plan\n\nRun focused tests.",
      revision: 2,
      content_hash: "b".repeat(64),
    });
    approveDialogPlan.mockResolvedValue({
      ...useResearchStore.getState().plans["plan-1"],
      status: "approved",
      plan_markdown: "# Edited plan\n\nRun focused tests.",
      revision: 2,
      content_hash: "b".repeat(64),
      approved_revision: 2,
      approved_content_hash: "b".repeat(64),
    });

    render(<ResearchSidePanel mode="side-panel" />);

    const editor = screen.getByRole("textbox", { name: "研究方案" });
    expect(editor).toHaveValue("# Complete plan\n\nRun focused tests.");
    fireEvent.change(editor, {
      target: { value: "# Edited plan\n\nRun focused tests." },
    });
    fireEvent.click(screen.getByRole("button", { name: "保存方案" }));

    await waitFor(() => expect(editDialogPlan).toHaveBeenCalledOnce());
    fireEvent.click(screen.getByRole("button", { name: "批准执行" }));

    await waitFor(() => expect(approveDialogPlan).toHaveBeenCalledOnce());
  });
});
