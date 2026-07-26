import { describe, expect, it } from "vitest";

import type { ResearchDialogState } from "@/api/modules/research";

import { projectResearchConversation } from "./ResearchChatBridge";

describe("projectResearchConversation", () => {
  it("projects the command and progress as normal chat turns", () => {
    const plan = {
      plan_id: "plan-1",
      status: "discovering",
      goal: "处理一个简单 issue",
      events: [
        {
          phase: "discovering",
          detail: "正在分析真实 issue 和相关代码",
          timestamp: "2026-07-26T00:00:00Z",
        },
      ],
    } as ResearchDialogState;

    expect(projectResearchConversation(plan, undefined)).toEqual([
      expect.objectContaining({
        id: "research:plan-1:command",
        role: "user",
        text: "/research 处理一个简单 issue",
      }),
      expect.objectContaining({
        role: "assistant",
        text: "AutoResearch 已受理，正在调研并生成研究计划。",
      }),
      expect.objectContaining({
        role: "assistant",
        text: "思考摘要 · 正在分析真实 issue 和相关代码",
      }),
    ]);
  });
});
