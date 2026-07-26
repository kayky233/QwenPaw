import { useState, useCallback, useRef, useEffect, useMemo } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import {
  Card,
  Button,
  Space,
  Tag,
  Progress,
  Statistic,
  Row,
  Col,
  Alert,
  Typography,
  Steps,
  Badge,
  InputNumber,
  Input,
  Tooltip,
  Collapse,
  Timeline,
} from "antd";
import {
  CheckCircleOutlined,
  BugOutlined,
  ExperimentOutlined,
  CodeOutlined,
  ReloadOutlined,
  TrophyOutlined,
  RocketOutlined,
  LoadingOutlined,
  BranchesOutlined,
  BulbOutlined,
  SearchOutlined,
  FileSearchOutlined,
  FormOutlined,
  MergeCellsOutlined,
} from "@ant-design/icons";
import {
  ThunderboltOutlined,
  CloseCircleOutlined,
  InfoCircleOutlined,
  MessageOutlined,
  CopyOutlined,
} from "@ant-design/icons";
import { api } from "@/api";
import { SseHttpError, streamJsonSse } from "@/api/sse";
import type {
  ResearchTaskDetail,
  ResearchRunState,
  ResearchRunOutcome,
  ResearchRunEvent,
} from "@/api/modules/research";
import { providerApi } from "@/api/modules/provider";
import { useAgentStore } from "@/stores/agentStore";
import { useResearchStore } from "@/features/research/researchStore";
import styles from "./index.module.less";
import {
  latestResearchSequence,
  nextResearchSequence,
} from "./researchStreamState";

const { Title, Text } = Typography;
const { TextArea } = Input;

const EXAMPLE_GOALS = [
  "帮我调研 qwenpaw 项目中 submitFeedback 没有对并发请求做去重的问题，解决并提交 PR",
  "分析 research.py 中 evaluate_candidate 的沙箱安全性，加固 sandbox 隔离并验证",
  "Channels 模块缺少输入校验，对所有 channel 输入增加 schema 验证并写测试",
];

const PHASE_LABELS: Record<string, string> = {
  queued: "排队中",
  planning: "分析需求",
  searching: "搜索代码库",
  reading: "读取文件",
  generating: "生成契约",
  creating_task: "创建任务",
  starting_run: "启动研究",
  evaluating_baseline: "基线评测",
  proposing: "LLM 改进方案",
  evaluating_candidate: "评测候选方案",
  keeping: "采纳改进",
  rejecting: "放弃改进",
  submitting: "提交方案",
  creating_pr: "创建 PR",
  pr_created: "PR 已创建",
  pr_failed: "PR 创建失败",
  completed: "研究完成",
  failed: "研究失败",
  cancelled: "已取消",
};

const DIALOG_PHASE_LABELS: Record<string, string> = {
  ...PHASE_LABELS,
  completed: "规划完成",
  failed: "规划失败",
};

// ── Syntax highlighting keywords ──

const PY_KEYWORDS = new Set([
  "def", "class", "return", "if", "elif", "else", "for", "while", "try",
  "except", "finally", "with", "as", "import", "from", "pass", "raise",
  "yield", "lambda", "and", "or", "not", "in", "is", "None", "True", "False",
  "async", "await", "break", "continue", "global", "nonlocal", "assert", "del",
]);

const PY_BUILTINS = new Set([
  "print", "len", "range", "str", "int", "float", "list", "dict", "set",
  "tuple", "bool", "type", "enumerate", "zip", "map", "filter", "sorted",
  "open", "isinstance", "hasattr", "getattr", "setattr", "super", "any", "all",
  "max", "min", "sum", "abs", "round", "Path", "json", "re", "os", "sys",
]);

