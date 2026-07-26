export type ResearchCommand =
  | { action: "start"; goal: string }
  | { action: "status" | "open" | "stop" }
  | { action: "help" };

export function parseResearchCommand(input: string): ResearchCommand | null {
  const trimmed = input.trim();
  if (!trimmed.toLowerCase().startsWith("/research")) return null;
  const rest = trimmed.slice("/research".length).trim();
  if (!rest) return { action: "help" };
  const action = rest.toLowerCase();
  if (action === "status" || action === "open" || action === "stop") {
    return { action };
  }
  return { action: "start", goal: rest };
}
