import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { resetResearchStoreForTests, useResearchStore } from "./researchStore";
import { ResearchSidePanel } from "./ResearchSidePanel";

const {
  editDialogPlan,
  approveDialogPlan,
  acceptDialogPlanRevision,
  rejectDialogPlanRevision,
} = vi.hoisted(() => ({
  editDialogPlan: vi.fn(),
  approveDialogPlan: vi.fn(),
  acceptDialogPlanRevision: vi.fn(),
  rejectDialogPlanRevision: vi.fn(),
}));

vi.mock("@/api", () => ({
  api: {
    editDialogPlan,
    approveDialogPlan,
    acceptDialogPlanRevision,
    rejectDialogPlanRevision,
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

  it("shows an environment mismatch and blocks approval", () => {
    useResearchStore.getState().updatePlan({
      ...useResearchStore.getState().plans["plan-1"],
      environment_compatibility: "incompatible",
      current_environment: "macOS (arm64)",
      environment_compatibility_reason:
        "计划要求 Windows，但当前执行环境是 macOS (arm64)",
    });

    render(<ResearchSidePanel mode="side-panel" />);

    expect(screen.getByText("环境不兼容")).toBeInTheDocument();
    expect(
      screen.getByText("计划要求 Windows，但当前执行环境是 macOS (arm64)"),
    ).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "批准执行" })).toBeDisabled();
    expect(approveDialogPlan).not.toHaveBeenCalled();
  });

  it("opens a large review surface with the goal and editable plan", () => {
    render(<ResearchSidePanel mode="side-panel" />);

    fireEvent.click(screen.getByRole("button", { name: "全屏审阅计划" }));

    const dialog = screen.getByRole("dialog", { name: "审阅研究计划" });
    expect(dialog).toHaveTextContent("研究意图");
    expect(dialog).toHaveTextContent("Fix issue 6470");
    const expandedEditor = screen.getByRole("textbox", {
      name: "全屏研究方案",
    });
    expect(expandedEditor).toHaveValue("# Complete plan\n\nRun focused tests.");

    fireEvent.change(expandedEditor, {
      target: { value: "# Expanded plan\n\nVerify the actual behavior." },
    });
    expect(screen.getByRole("textbox", { name: "研究方案" })).toHaveValue(
      "# Expanded plan\n\nVerify the actual behavior.",
    );
  });

  it("shows preserved validation evidence and allows plan revision", () => {
    useResearchStore.getState().updatePlan({
      ...useResearchStore.getState().plans["plan-1"],
      status: "needs_revision",
      error: "计划需要补充本次变更路径后重新审批",
      changed_paths: ["src/fix.py", "tests/skills/test_fix.py"],
      unapproved_paths: ["tests/skills/test_fix.py"],
      validation_failure_category: "scope_mismatch",
      validation_report: "# Validation attempt 1\n\nScope mismatch.",
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
          validation_report: "# Validation attempt 1\n\nScope mismatch.",
        },
      ],
    });

    render(<ResearchSidePanel mode="side-panel" />);

    expect(screen.getByText("需要修订计划")).toBeInTheDocument();
    expect(screen.getByText("tests/skills/test_fix.py")).toBeInTheDocument();
    expect(screen.getByText(/Validation attempt 1/)).toBeInTheDocument();
    expect(
      screen.getByRole("textbox", { name: "研究方案" }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "重新审批并继续" }),
    ).toBeInTheDocument();
  });

  it("shows the latest approval error instead of hiding it behind an old plan error", async () => {
    approveDialogPlan.mockRejectedValue(
      new Error("Revise the plan before continuing"),
    );
    useResearchStore.getState().updatePlan({
      ...useResearchStore.getState().plans["plan-1"],
      status: "needs_revision",
      error: "计划需要补充本次变更路径后重新审批",
    });

    render(<ResearchSidePanel mode="side-panel" />);

    fireEvent.click(screen.getByRole("button", { name: "重新审批并继续" }));

    await waitFor(() =>
      expect(
        screen.getByText("Revise the plan before continuing"),
      ).toBeInTheDocument(),
    );
  });

  it("lets the user accept or reject a proposed plan revision", async () => {
    const current = useResearchStore.getState().plans["plan-1"];
    const proposedPlan = "# Revised plan\n\nAdd focused regression coverage.";
    useResearchStore.getState().updatePlan({
      ...current,
      status: "needs_revision",
      revision_proposal: proposedPlan,
      revision_proposal_reason: "根据最近一次验证反馈生成",
      revision_proposal_revision: 1,
    });
    acceptDialogPlanRevision.mockResolvedValue({
      ...current,
      status: "awaiting_approval",
      plan_markdown: proposedPlan,
      revision: 2,
      revision_proposal: "",
      revision_proposal_reason: "",
      revision_proposal_revision: null,
    });

    render(<ResearchSidePanel mode="side-panel" />);

    expect(screen.getByText("建议修订")).toBeInTheDocument();
    expect(screen.getByText("根据最近一次验证反馈生成")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "接受修订" }));

    await waitFor(() =>
      expect(acceptDialogPlanRevision).toHaveBeenCalledWith("plan-1", {
        expected_revision: 1,
      }),
    );
    expect(rejectDialogPlanRevision).not.toHaveBeenCalled();
  });

  it("keeps an earlier failed report visible after a later validation", () => {
    useResearchStore.getState().updatePlan({
      ...useResearchStore.getState().plans["plan-1"],
      status: "completed",
      validation_report: "# Validation attempt 2\n\nPassed.",
      validation_attempts: [
        {
          attempt: 1,
          revision: 1,
          timestamp: "2026-01-01T00:00:00Z",
          failure_category: "scope_mismatch",
          changed_paths: ["tests/skills/test_fix.py"],
          unapproved_paths: ["tests/skills/test_fix.py"],
          reproduction_status: "pending",
          reproduction_summary: "",
          verification_status: "blocked",
          verification_summary: "Plan revision required.",
          validation_report: "# Validation attempt 1\n\nScope mismatch.",
        },
        {
          attempt: 2,
          revision: 2,
          timestamp: "2026-01-01T00:05:00Z",
          failure_category: "",
          changed_paths: ["tests/skills/test_fix.py"],
          unapproved_paths: [],
          reproduction_status: "reproduced",
          reproduction_summary: "Baseline failed.",
          verification_status: "passed",
          verification_summary: "Candidate passed.",
          validation_report: "# Validation attempt 2\n\nPassed.",
        },
      ],
    });

    render(<ResearchSidePanel mode="side-panel" />);

    expect(screen.getByText("历史验证尝试")).toBeInTheDocument();
    expect(
      screen.getByText(/尝试 1 · revision 1 · scope_mismatch/),
    ).toBeInTheDocument();
    expect(screen.getByText(/Validation attempt 1/)).toBeInTheDocument();
    expect(screen.getByText(/Validation attempt 2/)).toBeInTheDocument();
  });

  it("explains how to continue after rejecting the whole plan", () => {
    useResearchStore.getState().updatePlan({
      ...useResearchStore.getState().plans["plan-1"],
      status: "rejected",
      error: "计划需要补充本次变更路径后重新审批",
      rejection_reason: "用户放弃本次研究",
    });

    render(<ResearchSidePanel mode="side-panel" />);

    expect(screen.getByText("本次研究已结束")).toBeInTheDocument();
    expect(screen.getByText(/\/research <目标>/)).toBeInTheDocument();
    expect(
      screen.queryByText("计划需要补充本次变更路径后重新审批"),
    ).not.toBeInTheDocument();
  });
});
