import { create } from "zustand";
import { persist } from "zustand/middleware";

import { api } from "@/api";
import { SseHttpError, streamJsonSse } from "@/api/sse";
import type {
  ResearchDialogState,
  ResearchRunEvent,
  ResearchRunOutcome,
  ResearchRunState,
} from "@/api/modules/research";

export type ResearchConnectionState =
  | "idle"
  | "connecting"
  | "connected"
  | "reconnecting"
  | "disconnected";

interface ResearchStore {
  activeRunId: string | null;
  activePlanId: string | null;
  panelOpen: boolean;
  plans: Record<string, ResearchDialogState>;
  snapshots: Record<string, ResearchRunState>;
  connectionStates: Record<string, ResearchConnectionState>;
  errors: Record<string, string | null>;
  openPlan: (planId: string, goal: string) => void;
  updatePlan: (plan: ResearchDialogState) => void;
  openRun: (runId: string) => Promise<void>;
  closePanel: () => void;
  refreshSnapshot: (runId: string) => Promise<ResearchRunState>;
  stopRun: (runId: string) => Promise<void>;
}

const controllers = new Map<string, AbortController>();
const reconnectAttempts = new Map<string, number>();
const reconnectTimers = new Map<string, ReturnType<typeof setTimeout>>();

function latestSequence(snapshot: ResearchRunState): number {
  return snapshot.events.reduce(
    (latest, event) => Math.max(latest, event.sequence ?? -1),
    -1,
  );
}

export function reduceResearchMessage(
  snapshot: ResearchRunState,
  message: Record<string, unknown>,
): ResearchRunState {
  if (message.type === "event") {
    if (
      typeof message.sequence !== "number" ||
      message.sequence <= latestSequence(snapshot)
    ) {
      return snapshot;
    }
    const event: ResearchRunEvent = {
      phase: String(message.phase ?? ""),
      timestamp: String(message.timestamp ?? new Date().toISOString()),
      round: typeof message.round === "number" ? message.round : null,
      detail: String(message.detail ?? ""),
      sequence: message.sequence,
    };
    return {
      ...snapshot,
      phase: event.phase,
      current_round: event.round ?? snapshot.current_round,
      updated_at: event.timestamp,
      events: [...snapshot.events, event],
    };
  }

  if (message.type === "outcome") {
    const outcome = message.outcome as ResearchRunOutcome | undefined;
    if (!outcome) return snapshot;
    const existing = snapshot.outcomes.findIndex(
      (item) => item.round === outcome.round,
    );
    const outcomes =
      existing === -1
        ? [...snapshot.outcomes, outcome].sort((a, b) => a.round - b.round)
        : snapshot.outcomes.map((item, index) =>
            index === existing ? { ...item, ...outcome } : item,
          );
    return {
      ...snapshot,
      outcomes,
      completed_rounds: outcomes.length,
    };
  }

  const terminal = String(message.type ?? "").replace("run.", "");
  if (["completed", "failed", "cancelled", "auth_required"].includes(terminal)) {
    return {
      ...snapshot,
      status: terminal,
      phase: terminal,
      error: String(message.error ?? snapshot.error),
      finished_at: new Date().toISOString(),
    };
  }
  return snapshot;
}

function clearConnection(runId: string): void {
  controllers.get(runId)?.abort();
  controllers.delete(runId);
  const timer = reconnectTimers.get(runId);
  if (timer) clearTimeout(timer);
  reconnectTimers.delete(runId);
}

