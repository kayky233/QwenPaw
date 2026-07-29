"""Multilingual task-type inference layered over the TaskSpec core model."""

from __future__ import annotations

import re
from types import ModuleType

from . import research_task_spec as task_spec_module
from .research_task_spec import (
    ResearchTaskType,
    TaskSpec,
    delivery_contract_for,
    task_spec_basis,
    validation_contract_for,
)

_LEGACY_INFER_TASK_TYPE = task_spec_module.infer_task_type

_PATTERNS: tuple[tuple[ResearchTaskType, re.Pattern[str]], ...] = (
    (
        ResearchTaskType.PERFORMANCE,
        re.compile(
            r"(?:\bperformance\b|\blatency\b|\bthroughput\b|"
            r"\bbenchmark\b|\bprofil(?:e|ing)\b|\boptimi[sz](?:e|ation)\b|"
            r"性能|延迟|吞吐|基准测试|压测|性能优化|内存占用|CPU占用)",
            re.IGNORECASE,
        ),
    ),
    (
        ResearchTaskType.REFACTOR,
        re.compile(
            r"(?:\brefactor(?:ing)?\b|\brestructure\b|\breorganize\b|"
            r"\bdecouple\b|\bextract\s+(?:module|service|class)\b|"
            r"重构|拆分模块|抽取服务|解耦|重新组织|架构调整)",
            re.IGNORECASE,
        ),
    ),
    (
        ResearchTaskType.BUG_FIX,
        re.compile(
            r"(?:\bbug\b|\bfix\b|\bdefect\b|\bregression\b|\bcrash\b|"
            r"\berror\b|\bfailure\b|\bbroken\b|\bincorrect\b|"
            r"修复|解决.{0,12}(?:问题|错误|异常|故障)|错误|异常|崩溃|"
            r"回归问题|故障|不生效|无法使用|失败)",
            re.IGNORECASE,
        ),
    ),
    (
        ResearchTaskType.FEATURE,
        re.compile(
            r"(?:\bfeature\b|\benhancement\b|\bimplement\b|\badd\b|"
            r"\bsupport\b|\bcapability\b|\bintroduce\b|\bnew\s+api\b|"
            r"新增|添加|实现|支持|功能|能力|接入|扩展|新接口)",
            re.IGNORECASE,
        ),
    ),
    (
        ResearchTaskType.RESEARCH,
        re.compile(
            r"(?:\bresearch\b|\binvestigate\b|\bcompare\b|\bevaluate\b|"
            r"\bexperiment\b|\bstudy\b|\bexplore\b|\bfeasibility\b|"
            r"调研|研究|对比|评估|实验|探索|可行性分析|技术选型)",
            re.IGNORECASE,
        ),
    ),
)
_ISSUE_RE = re.compile(r"(?:issues?/|#)(\d{1,10})", re.IGNORECASE)


def infer_multilingual_task_type(
    goal: str,
    plan_markdown: str = "",
) -> tuple[ResearchTaskType, str, float, str]:
    """Infer English or Chinese intent while preserving explicit declarations."""

    explicit = _LEGACY_INFER_TASK_TYPE(goal, plan_markdown)
    if explicit[1] == "explicit_plan":
        return explicit

    text = f"{goal}\n{plan_markdown}"
    for task_type, pattern in _PATTERNS:
        if pattern.search(text):
            return (
                task_type,
                "inferred_multilingual_text",
                0.85,
                f"Matched multilingual {task_type.value} intent.",
            )

    if _ISSUE_RE.search(text):
        return (
            ResearchTaskType.BUG_FIX,
            "legacy_issue_default",
            0.65,
            "Issue-oriented tasks retain the historical bug-fix contract.",
        )
    return explicit


def build_multilingual_task_spec(
    goal: str,
    plan_markdown: str = "",
) -> TaskSpec:
    """Build a TaskSpec using multilingual classification rules."""

    task_type, source, confidence, rationale = infer_multilingual_task_type(
        goal,
        plan_markdown,
    )
    return TaskSpec(
        task_type=task_type,
        validation=validation_contract_for(task_type),
        delivery=delivery_contract_for(task_type),
        source=source,
        confidence=confidence,
        rationale=rationale,
        basis_hash=task_spec_basis(goal, plan_markdown),
    )


def install_research_task_spec_inference(research_module: ModuleType) -> None:
    """Replace inferred classification while retaining the same public contract."""

    task_spec_module.infer_task_type = infer_multilingual_task_type
    task_spec_module.build_task_spec = build_multilingual_task_spec
    research_module._build_task_spec = build_multilingual_task_spec
    research_module._infer_task_type = infer_multilingual_task_type
    research_module._task_spec_inference_installed = True
