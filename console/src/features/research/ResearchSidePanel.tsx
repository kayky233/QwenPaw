import { useEffect, useState } from "react";
import {
  CheckOutlined,
  CloseOutlined,
  CloseCircleOutlined,
  DownloadOutlined,
  ExperimentOutlined,
  ExportOutlined,
  FullscreenOutlined,
  ReloadOutlined,
  SaveOutlined,
  StopOutlined,
} from "@ant-design/icons";
import { Button, Empty, Input, Modal, Progress, Tag, Tooltip } from "antd";

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
  needs_revision: "warning",
  cancelled: "warning",
};

export function ResearchSidePanel({ mode, runId }: ResearchSidePanelProps) {
  const activeRunId = useResearchStore((state) => state.activeRunId);
  const activePlanId = useResearchStore((state) => state.activePlanId);
  const plan = useResearchStore((state) =>
    activePlanId ? state.plans[activePlanId] : undefined,
  );
  const openRun = useResearchStore((state) => state.openRun);
  const refreshPlan = useResearchStore((state) => state.refreshPlan);
  const closePanel = useResearchStore((state) => state.closePanel);
  const refreshSnapshot = useResearchStore((state) => state.refreshSnapshot);
  const stopRun = useResearchStore((state) => state.stopRun);
  const savePlan = useResearchStore((state) => state.savePlan);
  const proposePlanRevision = useResearchStore(
    (state) => state.proposePlanRevision,
  );
  const acceptPlanRevision = useResearchStore(
    (state) => state.acceptPlanRevision,
  );
  const rejectPlanRevision = useResearchStore(
    (state) => state.rejectPlanRevision,
  );
  const approvePlan = useResearchStore((state) => state.approvePlan);
  const rejectPlan = useResearchStore((state) => state.rejectPlan);
  const [planDraft, setPlanDraft] = useState("");
  const [planAction, setPlanAction] = useState<
    | "save"
    | "propose_revision"
    | "accept_revision"
    | "reject_revision"
    | "approve"
    | "reject"
    | null
  >(null);
  const [planActionError, setPlanActionError] = useState("");
  const [reviewOpen, setReviewOpen] = useState(false);
  const resolvedRunId = runId ?? activeRunId;
  const snapshot = useResearchStore((state) =>
    resolvedRunId ? state.snapshots[resolvedRunId] : undefined,
  );
  const connection = useResearchStore((state) =>
    resolvedRunId ? state.connectionStates[resolvedRunId] ?? "idle" : "idle",
  );
  const error = useResearchStore((state) =>
    resolvedRunId ? state.errors[resolvedRunId] : null,
  );

  useEffect(() => {
    if (resolvedRunId && !snapshot) void openRun(resolvedRunId);
  }, [resolvedRunId, snapshot, openRun]);

  useEffect(() => {
    if (activePlanId && !plan) {
      void refreshPlan(activePlanId);
    }
  }, [activePlanId, plan, refreshPlan]);

  useEffect(() => {
    setPlanDraft(plan?.plan_markdown ?? "");
    setPlanActionError("");
  }, [plan?.plan_id, plan?.revision, plan?.plan_markdown]);

  const handleSavePlan = async () => {
    if (!plan) return;
    setPlanAction("save");
    setPlanActionError("");
    try {
      await savePlan(plan.plan_id, planDraft);
    } catch (actionError) {
      setPlanActionError(
        actionError instanceof Error ? actionError.message : "保存方案失败",
      );
    } finally {
      setPlanAction(null);
    }
  };

  const handleApprovePlan = async () => {
    if (!plan) return;
    setPlanAction("approve");
    setPlanActionError("");
    try {
      if (planDraft !== (plan.plan_markdown ?? "")) {
        await savePlan(plan.plan_id, planDraft);
      }
      await approvePlan(plan.plan_id);
    } catch (actionError) {
      setPlanActionError(
        actionError instanceof Error ? actionError.message : "批准方案失败",
      );
    } finally {
      setPlanAction(null);
    }
  };

  const handleProposeRevision = async () => {
    if (!plan) return;
    setPlanAction("propose_revision");
    setPlanActionError("");
    try {
      await proposePlanRevision(plan.plan_id);
    } catch (actionError) {
      setPlanActionError(
        actionError instanceof Error ? actionError.message : "生成修订建议失败",
      );
    } finally {
      setPlanAction(null);
    }
  };

  const handleAcceptRevision = async () => {
    if (!plan) return;
    setPlanAction("accept_revision");
    setPlanActionError("");
    try {
      await acceptPlanRevision(plan.plan_id);
    } catch (actionError) {
      setPlanActionError(
        actionError instanceof Error ? actionError.message : "接受修订建议失败",
      );
    } finally {
      setPlanAction(null);
    }
  };

  const handleRejectRevision = async () => {
    if (!plan) return;
    setPlanAction("reject_revision");
    setPlanActionError("");
    try {
      await rejectPlanRevision(plan.plan_id);
    } catch (actionError) {
      setPlanActionError(
        actionError instanceof Error ? actionError.message : "拒绝修订建议失败",
      );
    } finally {
      setPlanAction(null);
    }
  };

  const handleRejectPlan = async () => {
    if (!plan) return;
    setPlanAction("reject");
    setPlanActionError("");
    try {
      await rejectPlan(plan.plan_id, "用户拒绝当前研究方案");
    } catch (actionError) {
      setPlanActionError(
        actionError instanceof Error ? actionError.message : "拒绝方案失败",
      );
    } finally {
      setPlanAction(null);
    }
  };

  const handleDownloadReport = () => {
    if (!plan?.validation_report) return;
    const blob = new Blob([plan.validation_report], {
      type: "text/markdown;charset=utf-8",
    });
    const url = URL.createObjectURL(blob);
    const anchor = document.createElement("a");
    anchor.href = url;
    anchor.download = `autoresearch-${plan.plan_id.replace(
      /[^a-zA-Z0-9._-]/g,
      "-",
    )}-validation.md`;
    anchor.click();
    URL.revokeObjectURL(url);
  };

  if ((!resolvedRunId || !snapshot) && plan) {
    const awaitingApproval = plan.status === "awaiting_approval";
    const needsRevision = plan.status === "needs_revision";
    const editablePlan = awaitingApproval || needsRevision;
    const environmentBlocked =
      plan.environment_compatibility === "incompatible";
    const planFinished = ["completed", "failed", "rejected"].includes(
      plan.status,
    );
    const planProgress =
      awaitingApproval || plan.status === "completed"
        ? 100
        : plan.status === "executing"
        ? 88
        : Math.min(82, Math.max(8, plan.events.length * 10));
    return (
      <aside
        className={`${styles.panel} ${
          styles[mode === "side-panel" ? "side" : "full"]
        }`}
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
                  : needsRevision
                  ? "warning"
                  : plan.status === "completed"
                  ? "success"
                  : plan.status === "rejected"
                  ? "default"
                  : awaitingApproval
                  ? "gold"
                  : "processing"
              }
            >
              {plan.status}
            </Tag>
            <span>
              {needsRevision
                ? "验证结果已保留，请修订计划后继续"
                : awaitingApproval
                ? "方案已生成，等待审批"
                : plan.status === "approved"
                ? "方案已批准，准备执行"
                : plan.status === "executing"
                ? "正在隔离工作树中实现并验证"
                : plan.status === "completed"
                ? "修复已完成并推送"
                : plan.status === "failed"
                ? "验证未通过，未提交或推送"
                : plan.status === "rejected"
                ? "方案已拒绝"
                : "正在调研并生成研究计划"}
            </span>
          </div>
          <Progress
            percent={planProgress}
            showInfo={false}
            status={
              plan.status === "failed"
                ? "exception"
                : needsRevision
                ? "normal"
                : planFinished
                ? "success"
                : "active"
            }
          />
        </section>
        {planActionError && <p className={styles.error}>{planActionError}</p>}
        {plan.error && plan.status !== "rejected" && (
          <p className={styles.error}>{plan.error}</p>
        )}
        {plan.status === "rejected" && (
          <section className={styles.validation}>
            <div className={styles.sectionHeading}>
              <h3>本次研究已结束</h3>
              <Tag>不会执行</Tag>
            </div>
            <p className={styles.muted}>
              {plan.rejection_reason || "你已放弃当前方案。"}
              在聊天中发送新的 <code>/research &lt;目标&gt;</code>{" "}
              即可重新开始。
            </p>
          </section>
        )}
        {plan.revision_proposal ? (
          <section className={styles.validation}>
            <div className={styles.sectionHeading}>
              <h3>建议修订</h3>
              <Tag color="gold">待决定</Tag>
            </div>
            <p className={styles.muted}>
              {plan.revision_proposal_reason ||
                "系统根据当前计划和验证记录生成了建议。"}
            </p>
            <pre className={styles.validationReport}>
              {plan.revision_proposal}
            </pre>
            <div className={styles.proposalActions}>
              <Button
                type="primary"
                icon={<CheckOutlined />}
                aria-label="接受修订"
                loading={planAction === "accept_revision"}
                disabled={Boolean(planAction)}
                onClick={() => void handleAcceptRevision()}
              >
                接受修订
              </Button>
              <Button
                icon={<CloseCircleOutlined />}
                aria-label="拒绝修订"
                loading={planAction === "reject_revision"}
                disabled={Boolean(planAction)}
                onClick={() => void handleRejectRevision()}
              >
                拒绝修订
              </Button>
            </div>
          </section>
        ) : (
          needsRevision && (
            <section className={styles.validation}>
              <div className={styles.sectionHeading}>
                <h3>修订建议</h3>
                <Button
                  icon={<ReloadOutlined />}
                  loading={planAction === "propose_revision"}
                  disabled={Boolean(planAction)}
                  onClick={() => void handleProposeRevision()}
                >
                  生成建议
                </Button>
              </div>
              <p className={styles.muted}>
                系统会基于当前计划和验证记录提出建议，不会自动批准或执行。
              </p>
            </section>
          )
        )}
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
          <section className={styles.validation}>
            <div className={styles.sectionHeading}>
              <h3>执行环境</h3>
              <Tag
                color={
                  environmentBlocked
                    ? "error"
                    : plan.environment_compatibility === "compatible"
                    ? "success"
                    : "warning"
                }
              >
                {environmentBlocked
                  ? "环境不兼容"
                  : plan.environment_compatibility === "compatible"
                  ? "环境兼容"
                  : "待人工核对"}
              </Tag>
            </div>
            {plan.current_environment && (
              <p>
                当前环境：<code>{plan.current_environment}</code>
              </p>
            )}
            {plan.environment_compatibility_reason && (
              <p className={environmentBlocked ? styles.error : styles.muted}>
                {plan.environment_compatibility_reason}
              </p>
            )}
          </section>
        )}
        {plan.plan_markdown && (
          <section className={styles.plan}>
            <div className={styles.sectionHeading}>
              <h3>
                {needsRevision
                  ? "需要修订计划"
                  : awaitingApproval
                  ? "待审批方案"
                  : "已批准方案"}
              </h3>
              <div className={styles.planHeadingActions}>
                <span>
                  revision {plan.revision} · {plan.content_hash.slice(0, 12)}
                </span>
                <Tooltip title="在大窗口中审阅计划和研究意图">
                  <Button
                    type="text"
                    icon={<FullscreenOutlined />}
                    aria-label="全屏审阅计划"
                    onClick={() => setReviewOpen(true)}
                  />
                </Tooltip>
              </div>
            </div>
            {editablePlan ? (
              <Input.TextArea
                aria-label="研究方案"
                value={planDraft}
                onChange={(event) => setPlanDraft(event.target.value)}
                rows={16}
                className={styles.planEditor}
              />
            ) : (
              <pre>{plan.plan_markdown}</pre>
            )}
          </section>
        )}
        {(plan.branch ||
          plan.commit_sha ||
          plan.pr_url ||
          plan.test_summary) && (
          <section className={styles.artifacts}>
            <h3>执行结果</h3>
            {plan.upstream_repository && (
              <div>
                <span>上游仓库</span>
                <code>{plan.upstream_repository}</code>
              </div>
            )}
            {plan.push_repository && (
              <div>
                <span>推送 Fork</span>
                <code>{plan.push_repository}</code>
              </div>
            )}
            {plan.branch && (
              <div>
                <span>分支</span>
                <code>{plan.branch}</code>
              </div>
            )}
            {plan.commit_sha && (
              <div>
                <span>Commit</span>
                <code>{plan.commit_sha}</code>
              </div>
            )}
            {plan.pr_url && (
              <div>
                <span>PR</span>
                <a href={plan.pr_url} target="_blank" rel="noreferrer">
                  <ExportOutlined /> {plan.pr_url}
                </a>
              </div>
            )}
            {plan.test_summary && <pre>{plan.test_summary}</pre>}
          </section>
        )}
        {plan.unapproved_paths?.length > 0 && (
          <section className={styles.validation}>
            <h3>未审批的变更路径</h3>
            <p className={styles.muted}>
              当前工作树和已有结果已保留。请在计划中明确加入或移除这些路径。
            </p>
            <pre>{plan.unapproved_paths.join("\n")}</pre>
          </section>
        )}
        {(plan.reproduction_status !== "pending" ||
          plan.verification_status !== "pending" ||
          plan.validation_report) && (
          <section className={styles.validation}>
            <div className={styles.sectionHeading}>
              <h3>复现与修复验证</h3>
              {plan.validation_report && (
                <Tooltip title="下载 Markdown 报告">
                  <Button
                    type="text"
                    icon={<DownloadOutlined />}
                    aria-label="下载验证报告"
                    onClick={handleDownloadReport}
                  />
                </Tooltip>
              )}
            </div>
            <div className={styles.validationStatus}>
              <div>
                <span>复现状态</span>
                <Tag
                  color={
                    plan.reproduction_status === "reproduced"
                      ? "success"
                      : plan.reproduction_status === "not_reproduced"
                      ? "warning"
                      : plan.reproduction_status === "error"
                      ? "error"
                      : "default"
                  }
                >
                  {plan.reproduction_status}
                </Tag>
                {plan.reproduction_summary && (
                  <small>{plan.reproduction_summary}</small>
                )}
              </div>
              <div>
                <span>修复验证</span>
                <Tag
                  color={
                    plan.verification_status === "passed"
                      ? "success"
                      : plan.verification_status === "failed" ||
                        plan.verification_status === "error"
                      ? "error"
                      : "default"
                  }
                >
                  {plan.verification_status}
                </Tag>
                {plan.verification_summary && (
                  <small>{plan.verification_summary}</small>
                )}
              </div>
            </div>
            {plan.validation_report && (
              <pre className={styles.validationReport}>
                {plan.validation_report}
              </pre>
            )}
            {(plan.validation_attempts?.length ?? 0) > 1 && (
              <div>
                <h4>历史验证尝试</h4>
                {plan.validation_attempts
                  .slice(0, -1)
                  .reverse()
                  .map((attempt) => (
                    <details key={`${attempt.attempt}-${attempt.timestamp}`}>
                      <summary>
                        尝试 {attempt.attempt} · revision {attempt.revision} ·{" "}
                        {attempt.failure_category || "validation"}
                      </summary>
                      <pre className={styles.validationReport}>
                        {attempt.validation_report}
                      </pre>
                    </details>
                  ))}
              </div>
            )}
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
        <Modal
          title="审阅研究计划"
          open={reviewOpen}
          onCancel={() => setReviewOpen(false)}
          centered
          width="min(1080px, calc(100vw - 32px))"
          zIndex={1300}
          className={styles.reviewModal}
          footer={
            editablePlan
              ? [
                  <Button key="close" onClick={() => setReviewOpen(false)}>
                    返回侧栏
                  </Button>,
                  <Button
                    key="save"
                    icon={<SaveOutlined />}
                    loading={planAction === "save"}
                    disabled={
                      Boolean(planAction) ||
                      !planDraft.trim() ||
                      planDraft === plan.plan_markdown
                    }
                    onClick={() => void handleSavePlan()}
                  >
                    保存修改
                  </Button>,
                  <Button
                    key="approve"
                    type="primary"
                    icon={<CheckOutlined />}
                    loading={planAction === "approve"}
                    disabled={
                      Boolean(planAction) ||
                      !planDraft.trim() ||
                      environmentBlocked
                    }
                    onClick={() => void handleApprovePlan()}
                  >
                    批准执行
                  </Button>,
                ]
              : [
                  <Button key="close" onClick={() => setReviewOpen(false)}>
                    关闭
                  </Button>,
                ]
          }
        >
          <div className={styles.reviewIntent}>
            <div>
              <span>研究意图</span>
              <strong>{plan.goal}</strong>
              {plan.task_title && <small>{plan.task_title}</small>}
            </div>
            <Tag
              color={
                environmentBlocked
                  ? "error"
                  : plan.environment_compatibility === "compatible"
                  ? "success"
                  : "warning"
              }
            >
              {environmentBlocked
                ? "环境不兼容"
                : plan.environment_compatibility === "compatible"
                ? "环境兼容"
                : "环境待核对"}
            </Tag>
          </div>
          {plan.environment_compatibility_reason && (
            <p className={environmentBlocked ? styles.error : styles.muted}>
              {plan.environment_compatibility_reason}
            </p>
          )}
          <div className={styles.reviewPlanHeading}>
            <h3>{editablePlan ? "可编辑执行计划" : "已批准执行计划"}</h3>
            <span>
              revision {plan.revision} · {plan.content_hash.slice(0, 12)}
            </span>
          </div>
          {editablePlan ? (
            <Input.TextArea
              aria-label="全屏研究方案"
              value={planDraft}
              onChange={(event) => setPlanDraft(event.target.value)}
              autoSize={{ minRows: 20, maxRows: 32 }}
              className={`${styles.planEditor} ${styles.expandedPlanEditor}`}
            />
          ) : (
            <pre className={styles.expandedPlan}>{plan.plan_markdown}</pre>
          )}
        </Modal>
        {editablePlan && (
          <footer className={styles.approvalFooter}>
            <Button
              icon={<SaveOutlined />}
              aria-label="保存方案"
              loading={planAction === "save"}
              disabled={
                Boolean(planAction) ||
                !planDraft.trim() ||
                planDraft === plan.plan_markdown
              }
              onClick={() => void handleSavePlan()}
            >
              保存
            </Button>
            <Button
              type="primary"
              icon={<CheckOutlined />}
              aria-label={needsRevision ? "重新审批并继续" : "批准执行"}
              loading={planAction === "approve"}
              disabled={
                Boolean(planAction) || !planDraft.trim() || environmentBlocked
              }
              onClick={() => void handleApprovePlan()}
            >
              {needsRevision ? "重新审批并继续" : "批准执行"}
            </Button>
            <Button
              danger
              icon={<CloseCircleOutlined />}
              aria-label="放弃本次研究"
              loading={planAction === "reject"}
              disabled={Boolean(planAction)}
              onClick={() =>
                Modal.confirm({
                  title: "确认放弃本次研究？",
                  content:
                    "当前计划将标记为已拒绝，不会执行。已有验证报告仍会保留。",
                  okText: "确认放弃",
                  cancelText: "继续修改",
                  okButtonProps: { danger: true },
                  onOk: handleRejectPlan,
                })
              }
            >
              放弃本次研究
            </Button>
          </footer>
        )}
      </aside>
    );
  }

  if (!resolvedRunId || !snapshot) {
    return (
      <aside
        className={`${styles.panel} ${
          styles[mode === "side-panel" ? "side" : "full"]
        }`}
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
      className={`${styles.panel} ${
        styles[mode === "side-panel" ? "side" : "full"]
      }`}
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
              <strong>{outcome.candidate_score?.toFixed(1) ?? "—"}</strong>
              <small>
                {outcome.error ||
                  `${
                    outcome.improvement >= 0 ? "+" : ""
                  }${outcome.improvement.toFixed(1)}`}
              </small>
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
