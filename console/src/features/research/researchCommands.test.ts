import { describe, expect, it } from "vitest";

import { parseResearchCommand } from "./researchCommands";

describe("parseResearchCommand", () => {
  it("parses a Chinese research goal", () => {
    expect(
      parseResearchCommand("/research 优化 submitFeedback 并发去重"),
    ).toEqual({
      action: "start",
      goal: "优化 submitFeedback 并发去重",
    });
  });

  it.each(["status", "open", "stop"] as const)(
    "parses %s",
    (action) => {
      expect(parseResearchCommand(`/research ${action}`)).toEqual({
        action,
      });
    },
  );

  it("does not intercept ordinary chat", () => {
    expect(parseResearchCommand("帮我优化代码")).toBeNull();
  });
});
