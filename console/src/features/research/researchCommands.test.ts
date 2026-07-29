import { describe, expect, it } from "vitest";

import {
  isResearchPlanTerminal,
  parseResearchCommand,
} from "./researchCommands";

describe("parseResearchCommand", () => {
  it("parses a Chinese research goal", () => {
    expect(
      parseResearchCommand("/research 优化 submitFeedback 并发去重"),
    ).toEqual({
      action: "start",
      goal: "优化 submitFeedback 并发去重",
    });
  });

  it.each(["status", "open", "stop"] as const)("parses %s", (action) => {
    expect(parseResearchCommand(`/research ${action}`)).toEqual({
      action,
    });
  });

  it("parses explicit chat plan revision commands", () => {
    expect(
      parseResearchCommand(
        "/research revise 增加失败用例并重新运行 focused tests",
      ),
    ).toEqual({
      action: "revise",
      instruction: "增加失败用例并重新运行 focused tests",
    });
    expect(parseResearchCommand("/research accept")).toEqual({
      action: "accept_revision",
    });
    expect(parseResearchCommand("/research reject")).toEqual({
      action: "reject_revision",
    });
  });

  it("does not intercept ordinary chat", () => {
    expect(parseResearchCommand("帮我优化代码")).toBeNull();
  });

  it("treats rejected plans as terminal while keeping revisions active", () => {
    expect(isResearchPlanTerminal("rejected")).toBe(true);
    expect(isResearchPlanTerminal("completed")).toBe(true);
    expect(isResearchPlanTerminal("failed")).toBe(true);
    expect(isResearchPlanTerminal("cancelled")).toBe(true);
    expect(isResearchPlanTerminal("needs_revision")).toBe(false);
    expect(isResearchPlanTerminal("awaiting_approval")).toBe(false);
  });
});
