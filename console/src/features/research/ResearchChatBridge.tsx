import { useEffect, useMemo, type RefObject } from "react";
import type {
  IAgentScopeRuntimeWebUIMessage,
  IAgentScopeRuntimeWebUIRef,
} from "@agentscope-ai/chat";

import type {
  ResearchDialogState,
  ResearchRunState,
} from "@/api/modules/research";

import { useResearchStore } from "./researchStore";

export interface ResearchConversationEntry {
  id: string;
  role: "user" | "assistant";
  text: string;
  timestamp: string;
}

const thoughtPhases = new Set([
  "planning",
  "discovering",
  "searching",
  "extracting",
  "writing_judge",
  "generating",
  "retrying",
]);

function eventText(phase: string, detail: string): string {
  if (thoughtPhases.has(phase)) return `思考摘要 · ${detail || phase}`;
  if (phase === "discovery_completed") {
    return `调研结论 · ${detail || "已完成优化方向调研"}`;
  }
  if (phase === "failed") return `AutoResearch 失败 · ${detail}`;
  return `研究进度 · ${detail || phase}`;
}

// Hidden chain-of-thought is never exposed; only persisted phase summaries
// and model outputs intended for the user are projected into chat.
// eslint-disable-next-line react-refresh/only-export-components
export function projectResearchConversation(
  plan?: ResearchDialogState,
  snapshot?: ResearchRunState,
): ResearchConversationEntry[] {
  const sourceId = plan?.plan_id ?? snapshot?.id;
  const goal = plan?.goal ?? snapshot?.research_brief?.goal;
  if (!sourceId || !goal) return [];

  const events = plan?.events ?? snapshot?.events ?? [];
  return [
    {
      id: `research:${sourceId}:command`,
      role: "user",
      text: `/research ${goal}`,
      timestamp: plan?.created_at ?? snapshot?.created_at ?? "",
    },
    {
      id: `research:${sourceId}:accepted`,
      role: "assistant",
      text: "AutoResearch 已受理，正在调研并生成研究计划。",
      timestamp: plan?.created_at ?? snapshot?.created_at ?? "",
    },
    ...events.map((event, index) => ({
      id: `research:${sourceId}:event:${"sequence" in event ? event.sequence : `${event.timestamp}:${index}`}`,
      role: "assistant" as const,
      text: eventText(event.phase, event.detail),
      timestamp: event.timestamp,
    })),
  ];
}

function toChatMessage(
  entry: ResearchConversationEntry,
): IAgentScopeRuntimeWebUIMessage {
  const createdAt = Math.floor(
    (Date.parse(entry.timestamp) || Date.now()) / 1000,
  );
  if (entry.role === "user") {
    return {
      id: entry.id,
      role: "user",
      cards: [
        {
          code: "AgentScopeRuntimeRequestCard",
          data: {
            created_at: createdAt,
            input: [
              {
                role: "user",
                type: "message",
                content: [
                  { type: "text", text: entry.text, status: "created" },
                ],
              },
            ],
          },
        },
      ],
      msgStatus: "finished",
    };
  }

  return {
    id: entry.id,
    role: "assistant",
    cards: [
      {
        code: "AgentScopeRuntimeResponseCard",
        data: {
          id: `${entry.id}:response`,
          object: "response",
          status: "completed",
          created_at: createdAt,
          completed_at: createdAt,
          error: null,
          output: [
            {
              id: `${entry.id}:output`,
              role: "assistant",
              type: "message",
              content: [{ type: "text", text: entry.text }],
              metadata: { timestamp: entry.timestamp },
            },
          ],
        },
      },
    ],
    msgStatus: "finished",
  };
}

export function ResearchChatBridge({
  chatRef,
}: {
  chatRef: RefObject<IAgentScopeRuntimeWebUIRef | null>;
}) {
  const runId = useResearchStore((state) => state.activeRunId);
  const planId = useResearchStore((state) => state.activePlanId);
  const plan = useResearchStore((state) =>
    planId ? state.plans[planId] : undefined,
  );
  const snapshot = useResearchStore((state) =>
    runId ? state.snapshots[runId] : undefined,
  );
  const entries = useMemo(
    () => projectResearchConversation(plan, snapshot),
    [plan, snapshot],
  );

  useEffect(() => {
    const messagesApi = chatRef.current?.messages;
    if (!messagesApi || entries.length === 0) return;
    const current = messagesApi.getMessages() ?? [];
    const projected = entries.map(toChatMessage);
    const projectedIds = new Set(projected.map((item) => item.id));

    current
      .filter(
        (item) =>
          item.id.startsWith("research:") && !projectedIds.has(item.id),
      )
      .forEach((item) => messagesApi.removeMessage({ id: item.id }));

    projected.forEach((message) => {
      const existing = messagesApi.getMessage(message.id);
      if (
        !existing ||
        JSON.stringify(existing.cards) !== JSON.stringify(message.cards)
      ) {
        messagesApi.updateMessage(message);
      }
    });
  }, [chatRef, entries]);

  return null;
}
