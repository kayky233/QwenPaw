import { useEffect } from "react";
import {
  CloseOutlined,
  ExperimentOutlined,
  ReloadOutlined,
  StopOutlined,
} from "@ant-design/icons";
import { Button, Empty, Progress, Tag, Tooltip } from "antd";

import { useResearchStore } from "./researchStore";
import styles from "./ResearchSidePanel.module.less";

interface ResearchSidePanelProps {
  mode: "side-panel" | "full-page";
  runId?: string | null;
}

const statusColor: Record<string, string> = {
  queued: "default",
  running: "processing",
  completed: "success",
  failed: "error",
  cancelled: "warning",
};

export function ResearchSidePanel({
  mode,
  runId,
}: ResearchSidePanelProps) {
  const activeRunId = useResearchStore((state) => state.activeRunId);
  const activePlanId = useResearchStore((state) => state.activePlanId);
  const plan = useResearchStore((state) =>
    activePlanId ? state.plans[activePlanId] : undefined,
  );
  const openRun = useResearchStore((state) => state.openRun);
  const closePanel = useResearchStore((state) => state.closePanel);
  const refreshSnapshot = useResearchStore(
    (state) => state.refreshSnapshot,
  );
  const stopRun = useResearchStore((state) => state.stopRun);
  const resolvedRunId = runId ?? activeRunId;
  const snapshot = useResearchStore((state) =>
    resolvedRunId ? state.snapshots[resolvedRunId] : undefined,
  );
  const connection = useResearchStore((state) =>
    resolvedRunId
      ? state.connectionStates[resolvedRunId] ?? "idle"
      : "idle",
  );
  const error = useResearchStore((state) =>
    resolvedRunId ? state.errors[resolvedRunId] : null,
  );

  useEffect(() => {
    if (resolvedRunId && !snapshot) void openRun(resolvedRunId);
  }, [resolvedRunId, snapshot, openRun]);

  if ((!resolvedRunId || !snapshot) && plan) {
    const awaitingApproval = plan.status === "awaiting_approval";
    const planProgress = awaitingApproval
      ? 100
      : Math.min(90, Math.max(8, plan.events.length * 12));
    return (
      <aside
        className={`${styles.panel} ${styles[mode === "side-panel" ? "side" : "full"]}`}
        aria-label="AutoResearch 规划面板"
      >
        <header className={styles.header}>
          <div>
            <span className={styles.eyebrow}>AUTORESEARCH · DISCOVERY</span>
            <h2>
              <ExperimentOutlined /> {plan.goal}
            </h2>
          </div>
          {mode === "side-panel" && (
            <Button
              type="text"
              icon={<CloseOutlined />}
              aria-label="关闭研究面板"
              onClick={closePanel}
            />
          )}
        </header>
        <section className={styles.overview}>
          <div className={styles.statusLine}>
            <Tag
              color={
                plan.status === "failed"
                  ? "error"
                  : awaitingApproval
                    ? "gold"
                    : "processing"
              }
            >
              {plan.status}
            </Tag>
            <span>
              {awaitingApproval
                ? "方案已生成，等待审批"
                : "正在调研并生成研究计划"}
            </span>
          </div>
          <Progress
            percent={planProgress}
            showInfo={false}
            status={
              plan.status === "failed"
                ? "exception"
                : awaitingApproval
                  ? "success"
                  : "active"
            }
          />
        </section>
        {plan.error && <p className={styles.error}>{plan.error}</p>}
        {plan.brief && (
          <section className={styles.brief}>
            <h3>已发现的优化方向</h3>
            {plan.brief.candidate_directions
              .slice()
              .sort((a, b) => a.priority - b.priority)
              .map((direction) => (
                <div key={direction.id} className={styles.direction}>
                  <span>{direction.priority}</span>
                  <div>
                    <strong>{direction.title}</strong>
                    <small>{direction.reason}</small>
                  </div>
                  <Tag>{direction.risk}</Tag>
                </div>
              ))}
          </section>
        )}
        {plan.plan_markdown && (
          <section className={styles.plan}>
            <h3>待审批方案</h3>
            <pre>{plan.plan_markdown}</pre>
          </section>
        )}
        <section className={styles.rounds}>
          <h3>规划进度</h3>
          {plan.events.length === 0 ? (
            <p className={styles.muted}>任务已接收，等待 Discovery 输出…</p>
          ) : (
            plan.events.slice(-8).map((event, index) => (
              <article
                key={`${event.timestamp}-${index}`}
                className={styles.planningEvent}
              >
                <Tag>{event.phase}</Tag>
                <span>{event.detail}</span>
              </article>
            ))
          )}
        </section>
      </aside>
    );
  }

  if (!resolvedRunId || !snapshot) {
    return (
      <aside
        className={`${styles.panel} ${styles[mode === "side-panel" ? "side" : "full"]}`}
      >
        <Empty
          image={Empty.PRESENTED_IMAGE_SIMPLE}
          description="暂无研究任务"
        />
      </aside>
    );
  }

  const percent = snapshot.rounds
    ? Math.round((snapshot.completed_rounds / snapshot.rounds) * 100)
    : 0;
  const bestScore = snapshot.outcomes.reduce(
    (best, outcome) => Math.max(best, outcome.candidate_score ?? best),
    0,
  );

  return (
    <aside
      className={`${styles.panel} ${styles[mode === "side-panel" ? "side" : "full"]}`}
      aria-label="AutoResearch 运行面板"
    >
      <header className={styles.header}>
        <div>
          <span className={styles.eyebrow}>AUTORESEARCH</span>
          <h2>
            <ExperimentOutlined /> {snapshot.task_id}
          </h2>
        </div>
        <div className={styles.actions}>
          <Tooltip title="刷新状态">
            <Button
              type="text"
              icon={<ReloadOutlined />}
              aria-label="刷新研究状态"
              onClick={() => void refreshSnapshot(resolvedRunId)}
            />
          </Tooltip>
          {mode === "side-panel" && (
            <Tooltip title="关闭面板">
              <Button
                type="text"
                icon={<CloseOutlined />}
                aria-label="关闭研究面板"
                onClick={closePanel}
              />
            </Tooltip>
          )}
        </div>
      </header>

      <section className={styles.overview}>
        <div className={styles.statusLine}>
          <Tag color={statusColor[snapshot.status] ?? "default"}>
            {snapshot.status}
          </Tag>
          <span>{snapshot.phase}</span>
          <i data-state={connection}>{connection}</i>
        </div>
        <Progress
          percent={percent}
          size="small"
          status={snapshot.status === "failed" ? "exception" : "active"}
        />
        <div className={styles.metrics}>
          <div>
            <strong>{snapshot.current_round ?? 0}</strong>
            <span>当前轮次 / {snapshot.rounds}</span>
          </div>
          <div>
            <strong>{bestScore.toFixed(1)}</strong>
            <span>最佳评分</span>
          </div>
          <div>
            <strong>{snapshot.outcomes.length}</strong>
            <span>实验结果</span>
          </div>
        </div>
      </section>

      {(error || snapshot.error) && (
        <p className={styles.error}>{error || snapshot.error}</p>
      )}

      {snapshot.research_brief && (
        <section className={styles.brief}>
          <h3>优化方向</h3>
          {snapshot.research_brief.candidate_directions
            .slice()
            .sort((a, b) => a.priority - b.priority)
            .map((direction) => (
              <div key={direction.id} className={styles.direction}>
                <span>{direction.priority}</span>
                <div>
                  <strong>{direction.title}</strong>
                  <small>{direction.reason}</small>
                </div>
                <Tag>{direction.risk}</Tag>
              </div>
            ))}
        </section>
      )}

      <section className={styles.rounds}>
        <h3>实验轮次</h3>
        {snapshot.outcomes.length === 0 ? (
          <p className={styles.muted}>等待第一轮结果…</p>
        ) : (
          snapshot.outcomes.map((outcome) => (
            <article key={outcome.round} className={styles.round}>
              <span>R{outcome.round}</span>
              <Tag
                color={
                  outcome.status === "kept"
                    ? "success"
                    : outcome.status === "rejected"
                      ? "default"
                      : "error"
                }
              >
                {outcome.status}
              </Tag>
              <strong>
                {outcome.candidate_score?.toFixed(1) ?? "—"}
              </strong>
              <small>{outcome.error || `${outcome.improvement >= 0 ? "+" : ""}${outcome.improvement.toFixed(1)}`}</small>
            </article>
          ))
        )}
      </section>

      {["queued", "running"].includes(snapshot.status) && (
        <footer className={styles.footer}>
          <Button
            danger
            icon={<StopOutlined />}
            onClick={() => void stopRun(resolvedRunId)}
          >
            停止研究
          </Button>
        </footer>
      )}
    </aside>
  );
}

export default ResearchSidePanel;
