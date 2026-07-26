import type {
  ResearchDialogState,
  ResearchRunState,
} from "@/api/modules/research";

import { useResearchStore } from "./researchStore";
import styles from "./ResearchMilestones.module.less";

const visiblePhases = new Set([
  "planning",
  "programming",
  "judging",
  "reviewing",
  "kept",
  "rejected",
  "completed",
  "failed",
  "cancelled",
]);

export interface ResearchMilestone {
  id: string;
  phase: string;
  text: string;
}

// eslint-disable-next-line react-refresh/only-export-components
export function projectResearchMilestones(
  snapshot: ResearchRunState,
): ResearchMilestone[] {
  const direction = snapshot.research_brief?.candidate_directions
    .slice()
    .sort((a, b) => a.priority - b.priority)[0];
  const discovery = direction
    ? [{
        id: "discovery",
        phase: "discovery",
        text: `调研方向：${direction.title}`,
      }]
    : [];
  const events = snapshot.events
    .filter((event) => visiblePhases.has(event.phase))
    .map((event) => ({
      id: String(event.sequence),
      phase: event.phase,
      text: event.detail || event.phase,
    }));
  return [...discovery, ...events];
}

// ponytail: dialog planning has no run snapshot yet; show its existing events directly.
export function projectResearchPlanMilestones(
  plan: ResearchDialogState,
): ResearchMilestone[] {
  return [
    {
      id: `${plan.plan_id}-accepted`,
      phase: "discovery",
      text: `已受理：${plan.goal}`,
    },
    ...plan.events.map((event, index) => ({
      id: `${plan.plan_id}-${event.timestamp}-${index}`,
      phase: event.phase,
      text: event.detail || event.phase,
    })),
  ];
}

export function ResearchMilestones() {
  const runId = useResearchStore((state) => state.activeRunId);
  const planId = useResearchStore((state) => state.activePlanId);
  const snapshot = useResearchStore((state) =>
    runId ? state.snapshots[runId] : undefined,
  );
  const plan = useResearchStore((state) =>
    planId ? state.plans[planId] : undefined,
  );
  const milestones = (
    snapshot
      ? projectResearchMilestones(snapshot)
      : plan
        ? projectResearchPlanMilestones(plan)
        : []
  ).slice(-3);
  if (milestones.length === 0) return null;

  return (
    <section className={styles.feed} aria-label="AutoResearch 关键进展">
      <strong>AutoResearch</strong>
      {milestones.map((item) => (
        <span key={item.id}>
          <i data-phase={item.phase} />
          {item.text}
        </span>
      ))}
    </section>
  );
}