function highlightLine(line: string): React.ReactNode {
  // Simple token-based highlighting for Python
  const tokens: React.ReactNode[] = [];
  let i = 0;
  while (i < line.length) {
    // String literals (single, double, f-strings)
    const strMatch = line.slice(i).match(/^(f?["'](?:[^"\\]|\\.)*["']|f?""".*?"""|f?'''.*?''')/s);
    if (strMatch) {
      tokens.push(
        <span key={i} style={{ color: "#a5d6ff" }}>{strMatch[0]}</span>
      );
      i += strMatch[0].length;
      continue;
    }
    // Comments
    if (line[i] === "#") {
      tokens.push(
        <span key={i} style={{ color: "#6e7681" }}>{line.slice(i)}</span>
      );
      break;
    }
    // Numbers
    const numMatch = line.slice(i).match(/^(\d+\.?\d*|\.\d+)/);
    if (numMatch) {
      tokens.push(
        <span key={i} style={{ color: "#79c0ff" }}>{numMatch[0]}</span>
      );
      i += numMatch[0].length;
      continue;
    }
    // Identifiers (keywords + builtins)
    const idMatch = line.slice(i).match(/^([a-zA-Z_]\w*)/);
    if (idMatch) {
      const word = idMatch[0];
      if (PY_KEYWORDS.has(word)) {
        tokens.push(
          <span key={i} style={{ color: "#ff7b72" }}>{word}</span>
        );
      } else if (PY_BUILTINS.has(word)) {
        tokens.push(
          <span key={i} style={{ color: "#d2a8ff" }}>{word}</span>
        );
      } else {
        tokens.push(<span key={i}>{word}</span>);
      }
      i += word.length;
      continue;
    }
    tokens.push(<span key={i}>{line[i]}</span>);
    i++;
  }
  return tokens;
}

// ── Code viewer ───────────────────────────────────────────────────────

function CodeBlock({ code, fileName }: { code: string; fileName?: string }) {
  const lines = code.split("\n");
  const [copied, setCopied] = useState(false);

  const handleCopy = useCallback(() => {
    navigator.clipboard.writeText(code).then(() => {
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    });
  }, [code]);

  return (
    <div className={styles.codeBlockWrapper}>
      <div className={styles.codeBlockHeader}>
        <Space size="small">
          <CodeOutlined style={{ fontSize: 11 }} />
          <span>{fileName || "solution.py"}</span>
          <Tag style={{ fontSize: 10, lineHeight: "16px", padding: "0 4px" }}>
            {lines.length} 行
          </Tag>
        </Space>
        <Button type="text" size="small" icon={<CopyOutlined />} onClick={handleCopy}
          style={{ fontSize: 11 }}>
          {copied ? "已复制" : "复制"}
        </Button>
      </div>
      <div className={styles.codeBlockBody}>
        <pre className={styles.codeBlock}>
          {lines.map((line, i) => (
            <div key={i} className={styles.codeLine}>
              <span className={styles.lineNum}>{i + 1}</span>
              <code>{highlightLine(line)}</code>
            </div>
          ))}
        </pre>
      </div>
    </div>
  );
}

// ── Event bubble classification ──

interface EventBubbleStyle {
  icon: React.ReactNode;
  className: string;
  label: string;
}

function getEventBubbleStyle(phase: string): EventBubbleStyle {
  const lower = phase.toLowerCase();
  if (lower.includes("propos") || lower === "proposal") {
    return {
      icon: <BulbOutlined style={{ color: "#a371f7" }} />,
      className: styles.bubbleProposal,
      label: "🤖 AI 分析",
    };
  }
  if (lower.includes("evaluat") || lower.includes("judg")) {
    return {
      icon: <ExperimentOutlined style={{ color: "#d2991d" }} />,
      className: styles.bubbleEvaluate,
      label: "📊 评测",
    };
  }
  if (["kept", "keeping"].includes(lower)) {
    return {
      icon: <CheckCircleOutlined style={{ color: "#3fb950" }} />,
      className: styles.bubbleSuccess,
      label: "✅ 采纳",
    };
  }
  if (["rejected", "rejecting"].includes(lower)) {
    return {
      icon: <CloseCircleOutlined style={{ color: "#f85149" }} />,
      className: styles.bubbleError,
      label: "❌ 丢弃",
    };
  }
  if (lower.includes("fail") || lower.includes("error") || lower === "baseline_failing") {
    return {
      icon: <CloseCircleOutlined style={{ color: "#f85149" }} />,
      className: styles.bubbleError,
      label: "⚠️ 问题",
    };
  }
  if (["completed", "pr_created", "accepted"].includes(lower)) {
    return {
      icon: <TrophyOutlined style={{ color: "#3fb950" }} />,
      className: styles.bubbleSuccess,
      label: "🎉 完成",
    };
  }
  if (lower.includes("plan") || lower.includes("search")) {
    return {
      icon: <SearchOutlined style={{ color: "#58a6ff" }} />,
      className: styles.bubblePlanning,
      label: "🔍 规划",
    };
  }
  return {
    icon: <InfoCircleOutlined style={{ color: "#8b949e" }} />,
    className: styles.bubbleDefault,
    label: "📌 进度",
  };
}

// ── Main page ──────────────────────────────────────────────────────────

export default function AutoResearchPage() {
  // ── Input state ──
  const [goal, setGoal] = useState("");
  const [rounds, setRounds] = useState(3);
  const [autoPr, setAutoPr] = useState(true);

  // ── Active model from chat (shared) ──
  const { selectedAgent } = useAgentStore();
  const [activeModelName, setActiveModelName] = useState("默认");
  const navigate = useNavigate();
  const [searchParams, setSearchParams] = useSearchParams();

  // ── Load active model from chat config ──
  useEffect(() => {
    let cancelled = false;
    providerApi
      .getActiveModels({ scope: "effective", agent_id: selectedAgent })
      .then((data) => {
        if (cancelled) return;
        const pId = data.active_llm?.provider_id;
        const mId = data.active_llm?.model;
        if (!pId || !mId) return;
        // Try to get a human-friendly name from the provider list
        providerApi.listProviders().then((providers) => {
          if (cancelled) return;
          for (const p of providers) {
            if (p.id === pId) {
              const m = [...(p.models ?? []), ...(p.extra_models ?? [])].find(
                (m) => m.id === mId,
              );
              if (m) {
                setActiveModelName(m.name || m.id);
                return;
              }
            }
          }
          setActiveModelName(mId);
        }).catch(() => {
          if (!cancelled) setActiveModelName(`${pId}/${mId}`);
        });
      })
      .catch(() => {
        // provider resolution failed, keep default name
      });
    return () => { cancelled = true; };
  }, [selectedAgent]);

  // ── Run state ──
  type ConnectionState = "connected" | "reconnecting" | "disconnected";
  const [phase, setPhase] = useState<"idle" | "planning" | "running" | "succeeded" | "failed" | "cancelled">("idle");
  const [connectionState, setConnectionState] = useState<ConnectionState>("connected");
  const [taskId, setTaskId] = useState<string | null>(null);
  const [taskTitle, setTaskTitle] = useState<string | null>(null);
  const [runId, setRunId] = useState<string | null>(null);
  const [runState, setRunState] = useState<ResearchRunState | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [task, setTask] = useState<ResearchTaskDetail | null>(null);

  const [planningEvents, setPlanningEvents] = useState<string[]>([]);

  const abortRef = useRef<AbortController | null>(null);
  const dialogAbortRef = useRef<AbortController | null>(null);
  const reconnectAttemptsRef = useRef(0);
  const reconnectTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const streamRef = useRef<HTMLDivElement>(null);
  const shouldAutoScrollRef = useRef(true);
  const activeRunIdRef = useRef<string | null>(null);
  const activePlanIdRef = useRef<string | null>(null);
  // Last received event sequence for resumption
  const lastEventSequenceRef = useRef(-1);
  // Ref to break circular dependency between scheduleReconnect and connectSSE
  const connectSSERef = useRef<((rId: string) => void) | null>(null);
  // Track when the last SSE event was received (for "last synced" display)
  const lastSyncedAtRef = useRef<string>("");

  const applyRunSnapshot = useCallback((state: ResearchRunState) => {
    setRunState(state);
    lastEventSequenceRef.current = latestResearchSequence(state.events);
    lastSyncedAtRef.current = state.updated_at;
  }, []);

  useEffect(() => {
    if (!runId || !runState) return;
    useResearchStore.setState((state) => ({
      activeRunId: runId,
      panelOpen: true,
      snapshots: { ...state.snapshots, [runId]: runState },
      connectionStates: {
        ...state.connectionStates,
        [runId]: connectionState,
      },
      errors: { ...state.errors, [runId]: error },
    }));
  }, [runId, runState, connectionState, error]);

  // ── Schedule SSE reconnection with exponential backoff ──
  const scheduleReconnect = useCallback((rId: string) => {
    const attempt = reconnectAttemptsRef.current++;
    const delay = Math.min(1000 * 2 ** attempt, 15_000);
    setConnectionState("reconnecting");

    reconnectTimerRef.current = setTimeout(async () => {
      try {
        const state = await api.getRun(rId);
        if (activeRunIdRef.current !== rId) return;
        applyRunSnapshot(state);
        if (["queued", "running"].includes(state.status)) {
          connectSSERef.current?.(rId);
        } else {
          setConnectionState("connected");
          if (state.status === "completed") setPhase("succeeded");
          else if (state.status === "cancelled") { setPhase("cancelled"); setError("研究已取消"); }
          else { setPhase("failed"); setError(state.error || "研究执行失败"); }
          if (taskId) api.getTask(taskId).then(setTask).catch(() => {});
        }
      } catch {
        if (activeRunIdRef.current !== rId) return;
        const attempt = reconnectAttemptsRef.current;
        if (attempt < 5) {
          scheduleReconnect(rId);
        } else {
          setConnectionState("disconnected");
          setError("实时连接已断开，无法获取研究状态。请手动恢复。");
        }
      }
    }, delay);
  }, [taskId, applyRunSnapshot]);

  // ── Cleanup SSE and reconnect timers ──
  useEffect(() => {
    return () => {
      abortRef.current?.abort();
      dialogAbortRef.current?.abort();
      if (reconnectTimerRef.current) {
        clearTimeout(reconnectTimerRef.current);
      }
    };
  }, []);

  // ── Connect SSE for a run (fetch-based to support auth headers) ──
  const connectSSE = useCallback((rId: string) => {
    activeRunIdRef.current = rId;
    if (reconnectTimerRef.current) {
      clearTimeout(reconnectTimerRef.current);
      reconnectTimerRef.current = null;
    }

    // Abort any previous connection
    abortRef.current?.abort();
    const controller = new AbortController();
    abortRef.current = controller;

    const afterSeq = lastEventSequenceRef.current;
    const params = new URLSearchParams();
    if (afterSeq >= 0) params.set("after_sequence", String(afterSeq));
    const qs = params.toString();
    const streamPath = `/research/runs/${rId}/stream${qs ? `?${qs}` : ""}`;

    (async () => {
      let terminalReceived = false;
      try {
        setConnectionState("connected");
        reconnectAttemptsRef.current = 0;
        await streamJsonSse(streamPath, controller.signal, (data) => {
            // Guard: stale connection
            if (activeRunIdRef.current !== rId || abortRef.current !== controller) {
              return false;
            }

            if (data.type === "event") {
              const sequence = nextResearchSequence(
                lastEventSequenceRef.current,
                data.sequence,
              );
              if (sequence === null) return;
              lastEventSequenceRef.current = sequence;
              lastSyncedAtRef.current = new Date().toISOString();

              setRunState((prev) => {
                const newEvent = {
                  phase: data.phase as string,
                  timestamp: (data.timestamp as string) ?? new Date().toISOString(),
                  round: typeof data.round === "number" ? data.round : null,
                  detail: (data.detail as string) || "",
                  sequence,
                };
                if (!prev) {
                  return {
                    id: rId,
                    task_id: "",
                    agent_id: "",
                    status: "running",
                    phase: data.phase as string,
                    rounds: 0,
                    current_round: (data.round as number) ?? null,
                    completed_rounds: 0,
                    events: [newEvent],
                    outcomes: [],
                    created_at: (data.timestamp as string) ?? new Date().toISOString(),
                    updated_at: (data.timestamp as string) ?? new Date().toISOString(),
                    started_at: null,
                    finished_at: null,
                    error: "",
                  };
                }
                return {
                  ...prev,
                  phase: data.phase as string,
                  current_round: (data.round as number) ?? prev.current_round,
                  events: [...prev.events, newEvent],
                  updated_at: (data.timestamp as string) ?? new Date().toISOString(),
                };
              });
            } else if (data.type === "outcome") {
              lastSyncedAtRef.current = new Date().toISOString();
              setRunState((prev) => {
                const outcome = data.outcome as ResearchRunOutcome;
                if (!prev) {
                  return {
                    id: rId,
                    task_id: "",
                    agent_id: "",
                    status: "running",
                    phase: (data.phase as string) ?? "evaluating_candidate",
                    rounds: 0,
                    current_round: (outcome.round as number) ?? null,
                    completed_rounds: 1,
                    events: [],
                    outcomes: [outcome],
                    created_at: (data.timestamp as string) ?? new Date().toISOString(),
                    updated_at: (data.timestamp as string) ?? new Date().toISOString(),
                    started_at: null,
                    finished_at: null,
                    error: "",
                  };
                }
                const idx = prev.outcomes.findIndex((o) => o.round === outcome.round);
                const newOutcomes =
                  idx === -1
                    ? [...prev.outcomes, outcome].sort((a, b) => a.round - b.round)
                    : prev.outcomes.map((o, i) =>
                        i === idx
                          ? {
                              ...o,
                              ...outcome,
                              metrics: {
                                ...((o.metrics ?? {}) as Record<string, unknown>),
                                ...((outcome.metrics ?? {}) as Record<string, unknown>),
                              },
                            }
                          : o,
                      );
                const done = newOutcomes.filter(
                  (item) =>
                    item.status === "kept" ||
                    item.status === "rejected" ||
                    (item.status ?? "").endsWith("_error"),
                ).length;
                return {
                  ...prev,
                  outcomes: newOutcomes,
                  completed_rounds: done,
                  updated_at: (data.timestamp as string) ?? new Date().toISOString(),
                };
              });
            } else if (
              data.type === "done" ||
              data.type === "run.completed" ||
              data.type === "run.failed" ||
              data.type === "run.cancelled"
            ) {
              terminalReceived = true;
              setConnectionState("connected");
              if (data.type === "run.completed" || data.status === "completed") {
                setPhase("succeeded");
              } else if (data.type === "run.cancelled" || data.status === "cancelled") {
                setPhase("cancelled");
                setError((data.error as string) ?? "研究已取消");
              } else {
                setPhase("failed");
                setError((data.error as string) ?? "研究执行失败");
              }
              return false;
            }
          },
        );
        if (
          !terminalReceived &&
          !controller.signal.aborted &&
          activeRunIdRef.current === rId &&
          abortRef.current === controller
        ) {
          scheduleReconnect(rId);
        }
      } catch (err: unknown) {
        if (err instanceof DOMException && err.name === "AbortError") return;
        if (activeRunIdRef.current !== rId) return;
        if (
          err instanceof SseHttpError &&
          [401, 403, 404].includes(err.status)
        ) {
          setConnectionState("disconnected");
          setError(`SSE 连接被拒绝 (${err.status})，请检查权限或刷新页面。`);
          return;
        }

        // Attempt reconnection
        if (reconnectAttemptsRef.current < 5) {
          scheduleReconnect(rId);
        } else {
          setConnectionState("disconnected");
          // Keep runState — don't clear it
        }
      }
    })();
  }, [scheduleReconnect]);
  // Keep ref in sync to break circular dependency with scheduleReconnect
  connectSSERef.current = connectSSE;

  // ── Start a run stream: fetch initial state first, then connect SSE ──
  const startRunStream = useCallback(async (runId: string) => {
    activeRunIdRef.current = runId;
    // Reset sequence for fresh run start
    lastEventSequenceRef.current = -1;
    lastSyncedAtRef.current = "";
    reconnectAttemptsRef.current = 0;
    try {
      const initialState = await api.getRun(runId);
      if (activeRunIdRef.current !== runId) return;
      applyRunSnapshot(initialState);
    } catch {
      if (activeRunIdRef.current !== runId) return;
      setRunState(null);
    }
    if (activeRunIdRef.current !== runId) return;
    connectSSE(runId);
  }, [connectSSE, applyRunSnapshot]);

  // ── Load task when taskId is known ──
  useEffect(() => {
    if (!taskId || !["succeeded", "failed", "cancelled"].includes(phase)) return;
    api.getTask(taskId).then(setTask).catch(() => {});
  }, [taskId, phase]);

  // ── URL parameter support: load existing task/run via ?taskId=xxx&runId=yyy ──
  const urlTaskId = searchParams.get("taskId");
  const urlRunId = searchParams.get("runId");

  useEffect(() => {
    if (!urlTaskId || !urlRunId) return;
    let cancelled = false;

    const loadExistingRun = async () => {
      try {
        const [runData, taskData] = await Promise.all([
          api.getRun(urlRunId),
          api.getTask(urlTaskId),
        ]);
        if (cancelled) return;
        setTaskId(urlTaskId);
        setRunId(urlRunId);
        setTaskTitle(taskData.title);
        applyRunSnapshot(runData);
        setTask(taskData);

        const status = runData.status;
        if (status === "completed") {
          setPhase("succeeded");
          setConnectionState("connected");
        } else if (status === "failed") {
          setPhase("failed");
          setError(runData.error || "研究执行失败");
          setConnectionState("connected");
        } else if (status === "cancelled") {
          setPhase("cancelled");
          setError("研究已取消");
          setConnectionState("connected");
        } else if (status === "running" || status === "queued") {
          setPhase("running");
          // Connect SSE for live updates
          startRunStream(urlRunId);
        }
      } catch (e: unknown) {
        if (cancelled) return;
        const msg = e instanceof Error ? e.message : "加载失败";
        setError(`无法加载研究数据: ${msg}`);
        // Clear invalid URL params
        setSearchParams({}, { replace: true });
      }
    };

    loadExistingRun();
    return () => { cancelled = true; };
  }, [urlTaskId, urlRunId, startRunStream, applyRunSnapshot, setSearchParams]);

  // ── Connect dialog-planning SSE ──
  const connectDialogSSE = useCallback((planId: string) => {
    activePlanIdRef.current = planId;
    dialogAbortRef.current?.abort();
    const controller = new AbortController();
    dialogAbortRef.current = controller;

    const pollAfterDisconnect = () => {
      if (activePlanIdRef.current !== planId) return;
      let pollCount = 0;
      const maxPolls = 10;
      const pollInterval = 2000;

      const poll = () => {
        if (activePlanIdRef.current !== planId) return;
        api.dialogStatus(planId).then((ds) => {
          if (activePlanIdRef.current !== planId) return;
          if (ds.status === "failed") {
            activePlanIdRef.current = null;
            setPhase("idle");
            setError(ds.error || "规划失败");
            setPlanningEvents((prev) => [...prev, `❌ ${ds.error || "规划失败"}`]);
          } else if (ds.status === "cancelled") {
            activePlanIdRef.current = null;
            setPhase("idle");
            setError("研究已取消");
            setPlanningEvents((prev) => [...prev, `⏹️ 研究已取消`]);
          } else if (ds.status === "completed" && ds.run_id) {
            activePlanIdRef.current = null;
            setTaskId(ds.task_id);
            setTaskTitle(ds.task_title ?? null);
            setRunId(ds.run_id);
            setPlanningEvents((prev) => [...prev, "✅ 研究引擎已启动"]);
            setPhase("running");
            startRunStream(ds.run_id!);
          } else if (ds.status === "planning" || ds.status === "queued") {
            // Still in progress — poll again
            pollCount++;
            if (pollCount < maxPolls) {
              setTimeout(poll, pollInterval);
            }
          }
        }).catch(() => {
          // Poll failed — retry if within limit
          if (activePlanIdRef.current !== planId) return;
          pollCount++;
          if (pollCount < maxPolls) {
            setTimeout(poll, pollInterval);
          }
        });
      };

      poll();
    };

    void streamJsonSse(
      `/research/dialog/${planId}/stream`,
      controller.signal,
      (data) => {
        if (
          activePlanIdRef.current !== planId ||
          dialogAbortRef.current !== controller
        ) {
          return false;
        }
        if (data.type === "event") {
          const phaseName = String(data.phase ?? "");
          const label = DIALOG_PHASE_LABELS[phaseName] ?? phaseName;
          const detail = data.detail ? ` — ${data.detail}` : "";
          setPlanningEvents((prev) => [...prev, `${label}${detail}`]);
          return;
        }
        if (data.type !== "done") return;

        dialogAbortRef.current = null;
        activePlanIdRef.current = null;
        if (data.status === "failed") {
          setPhase("idle");
          setError(String(data.error || "规划失败"));
          setPlanningEvents((prev) => [
            ...prev,
            `❌ ${String(data.error || "规划失败")}`,
          ]);
          return false;
        }
        if (data.status === "cancelled") {
          setPhase("idle");
          setError("研究已取消");
          setPlanningEvents((prev) => [...prev, "⏹️ 研究已取消"]);
          return false;
        }

        setPlanningEvents((prev) => [...prev, "✅ 研究引擎已启动"]);
        if (typeof data.task_id === "string") {
          setTaskId(data.task_id);
          setTaskTitle(
            typeof data.task_title === "string" ? data.task_title : null,
          );
        }
        if (typeof data.run_id === "string") {
          setRunId(data.run_id);
          setPhase("running");
          startRunStream(data.run_id);
        }
        return false;
      },
    )
      .then(() => {
        if (
          !controller.signal.aborted &&
          activePlanIdRef.current === planId
        ) {
          pollAfterDisconnect();
        }
      })
      .catch((error: unknown) => {
        if (error instanceof DOMException && error.name === "AbortError") return;
        if (activePlanIdRef.current === planId) pollAfterDisconnect();
      });
  }, [startRunStream]);

  // ── Start dialog research (async: returns immediately, SSE for progress) ──
  const startResearch = useCallback(async () => {
    if (!goal.trim()) return;
    setPhase("planning");
    setError(null);
    setRunState(null);
    setTask(null);
    setPlanningEvents(["📤 提交目标，等待 AI 规划..."]);

    try {
      const result = await api.dialogResearch({
        goal: goal.trim(),
        rounds,
        auto_pr: autoPr,
      });

      activePlanIdRef.current = result.plan_id;
      // Connect SSE for planning progress
      connectDialogSSE(result.plan_id);
    } catch (e: unknown) {
      const msg = e instanceof Error ? e.message : "提交失败";
      setPhase("idle");
      setError(msg);
      setPlanningEvents([]);
    }
  }, [goal, rounds, autoPr, connectDialogSSE]);

  // ── Recover from disconnected state: query backend, then decide next step ──
  const recoverConnection = useCallback(async () => {
    if (!runId) return;
    setError(null);
    try {
      const state = await api.getRun(runId);
      if (activeRunIdRef.current !== runId) return;
      applyRunSnapshot(state);
      if (state.status === "completed") {
        setPhase("succeeded");
        setConnectionState("connected");
        return;
      }
      if (state.status === "failed") {
        setPhase("failed");
        setError(state.error || "研究执行失败");
        setConnectionState("connected");
        return;
      }
      if (state.status === "cancelled") {
        setPhase("cancelled");
        setError("研究已取消");
        setConnectionState("connected");
        return;
      }
      // Still running — reconnect SSE
      reconnectAttemptsRef.current = 0;
      setConnectionState("reconnecting");
      connectSSE(runId);
      if (taskId) api.getTask(taskId).then(setTask).catch(() => {});
    } catch {
      if (activeRunIdRef.current !== runId) return;
      setError("恢复连接失败，请稍后重试");
    }
  }, [runId, taskId, connectSSE, applyRunSnapshot]);

  // ── Reset ──
  const reset = useCallback(() => {
    abortRef.current?.abort();
    abortRef.current = null;
    dialogAbortRef.current?.abort();
    dialogAbortRef.current = null;
    if (reconnectTimerRef.current) {
      clearTimeout(reconnectTimerRef.current);
      reconnectTimerRef.current = null;
    }
    reconnectAttemptsRef.current = 0;
    activeRunIdRef.current = null;
    activePlanIdRef.current = null;
    lastEventSequenceRef.current = -1;
    lastSyncedAtRef.current = "";
    if (runId && phase === "running") {
      void api.cancelRun(runId);
    }
    setPhase("idle");
    setTaskId(null);
    setTaskTitle(null);
    setRunId(null);
    setRunState(null);
    setError(null);
    setConnectionState("connected");
    setTask(null);
    setPlanningEvents([]);
  }, [runId, phase]);

  // ── Derived stats ──
  const baselineEval = task?.evaluation;
  const baselineMetrics = (baselineEval?.metrics ?? {}) as Record<string, unknown>;
  const baselineBugs: number = (baselineMetrics.bugs as number) ?? 0;
  const baselineScore: number = baselineEval?.score ?? 0;

  const outcomes: ResearchRunOutcome[] = useMemo(
    () => runState?.outcomes ?? [],
    [runState?.outcomes],
  );
  const keptOutcomes = outcomes.filter((o) => o.status === "kept");
  const rejectedOutcomes = outcomes.filter((o) => o.status === "rejected");
  const latestKeptOutcome = [...outcomes].reverse().find((o) => o.status === "kept");
  const lastMetrics = (latestKeptOutcome?.metrics ?? {}) as Record<string, unknown>;
  const currentBugs: number = latestKeptOutcome
    ? ((lastMetrics.bugs as number) ?? baselineBugs)
    : baselineBugs;
  const currentScore: number = latestKeptOutcome?.candidate_score ?? baselineScore;
  const currentRound: number = runState?.current_round ?? outcomes.length;

  const currentPhaseLabel = PHASE_LABELS[runState?.phase ?? ""] ?? runState?.phase ?? "运行中";

  // ── PR event ──
  const prEvent = runState?.events?.find(
    (e) => e.phase === "pr_created" || e.phase === "pr_failed"
  );
  const prUrl = prEvent?.phase === "pr_created" ? prEvent.detail : null;

  // ── Group events by round for per-round display (MUST be before early returns for hooks compliance) ──
  const eventsByRound = useMemo(() => {
    const map = new Map<number, ResearchRunEvent[]>();
    for (const e of runState?.events ?? []) {
      const r = e.round ?? 0;
      if (!map.has(r)) map.set(r, []);
      map.get(r)!.push(e);
    }
    return map;
  }, [runState?.events]);

  // ── Filter runtime events (exclude planning events already shown) ──
  // Moved BEFORE early returns for React Hooks compliance
  const runtimeEvents = useMemo(() => {
    const planPhases = new Set([
      "planning", "searching", "reading", "generating", "creating_task",
      "starting_run", "queued", "starting",
    ]);
    return (runState?.events ?? []).filter(
      (e) => !planPhases.has(e.phase) && e.phase !== "pr_created" && e.phase !== "pr_failed"
    );
  }, [runState?.events]);

  // Group live events: show events not yet summarized in outcomes
  const liveFeed = useMemo(() => {
    const outcomeRounds = new Set(outcomes.map((o) => o.round));
    return runtimeEvents.filter((e) => {
      if (e.round === currentRound) return true;
      if (e.round != null && !outcomeRounds.has(e.round)) return true;
      return false;
    });
  }, [runtimeEvents, outcomes, currentRound]);

  // ── Smart auto-scroll: only scroll to bottom when user is already near bottom ──
  useEffect(() => {
    const el = streamRef.current;
    if (!el || !shouldAutoScrollRef.current) return;
    el.scrollTo({ top: el.scrollHeight, behavior: "smooth" });
  }, [liveFeed.length]);

  // ── Reconnect indicator ──

  // ── Loading state ──
  if (phase === "planning") {
    return (
      <div className={styles.container}>
        <div className={styles.hero}>
          <BulbOutlined style={{ fontSize: 32, color: "var(--accent)", marginBottom: 8 }} />
          <Title level={2} style={{ margin: 0 }}>AI 正在规划研究方案</Title>
          <Text type="secondary">分析目标 → 搜索代码库 → 生成评测契约</Text>
        </div>
        <Card className={styles.card}>
          <Space direction="vertical" size="middle" style={{ width: "100%" }}>
            {planningEvents.map((evt, i) => (
              <div key={i} style={{ display: "flex", alignItems: "center", gap: 8 }}>
                {i < planningEvents.length - 1 ? (
                  <CheckCircleOutlined style={{ color: "#3fb950" }} />
                ) : (
                  <LoadingOutlined spin style={{ color: "var(--accent)" }} />
                )}
                <Text>{evt}</Text>
              </div>
            ))}
          </Space>
          <div style={{ marginTop: 24 }}>
            <Progress percent={Math.min((planningEvents.length / 6) * 100, 95)} status="active"
              strokeColor={{ from: "#58a6ff", to: "#a371f7" }} />
          </div>
        </Card>
      </div>
    );
  }

  // ── Idle state: dialog input ──
  if (phase === "idle") {
    return (
      <div className={styles.container}>
        {/* Hero */}
        <div className={styles.hero}>
          <RocketOutlined style={{ fontSize: 40, color: "var(--accent)", marginBottom: 12 }} />
          <Title level={2} style={{ margin: 0 }}>Auto Research</Title>
          <Text type="secondary" style={{ fontSize: 15, maxWidth: 480, textAlign: "center" }}>
            用自然语言描述目标，AI 自动规划、分析代码、迭代修复、提交 PR
          </Text>
        </div>

        {/* Input card — richer styling */}
        <Card className={`${styles.card} ${styles.promptCard}`}>
          <div className={styles.idleWrapper}>
            <div style={{ marginBottom: 16 }}>
              <Space align="center" style={{ marginBottom: 8 }}>
                <RocketOutlined style={{ color: "var(--accent)", fontSize: 16 }} />
                <Text strong style={{ fontSize: 14 }}>你想要 AI 帮你做什么？</Text>
              </Space>
              <TextArea
                value={goal}
                onChange={(e) => setGoal(e.target.value)}
                placeholder="例如：帮我调研 qwenpaw 的 issue #42，分析根本原因，修改代码并端到端验证，最后提交 PR"
                autoSize={{ minRows: 3, maxRows: 8 }}
                style={{
                  fontSize: 14,
                  background: "rgba(255,255,255,0.04)",
                  border: "1px solid var(--border-color, rgba(255,255,255,0.08))",
                  borderRadius: 8,
                }}
                onPressEnter={(e) => {
                  if (e.metaKey || e.ctrlKey) startResearch();
                }}
              />
            </div>

            {/* Quick-try examples */}
            <div style={{ marginBottom: 16 }}>
              <Text type="secondary" style={{ fontSize: 11, marginBottom: 6, display: "block" }}>
                💡 试试这些 →
              </Text>
              <div style={{ display: "flex", flexWrap: "wrap", gap: 6 }}>
                {EXAMPLE_GOALS.map((g, i) => (
                  <Tag
                    key={i}
                    className={styles.suggestionTag}
                    onClick={() => setGoal(g)}
                  >
                    {g.length > 55 ? g.slice(0, 55) + "…" : g}
                  </Tag>
                ))}
              </div>
            </div>

            {/* Model + settings row */}
            <div
              style={{
                display: "flex",
                alignItems: "center",
                justifyContent: "space-between",
                flexWrap: "wrap",
                gap: 12,
                padding: "12px 0",
                borderTop: "1px solid var(--border-color, rgba(255,255,255,0.07))",
                borderBottom: "1px solid var(--border-color, rgba(255,255,255,0.07))",
                marginBottom: 16,
              }}
            >
              <Space size="middle" wrap>
                <Space size={4}>
                  <Text type="secondary" style={{ fontSize: 12 }}>🧠 模型:</Text>
                  <Tooltip title={activeModelName}>
                    <Tag color="blue" style={{ margin: 0, fontSize: 11, maxWidth: 160 }} className={styles.modelTag}>
                      {activeModelName.length > 18
                        ? activeModelName.slice(0, 18) + "…"
                        : activeModelName}
                    </Tag>
                  </Tooltip>
                  <Tooltip title="在聊天页面切换模型">
                    <Button
                      type="link"
                      size="small"
                      style={{ fontSize: 10, padding: 0 }}
                      onClick={() => navigate("/chat")}
                    >切换</Button>
                  </Tooltip>
                </Space>
                <Space size={4}>
                  <Text type="secondary" style={{ fontSize: 12 }}>🔄 迭代轮数:</Text>
                  <InputNumber
                    min={1} max={20} value={rounds}
                    onChange={(v) => setRounds(v ?? 3)}
                    size="small" style={{ width: 60 }}
                  />
                </Space>
              </Space>
              <Tooltip title={autoPr ? "研究完成后自动提交 PR" : "只做研究，不提交 PR"}>
                <Tag.CheckableTag
                  checked={autoPr}
                  onChange={setAutoPr}
                  style={{
                    padding: "4px 12px",
                    border: "1px solid var(--border)",
                    borderRadius: 6,
                    fontSize: 12,
                  }}
                >
                  <MergeCellsOutlined style={{ marginRight: 4 }} />
                  {autoPr ? "自动提交 PR" : "仅研究"}
                </Tag.CheckableTag>
              </Tooltip>
            </div>

            {/* Submit button */}
            <Button
              type="primary"
              size="large"
              icon={<RocketOutlined />}
              onClick={startResearch}
              disabled={!goal.trim()}
              block
              style={{ height: 48, fontSize: 15, borderRadius: 8 }}
            >
              开始研究
            </Button>
            <Text type="secondary" style={{ fontSize: 10, display: "block", textAlign: "center", marginTop: 6 }}>
              ⌘ + Enter 快速开始
            </Text>
          </div>
        </Card>

        {/* Workflow explanation */}
        <Card
          className={styles.card}
          style={{ marginTop: 12, background: "rgba(88,166,255,0.02)" }}
        >
          <Title level={5} style={{ marginTop: 0, marginBottom: 16, fontSize: 14 }}>
            🤖 工作流程
          </Title>
          <Steps
            size="small"
            items={[
              { title: "分析目标", description: "LLM 理解你的需求", icon: <SearchOutlined /> },
              { title: "搜索代码库", description: "定位相关文件和 Issue", icon: <FileSearchOutlined /> },
              { title: "生成契约", description: "自动创建 3 文件", icon: <FormOutlined /> },
              { title: "迭代研究", description: "循环改进直到最优", icon: <ExperimentOutlined /> },
              ...(autoPr
                ? [{ title: "提交 PR", description: "自动创建 Pull Request", icon: <MergeCellsOutlined /> }]
                : [{ title: "生成报告", description: "输出研究结果报告", icon: <CheckCircleOutlined /> }]),
            ]}
          />
        </Card>

        {error && (
          <Alert type="error" message={error} closable style={{ marginTop: 12 }}
            onClose={() => setError(null)} />
        )}
      </div>
    );
  }

  // ── Running / Done state ──
  return (
    <div className={styles.container}>
      {/* Header */}
      <div className={styles.hero}>
        <RocketOutlined style={{ fontSize: 32, color: "var(--accent)", marginBottom: 8 }} />
        <Title level={2} style={{ margin: 0 }}>{taskTitle ?? "Auto Research"}</Title>
        <Space>
          <Tag color={phase === "running" ? "processing" : phase === "succeeded" ? "success" : phase === "failed" ? "error" : "warning"}>
            {phase === "running" ? currentPhaseLabel : phase === "succeeded" ? "研究完成" : phase === "failed" ? "研究失败" : "已取消"}
          </Tag>
          {taskId && <Tag>{taskId}</Tag>}
          {phase === "running" && <LoadingOutlined spin style={{ color: "var(--accent)" }} />}
          {phase === "succeeded" && <CheckCircleOutlined style={{ color: "#3fb950" }} />}
          {phase === "failed" && <CloseCircleOutlined style={{ color: "#f85149" }} />}
        </Space>
      </div>

      {/* Error */}
      {error && (
        <Alert type="error" message={error} closable style={{ marginBottom: 16 }}
          onClose={() => setError(null)} />
      )}

      {/* Reconnecting indicator */}
      {connectionState === "reconnecting" && phase === "running" && (
        <Alert
          type="warning"
          icon={<LoadingOutlined spin />}
          message="实时连接已断开，正在重新连接..."
          style={{ marginBottom: 16 }}
          banner
          role="status"
          aria-live="polite"
        />
      )}

      {/* Disconnected — manual recovery, preserve snapshot */}
      {connectionState === "disconnected" && phase === "running" && (
        <Alert
          type="warning"
          message="实时连接已断开"
          description={
            lastSyncedAtRef.current
              ? `研究任务可能仍在后台运行中（最后同步: ${new Date(lastSyncedAtRef.current).toLocaleTimeString()}）。`
              : "研究任务可能仍在后台运行中。"
          }
          showIcon
          style={{ marginBottom: 16 }}
          role="status"
          aria-live="polite"
          action={
            <Button
              type="primary"
              size="small"
              icon={<ReloadOutlined />}
              onClick={recoverConnection as unknown as () => void}
            >
              手动恢复
            </Button>
          }
        />
      )}

      {/* PR created */}
      {prUrl && (
        <Alert
          type="success"
          message="🎉 Pull Request 已创建"
          description={
            <a href={prUrl} target="_blank" rel="noopener noreferrer">{prUrl}</a>
          }
          showIcon
          icon={<BranchesOutlined />}
          style={{ marginBottom: 16 }}
        />
      )}

      {/* Stats row */}
      <Row gutter={[12, 12]}>
        <Col xs={12} sm={6}>
          <Card className={styles.statCard}>
            <Statistic title="Bug 数" value={currentBugs as number}
              suffix={baselineBugs > 0 ? `/ ${baselineBugs}` : ""}
              valueStyle={{ color: currentBugs === 0 ? "#3fb950" : "#f85149" }}
              prefix={<BugOutlined />} />
          </Card>
        </Col>
        <Col xs={12} sm={6}>
          <Card className={styles.statCard}>
            <Statistic title="评分" value={currentScore as number} precision={1}
              valueStyle={{ color: (currentScore as number) >= baselineScore ? "#3fb950" : "#d2991d" }}
              prefix={<TrophyOutlined />} />
          </Card>
        </Col>
        <Col xs={12} sm={6}>
          <Card className={styles.statCard}>
            <Statistic title="当前轮次" value={`${currentRound} / ${runState?.rounds ?? rounds}`}
              valueStyle={{ color: "#58a6ff" }} prefix={<ExperimentOutlined />} />
          </Card>
        </Col>
        <Col xs={12} sm={6}>
          <Card className={styles.statCard}>
            <Statistic title="保留/丢弃" value={keptOutcomes.length}
              suffix={`/ ${rejectedOutcomes.length}`}
              valueStyle={{ color: "#a371f7" }} prefix={<CheckCircleOutlined />} />
          </Card>
        </Col>
      </Row>

      {/* Progress bar */}
      {phase === "running" && (
        <Card className={styles.card} style={{ marginTop: 12 }}>
          <Progress
            percent={runState ? Math.min((outcomes.length / runState.rounds) * 100, 99) : 0}
            status="active"
            strokeColor={{ from: "#58a6ff", to: "#a371f7" }}
            format={() => `${currentRound}/${runState?.rounds ?? rounds} 轮`}
          />
        </Card>
      )}

      {/* ── 🧠 实时研究过程 (live event stream) ── */}
      {phase === "running" && liveFeed.length > 0 && (
        <Card
          title={
            <Space>
              <ThunderboltOutlined style={{ color: "var(--accent)" }} />
              <span>🧠 实时研究过程</span>
              <LoadingOutlined spin style={{ fontSize: 12, color: "var(--accent)" }} />
            </Space>
          }
          className={styles.card}
          style={{ marginTop: 12 }}
        >
          <div
            className={styles.liveStream}
            ref={streamRef}
            onScroll={() => {
              const el = streamRef.current;
              if (!el) return;
              const distanceToBottom = el.scrollHeight - el.scrollTop - el.clientHeight;
              shouldAutoScrollRef.current = distanceToBottom < 80;
            }}
          >
            {liveFeed.map((evt, idx) => {
              const style = getEventBubbleStyle(evt.phase);
              const isProposal = evt.phase === "proposal" || evt.phase === "proposing";
              return (
                <div key={`${evt.timestamp}-${idx}`} className={`${styles.eventBubble} ${style.className}`}>
                  <div className={styles.eventIcon}>{style.icon}</div>
                  <div className={styles.eventContent}>
                    <div className={styles.eventLabel}>
                      {style.label}
                      {evt.round ? (
                        <Tag style={{ fontSize: 10, marginLeft: 8, lineHeight: "16px", padding: "0 4px" }}>
                          第 {evt.round} 轮
                        </Tag>
                      ) : null}
                      <Text type="secondary" style={{ fontSize: 10, marginLeft: 8 }}>
                        {new Date(evt.timestamp).toLocaleTimeString()}
                      </Text>
                    </div>
                    {evt.detail && (
                      <div className={styles.eventDetail}>
                        {isProposal ? (
                          <Card
                            size="small"
                            className={styles.proposalCard}
                            style={{
                              background: "rgba(163, 113, 247, 0.06)",
                              border: "1px solid rgba(163, 113, 247, 0.12)",
                            }}
                          >
                            <pre className={styles.proposalText}>{evt.detail}</pre>
                          </Card>
                        ) : (
                          evt.detail.length > 250 ? `${evt.detail.slice(0, 250)}...` : evt.detail
                        )}
                      </div>
                    )}
                  </div>
                </div>
              );
            })}
          </div>
        </Card>
      )}

      {/* ── 🤖 轮次详情 (per-round panels) ── */}
      {outcomes.length > 0 && (
        <Card
          title={
            <Space>
              <MessageOutlined />
              <span>🤖 轮次详情</span>
            </Space>
          }
          className={styles.card}
          style={{ marginTop: 12 }}
        >
          <Collapse
            accordion
            defaultActiveKey={outcomes[outcomes.length - 1]?.round}
            style={{ background: "transparent" }}
          >
            {outcomes.map((o) => {
              const roundEvts = eventsByRound.get(o.round) ?? [];
              const proposalEvt = roundEvts.find((e) => e.phase === "proposal");
              const errorEvt = roundEvts.find(
                (e) => e.phase === "proposer_error" || e.phase === "baseline_failing"
              );
              const scoreDelta = o.improvement;
              const isSignificant = Math.abs(scoreDelta) >= 1.0;

              return (
                <Collapse.Panel
                  key={o.round}
                  header={
                    <Space wrap>
                      <Badge
                        status={o.status === "kept" ? "success" : "error"}
                        text={<Text strong>第 {o.round} 轮</Text>}
                      />
                      <Tag color={o.status === "kept" ? "green" : "red"}>
                        {o.status === "kept"
                          ? "✅ 保留"
                          : o.status === "proposer_error"
                            ? "💥 出错"
                            : "❌ 丢弃"}
                      </Tag>
                      <Text style={{ fontSize: 13, fontWeight: isSignificant ? 600 : 400 }}>
                        <span style={{ color: "var(--text-secondary)" }}>
                          {o.baseline_score.toFixed(1)}
                        </span>
                        {" → "}
                        <span style={{
                          color: (o.candidate_score ?? 0) >= o.baseline_score ? "#3fb950" : "#f85149",
                        }}>
                          {(o.candidate_score ?? 0).toFixed(1)}
                        </span>
                        {scoreDelta !== 0 && (
                          <Tag
                            color={scoreDelta > 0 ? "green" : "red"}
                            style={{ fontSize: 11, marginLeft: 6, lineHeight: "16px", padding: "0 4px" }}
                          >
                            {scoreDelta > 0 ? "+" : ""}{scoreDelta.toFixed(1)}
                          </Tag>
                        )}
                      </Text>
                    </Space>
                  }
                >
                  {/* Proposal reasoning — shown prominently */}
                  {proposalEvt?.detail && (
                    <Card
                      size="small"
                      title={
                        <Space>
                          <BulbOutlined style={{ color: "#a371f7" }} />
                          <span>💡 AI 分析与改进思路</span>
                        </Space>
                      }
                      className={styles.proposalCard}
                      style={{
                        background: "rgba(163, 113, 247, 0.04)",
                        border: "1px solid rgba(163, 113, 247, 0.1)",
                      }}
                    >
                      <pre className={styles.proposalText}>{proposalEvt.detail}</pre>
                    </Card>
                  )}

                  {/* Per-round event timeline */}
                  {roundEvts.length > 0 && (
                    <div style={{ marginBottom: 8 }}>
                      <Text type="secondary" style={{ fontSize: 11 }}>⏱ 执行流程:</Text>
                      <Timeline
                        style={{ marginTop: 4, fontSize: 12 }}
                        items={roundEvts.map((e) => ({
                          color: e.phase === "kept" ? "green"
                            : e.phase === "rejected" ? "red"
                            : e.phase === "proposal" ? "purple"
                            : e.phase === "proposer_error" || e.phase === "baseline_failing" ? "red"
                            : "blue",
                          dot: e.phase === "proposal"
                            ? <BulbOutlined style={{ fontSize: 10 }} />
                            : undefined,
                          children: (
                            <div>
                              <Tag style={{ fontSize: 10, marginRight: 6, lineHeight: "16px", padding: "0 4px" }}>
                                {DIALOG_PHASE_LABELS[e.phase] ?? e.phase}
                              </Tag>
                              {e.detail && e.phase !== "proposal" && (
                                <Text type="secondary" style={{ fontSize: 11 }}>
                                  {e.detail.slice(0, 200)}
                                </Text>
                              )}
                            </div>
                          ),
                        }))}
                      />
                    </div>
                  )}

                  {/* Error details */}
                  {errorEvt?.detail && (
                    <Alert
                      type="warning"
                      message="⚠️ 遇到问题"
                      description={errorEvt.detail}
                      style={{ marginBottom: 8 }}
                    />
                  )}

                  {/* Metrics */}
                  {o.metrics && Object.keys(o.metrics).length > 0 && (
                    <div style={{ marginBottom: 4 }}>
                      <Text type="secondary" style={{ fontSize: 11 }}>📈 评测指标:</Text>
                      <div style={{ marginTop: 4, display: "flex", gap: 6, flexWrap: "wrap" }}>
                        {Object.entries(o.metrics).map(([k, v]) => (
                          <Tag key={k} style={{ fontSize: 11 }}>
                            {k}: {String(v)}
                          </Tag>
                        ))}
                      </div>
                    </div>
                  )}

                  {/* Outcome error */}
                  {o.error && (
                    <Text type="danger" style={{ fontSize: 11 }}>
                      ⚠️ {o.error.slice(0, 300)}
                    </Text>
                  )}
                </Collapse.Panel>
              );
            })}
          </Collapse>
        </Card>
      )}

      {/* Current solution code */}
      {task && (
        <Card
          title={
            <Space>
              <CodeOutlined />
              <span>📝 当前方案</span>
              <Tag style={{ fontSize: 11 }}>{task.solution_name}</Tag>
            </Space>
          }
          className={styles.card}
          style={{ marginTop: 12 }}
        >
          <CodeBlock code={task.solution} fileName={task.solution_name} />
        </Card>
      )}

      {/* Controls */}
      <div style={{ marginTop: 16, display: "flex", justifyContent: "center", gap: 12 }}>
        {["succeeded", "failed", "cancelled"].includes(phase) && (
          <Button type="primary" size="large" icon={<RocketOutlined />} onClick={reset}>
            开始新研究
          </Button>
        )}
        {phase === "running" && (
          <Button size="large" icon={<CloseCircleOutlined />} onClick={reset} danger>
            取消研究
          </Button>
        )}
      </div>
    </div>
  );
}
