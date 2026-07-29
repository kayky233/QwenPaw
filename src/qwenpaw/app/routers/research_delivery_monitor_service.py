"""Background GitHub CI and review monitoring for AutoResearch dialogs."""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import ModuleType

from ...research_ledger.delivery_lifecycle import CICheckStatus
from ...research_ledger.github_delivery_monitor import (
    fetch_github_delivery_snapshot,
)


def install_research_delivery_monitor_service(
    research_module: ModuleType,
) -> None:
    if getattr(research_module, "_delivery_monitor_installed", False):
        return

    research_module._dialog_delivery_monitor_tasks = {}

    async def monitor(plan_id: str) -> None:
        try:
            # Let the legacy executor finish its final completed event, which the
            # delivery lifecycle converts into ci_waiting.
            await asyncio.sleep(0)
            for attempt in range(1, 41):
                dialog = research_module._dialog_runs.get(plan_id)
                if dialog is None:
                    return
                if dialog.status not in {"ci_waiting", "review_waiting"}:
                    return
                if research_module.shutil.which("gh") is None:
                    research_module._dialog_emit(
                        plan_id,
                        "ci_monitor_unavailable",
                        "未检测到 GitHub CLI，交付状态保持等待。",
                    )
                    return

                snapshot = await fetch_github_delivery_snapshot(
                    repository=dialog.upstream_repository,
                    pr_url=dialog.pr_url,
                    worktree=Path(dialog.worktree_path),
                    run_process=research_module._run_process,
                    attempt=attempt,
                )
                research_module._record_dialog_ci_report(
                    plan_id,
                    snapshot.ci_report,
                )
                if snapshot.ci_report.status == CICheckStatus.PASSED:
                    research_module._record_dialog_review_threads(
                        plan_id,
                        snapshot.review_threads,
                    )
                    return
                if snapshot.ci_report.status in {
                    CICheckStatus.FAILED,
                    CICheckStatus.CANCELLED,
                }:
                    return
                await asyncio.sleep(30)

            research_module._dialog_emit(
                plan_id,
                "ci_monitor_timeout",
                "CI 监控超过等待上限，任务仍保持 ci_waiting。",
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            research_module._log.exception(
                "Delivery monitoring failed for %s",
                plan_id,
            )
            research_module._dialog_emit(
                plan_id,
                "ci_monitor_failed",
                f"GitHub CI/Review 监控失败：{type(exc).__name__}: {exc}",
            )
        finally:
            research_module._dialog_delivery_monitor_tasks.pop(plan_id, None)

    def schedule(plan_id: str) -> None:
        existing = research_module._dialog_delivery_monitor_tasks.get(plan_id)
        if existing is not None and not existing.done():
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        research_module._dialog_delivery_monitor_tasks[plan_id] = (
            loop.create_task(monitor(plan_id))
        )

    base_emit = research_module._dialog_emit

    def emit(plan_id: str, phase: str, detail: str = "") -> None:
        base_emit(plan_id, phase, detail)
        if phase == "pr_created":
            schedule(plan_id)

    base_close = research_module.close_research_ledger

    async def close() -> None:
        tasks = tuple(research_module._dialog_delivery_monitor_tasks.values())
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        research_module._dialog_delivery_monitor_tasks.clear()
        await base_close()

    research_module._monitor_dialog_delivery = monitor
    research_module._schedule_dialog_delivery_monitor = schedule
    research_module._dialog_emit = emit
    research_module.close_research_ledger = close
    research_module._delivery_monitor_installed = True
