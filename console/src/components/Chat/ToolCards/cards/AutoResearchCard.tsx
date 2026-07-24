import React from "react";
import { ExperimentOutlined } from "@ant-design/icons";
import type { ToolCallContent } from "../shared/types";
import { DefaultBlock, ToolCardShell } from "../shared";
import { stringifyResult } from "../shared/utils";

type ResearchProgress = {
  task_id?: string;
  phase?: string;
  round?: number;
  completed_rounds?: number;
  rounds?: number;
};

function parseProgress(result: unknown): ResearchProgress | null {
  try {
    const parsed = JSON.parse(stringifyResult(result));
    return parsed?.type === "research_progress" ? parsed : null;
  } catch {
    return null;
  }
}

const AutoResearchCard: React.FC<{
  content: ToolCallContent;
  isStreaming?: boolean;
}> = ({ content, isStreaming }) => {
  const resultText = stringifyResult(content.result);
  const progress = parseProgress(content.result);
  const task = progress?.task_id || String(content.params.task_id || "");
  const currentRound = progress?.round || progress?.completed_rounds || 0;
  const inlineResult = progress
    ? `${currentRound}/${progress.rounds || 0} · ${progress.phase || progress.completed_rounds}`
    : undefined;

  return (
    <ToolCardShell
      content={content}
      isStreaming={isStreaming}
      icon={<ExperimentOutlined />}
      title={task ? `AutoResearch · ${task}` : "AutoResearch"}
      inlineResult={inlineResult}
    >
      {resultText && <DefaultBlock title="Research trace" content={resultText} />}
    </ToolCardShell>
  );
};

export default AutoResearchCard;
