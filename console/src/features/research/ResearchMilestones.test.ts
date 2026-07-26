import { describe, expect, it } from "vitest";

import type {
  ResearchDialogState,
  ResearchRunState,
} from "@/api/modules/research";

import {
  projectResearchMilestones,
  projectResearchPlanMilestones,
} from "./ResearchMilestones";

describe("projectResearchMilestones", () => {
  it("keeps discovery and major run events but omits internal noise", () => {
    const snapshot = {
      research_brief: {
        candidate_directions: [
          { title: "Database uniqueness", priority: 1 },
        ],
      },
      events: [
        { phase: "candidate.generated", detail: "noise", sequence: 1 },
        { phase: "planning", detail: "Plan ready", sequence: 2 },
        { phase: "kept", detail: "Score improved", sequence: 3 },
      ],
    } as ResearchRunState;

    expect(projectResearchMilestones(snapshot).map((item) => item.text)).toEqual([
      "调研方向：Database uniqueness",
      "Plan ready",
      "Score improved",
    ]);
  });

  it("shows immediate discovery progress before a run exists", () => {
    const plan = {
      status: "planning",
      goal: "Fix duplicate writes",
      events: [
        {
          phase: "discovery",
          detail: "正在分析相关实现与测试",
          timestamp: "2026-07-26T00:00:00Z",
        },
      ],
    } as ResearchDialogState;

    expect(projectResearchPlanMilestones(plan).map((item) => item.text)).toEqual([
      "已受理：Fix duplicate writes",
      "正在分析相关实现与测试",
    ]);
  });
});
