export type ResearchCommand =
  | { action: "start"; goal: string }
  | { action: "revise"; instruction: string }
  | { action: "accept_revision" | "reject_revision" }
  | { action: "status" | "open" | "stop" }
  | { action: "help" };

const TERMINAL_RESEARCH_PLAN_STATUSES = new Set([
  "completed",
  "failed",
  "cancelled",
  "rejected",
]);

export function isResearchPlanTerminal(status: string): boolean {
  return TERMINAL_RESEARCH_PLAN_STATUSES.has(status);
}

export function parseResearchCommand(input: string): ResearchCommand | null {
  const trimmed = input.trim();
  if (!trimmed.toLowerCase().startsWith("/research")) return null;
  const rest = trimmed.slice("/research".length).trim();
  if (!rest) return { action: "help" };
  const action = rest.toLowerCase();
  if (action === "status" || action === "open" || action === "stop") {
    return { action };
  }
  if (action === "accept") return { action: "accept_revision" };
  if (action === "reject") return { action: "reject_revision" };
  if (action === "revise") return { action: "revise", instruction: "" };
  if (action.startsWith("revise ")) {
    return {
      action: "revise",
      instruction: rest.slice("revise".length).trim(),
    };
  }
  return { action: "start", goal: rest };
}