async function connectRun(runId: string): Promise<void> {
  if (controllers.has(runId)) return;
  const controller = new AbortController();
  controllers.set(runId, controller);
  const store = useResearchStore.getState();
  const snapshot = store.snapshots[runId];
  const sequence = snapshot ? latestSequence(snapshot) : -1;
  const suffix = sequence >= 0 ? `?after_sequence=${sequence}` : "";
  useResearchStore.setState((state) => ({
    connectionStates: {
      ...state.connectionStates,
      [runId]: "connecting",
    },
  }));

  let terminal = false;
  try {
    await streamJsonSse(
      `/research/runs/${runId}/stream${suffix}`,
      controller.signal,
      (message) => {
        const current = useResearchStore.getState().snapshots[runId];
        if (!current) return;
        const next = reduceResearchMessage(current, message);
        terminal = next.status !== current.status &&
          ["completed", "failed", "cancelled", "auth_required"].includes(
            next.status,
          );
        useResearchStore.setState((state) => ({
          snapshots: { ...state.snapshots, [runId]: next },
          connectionStates: {
            ...state.connectionStates,
            [runId]: "connected",
          },
        }));
        return terminal ? false : undefined;
      },
    );
  } catch (error) {
    if (error instanceof DOMException && error.name === "AbortError") return;
    const denied =
      error instanceof SseHttpError && [401, 403, 404].includes(error.status);
    useResearchStore.setState((state) => ({
      connectionStates: {
        ...state.connectionStates,
        [runId]: "disconnected",
      },
      errors: {
        ...state.errors,
        [runId]: denied
          ? `实时连接被拒绝 (${error.status})`
          : "实时连接已断开",
      },
    }));
  } finally {
    if (controllers.get(runId) === controller) controllers.delete(runId);
  }

  if (terminal || controller.signal.aborted) return;
  try {
    const refreshed = await useResearchStore.getState().refreshSnapshot(runId);
    if (!["queued", "running"].includes(refreshed.status)) return;
  } catch {
    // Retry below; the connection banner remains the source of truth.
  }
  const attempt = reconnectAttempts.get(runId) ?? 0;
  if (attempt >= 5) return;
  reconnectAttempts.set(runId, attempt + 1);
  useResearchStore.setState((state) => ({
    connectionStates: {
      ...state.connectionStates,
      [runId]: "reconnecting",
    },
  }));
  reconnectTimers.set(
    runId,
    setTimeout(() => {
      reconnectTimers.delete(runId);
      void connectRun(runId);
    }, Math.min(1000 * 2 ** attempt, 15_000)),
  );
}

export const useResearchStore = create<ResearchStore>()(
  persist(
    (set, get) => ({
      activeRunId: null,
      activePlanId: null,
      panelOpen: false,
      plans: {},
      snapshots: {},
      connectionStates: {},
      errors: {},

      openPlan: (planId, goal) => {
        const now = new Date().toISOString();
        set((state) => ({
          activeRunId: null,
          activePlanId: planId,
          panelOpen: true,
          plans: {
            ...state.plans,
            [planId]: {
              plan_id: planId,
              status: "accepted",
              goal,
              task_id: null,
              task_title: null,
              run_id: null,
              brief: null,
              error: "",
              events: [],
              created_at: now,
              updated_at: now,
            },
          },
        }));
      },

      updatePlan: (plan) =>
        set((state) => ({
          plans: { ...state.plans, [plan.plan_id]: plan },
        })),

      openRun: async (runId) => {
        const previous = get().activeRunId;
        if (previous && previous !== runId) clearConnection(previous);
        set({ activeRunId: runId, activePlanId: null, panelOpen: true });
        const snapshot = await get().refreshSnapshot(runId);
        if (["queued", "running"].includes(snapshot.status)) {
          void connectRun(runId);
        }
      },

      closePanel: () => set({ panelOpen: false }),

      refreshSnapshot: async (runId) => {
        const snapshot = await api.getRun(runId);
        set((state) => ({
          snapshots: { ...state.snapshots, [runId]: snapshot },
          errors: { ...state.errors, [runId]: null },
        }));
        return snapshot;
      },

      stopRun: async (runId) => {
        const snapshot = await api.cancelRun(runId);
        set((state) => ({
          snapshots: { ...state.snapshots, [runId]: snapshot },
        }));
      },
    }),
    {
      name: "qwenpaw-active-research",
      partialize: (state) => ({
        activeRunId: state.activeRunId,
        panelOpen: state.panelOpen,
      }),
    },
  ),
);

export function resetResearchStoreForTests(): void {
  for (const runId of controllers.keys()) clearConnection(runId);
  reconnectAttempts.clear();
  useResearchStore.setState({
    activeRunId: null,
    activePlanId: null,
    panelOpen: false,
    plans: {},
    snapshots: {},
    connectionStates: {},
    errors: {},
  });
}
