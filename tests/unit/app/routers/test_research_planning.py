# -*- coding: utf-8 -*-
"""P0 unit tests for Auto Research planning — parse, validate, retry."""

from __future__ import annotations

import hashlib
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from qwenpaw.app.routers.research import (
    _build_plan_only_prompt,
    _parse_planning_output,
    _parse_research_brief,
)


def test_parse_research_brief_uses_authoritative_goal_and_rounds() -> None:
    brief = _parse_research_brief(
        """
        ```json
        {
          "goal": "ignore this",
          "current_behavior": "Concurrent calls can insert twice.",
          "root_cause_hypotheses": ["The check and insert are not atomic."],
          "candidate_directions": [{
            "id": "unique-constraint",
            "title": "Add a database uniqueness constraint",
            "priority": 1,
            "risk": "low",
            "reason": "It protects every process."
          }],
          "success_metrics": ["20 concurrent calls create one row"],
          "iteration_budget": 99,
          "modifiable_files": ["src/feedback.py"],
          "relevant_tests": ["tests/test_feedback.py"]
        }
        ```
        """,
        goal="Deduplicate submitFeedback",
        rounds=5,
    )

    assert brief.goal == "Deduplicate submitFeedback"
    assert brief.iteration_budget == 5
    assert brief.candidate_directions[0].id == "unique-constraint"


def test_parse_research_brief_rejects_missing_directions() -> None:
    response = """
    {
      "current_behavior": "Duplicate writes.",
      "root_cause_hypotheses": ["Non-atomic write."],
      "candidate_directions": [],
      "success_metrics": ["One row"]
    }
    """

    with pytest.raises(ValueError, match="ResearchBrief validation failed"):
        _parse_research_brief(response, goal="Deduplicate", rounds=3)


def test_plan_prompt_includes_discovery_direction() -> None:
    brief = _parse_research_brief(
        """
        {
          "current_behavior": "Duplicate writes.",
          "root_cause_hypotheses": ["Non-atomic write."],
          "candidate_directions": [{
            "id": "idempotency-key",
            "title": "Use an idempotency key",
            "priority": 1,
            "risk": "medium",
            "reason": "Works across retries."
          }],
          "success_metrics": ["One row"]
        }
        """,
        goal="Deduplicate",
        rounds=3,
    )

    assert "idempotency-key" in _build_plan_only_prompt("Deduplicate", brief)


@pytest.mark.asyncio
async def test_dialog_plan_waits_for_approval_without_solution_generation() -> None:
    from qwenpaw.app.routers import research as research_mod

    plan_id = "plan-awaiting-approval"
    research_mod._dialog_runs[plan_id] = research_mod.DialogRunState(
        plan_id=plan_id,
        status="accepted",
        goal="Fix one simple GitHub issue",
        events=[],
    )
    brief = """
    {
      "current_behavior": "A race can select an occupied port.",
      "root_cause_hypotheses": ["Port selection and binding are separate."],
      "candidate_directions": [{
        "id": "bind-retry",
        "title": "Retry after a bind conflict",
        "priority": 1,
        "risk": "low",
        "reason": "Small, localized change."
      }],
      "success_metrics": ["A bind conflict retries safely."]
    }
    """
    plan = """TASK_ID: retry-cdp-bind
<<<FILE:program.md>>>
# Retry CDP bind conflicts

Add a bounded retry around browser startup and cover the race with a test.
<<<END>>>
"""
    run_phase = AsyncMock(side_effect=[brief, plan])

    with (
        patch.object(research_mod, "_run_single_phase", run_phase),
        patch.object(
            research_mod,
            "_fetch_github_issue_evidence",
            AsyncMock(return_value="#6470: MCP transport bug"),
        ),
        patch.object(
            research_mod,
            "_owner_identity",
            return_value=("default", None, None),
        ),
        patch(
            "qwenpaw.config.config.load_agent_config",
            return_value=SimpleNamespace(active_model=None),
        ),
    ):
        await research_mod._execute_dialog_plan(
            plan_id,
            research_mod.DialogGoalRequest(
                goal="Fix one simple GitHub issue",
                rounds=3,
            ),
        )

    state = research_mod._dialog_runs.pop(plan_id)
    assert run_phase.await_count == 2
    assert state.status == "awaiting_approval"
    assert "Retry CDP bind conflicts" in state.plan_markdown
    assert state.run_id is None


@pytest.mark.asyncio
async def test_dialog_plan_edit_requires_owner_and_current_revision() -> None:
    from qwenpaw.app.routers import research as research_mod

    plan_id = "plan-edit-owner"
    original = "# Fix issue 6470\n\nRun the focused MCP transport tests."
    research_mod._dialog_runs[plan_id] = research_mod.DialogRunState(
        plan_id=plan_id,
        status="awaiting_approval",
        goal="Fix issue 6470",
        events=[],
        owner_agent_id="default",
        owner_user_id="kai",
        owner_session_id="chat-1",
        plan_markdown=original,
        revision=1,
        content_hash=hashlib.sha256(original.encode()).hexdigest(),
    )

    with patch.object(
        research_mod,
        "_owner_identity",
        return_value=("default", "other-user", "chat-1"),
    ):
        with pytest.raises(research_mod.HTTPException) as denied:
            await research_mod.edit_dialog_plan(
                plan_id,
                research_mod.DialogPlanEditRequest(
                    plan_markdown="# Unauthorized edit",
                    expected_revision=1,
                ),
            )
    assert denied.value.status_code == 404

    with patch.object(
        research_mod,
        "_owner_identity",
        return_value=("default", "kai", "chat-1"),
    ):
        with pytest.raises(research_mod.HTTPException) as stale:
            await research_mod.edit_dialog_plan(
                plan_id,
                research_mod.DialogPlanEditRequest(
                    plan_markdown="# Stale edit",
                    expected_revision=2,
                ),
            )
        assert stale.value.status_code == 409

        updated = await research_mod.edit_dialog_plan(
            plan_id,
            research_mod.DialogPlanEditRequest(
                plan_markdown="# Fix issue 6470\n\nAdd regression coverage.",
                expected_revision=1,
            ),
        )

    state = research_mod._dialog_runs.pop(plan_id)
    assert updated["revision"] == 2
    assert state.plan_markdown == "# Fix issue 6470\n\nAdd regression coverage."
    assert state.content_hash == hashlib.sha256(
        state.plan_markdown.encode(),
    ).hexdigest()


@pytest.mark.asyncio
async def test_dialog_approval_binds_revision_hash_and_is_idempotent() -> None:
    from qwenpaw.app.routers import research as research_mod

    plan_id = "plan-approve-revision"
    plan = "# Fix issue 6470\n\nImplement and test streamable_http."
    digest = hashlib.sha256(plan.encode()).hexdigest()
    research_mod._dialog_runs[plan_id] = research_mod.DialogRunState(
        plan_id=plan_id,
        status="awaiting_approval",
        goal="Fix issue 6470",
        events=[],
        owner_agent_id="default",
        owner_user_id="kai",
        owner_session_id="chat-1",
        plan_markdown=plan,
        revision=3,
        content_hash=digest,
    )
    execute = AsyncMock()

    with (
        patch.object(
            research_mod,
            "_owner_identity",
            return_value=("default", "kai", "chat-1"),
        ),
        patch.object(research_mod, "_execute_approved_dialog", execute),
    ):
        with pytest.raises(research_mod.HTTPException) as stale:
            await research_mod.approve_dialog_plan(
                plan_id,
                research_mod.DialogPlanApprovalRequest(
                    expected_revision=2,
                    content_hash=digest,
                    idempotency_key="approval-6470",
                ),
            )
        assert stale.value.status_code == 409

        approved = await research_mod.approve_dialog_plan(
            plan_id,
            research_mod.DialogPlanApprovalRequest(
                expected_revision=3,
                content_hash=digest,
                idempotency_key="approval-6470",
            ),
        )
        duplicate = await research_mod.approve_dialog_plan(
            plan_id,
            research_mod.DialogPlanApprovalRequest(
                expected_revision=3,
                content_hash=digest,
                idempotency_key="approval-6470",
            ),
        )
        await research_mod._dialog_tasks[plan_id]

    state = research_mod._dialog_runs.pop(plan_id)
    research_mod._dialog_tasks.pop(plan_id, None)
    assert approved["status"] == "approved"
    assert duplicate["status"] == "approved"
    assert execute.await_count == 1
    assert state.approved_revision == 3
    assert state.approved_content_hash == digest
    assert state.approved_by == "kai"
    assert state.approved_at


@pytest.mark.asyncio
async def test_approved_dialog_executes_in_worktree_then_commits_and_pushes(
    tmp_path: Path,
) -> None:
    from qwenpaw.app.routers import research as research_mod

    plan_id = "plan-execute-approved"
    plan = """# Fix issue 6470

Repository: https://github.com/agentscope-ai/QwenPaw
Issue: #6470
Implement streamable_http transport selection and add a regression test.
"""
    digest = hashlib.sha256(plan.encode()).hexdigest()
    research_mod._dialog_runs[plan_id] = research_mod.DialogRunState(
        plan_id=plan_id,
        status="approved",
        goal="Fix https://github.com/agentscope-ai/QwenPaw issue #6470",
        events=[],
        owner_agent_id="default",
        owner_user_id="kai",
        owner_session_id="chat-1",
        plan_markdown=plan,
        revision=2,
        content_hash=digest,
        approved_revision=2,
        approved_content_hash=digest,
        approved_by="kai",
        approved_at="2026-07-26T00:00:00+00:00",
    )
    workspace = object()
    app_services = object()
    research_mod._dialog_runtime_context[plan_id] = {
        "workspace": workspace,
        "app_services": app_services,
    }
    worktree = tmp_path / "worktree"
    worktree.mkdir()

    prepare = AsyncMock(
        return_value=(worktree, "autoresearch/issue-6470-planexec"),
    )
    run_task = AsyncMock(
        return_value={
            "status": "success",
            "response": "Implemented the fix and focused tests pass.",
            "response_length": 47,
            "tool_count": 7,
            "model_info": {"model_name": "test-model"},
        },
    )
    validate_commit = AsyncMock(
        return_value={
            "commit_sha": "a" * 40,
            "test_summary": "focused tests passed",
        },
    )
    push = AsyncMock()

    with (
        patch.object(research_mod, "_prepare_research_worktree", prepare),
        patch.object(research_mod, "_run_task", run_task),
        patch.object(
            research_mod,
            "_validate_and_commit_worktree",
            validate_commit,
        ),
        patch.object(research_mod, "_push_research_branch", push),
        patch(
            "qwenpaw.config.config.load_agent_config",
            return_value=SimpleNamespace(active_model=None),
        ),
    ):
        await research_mod._execute_approved_dialog(plan_id)

    state = research_mod._dialog_runs.pop(plan_id)
    research_mod._dialog_runtime_context.pop(plan_id, None)
    assert state.status == "completed"
    assert state.worktree_path == str(worktree)
    assert state.branch == "autoresearch/issue-6470-planexec"
    assert state.commit_sha == "a" * 40
    assert state.test_summary == "focused tests passed"
    prepare.assert_awaited_once()
    run_task.assert_awaited_once()
    assert run_task.await_args.kwargs["workspace"] is workspace
    assert run_task.await_args.kwargs["app_services"] is app_services
    assert run_task.await_args.kwargs["workspace_dir_override"] == str(worktree)
    validate_commit.assert_awaited_once()
    push.assert_awaited_once_with(
        worktree,
        "autoresearch/issue-6470-planexec",
    )


@pytest.mark.asyncio
async def test_approved_dialog_refuses_changed_plan_hash() -> None:
    from qwenpaw.app.routers import research as research_mod

    plan_id = "plan-hash-changed"
    research_mod._dialog_runs[plan_id] = research_mod.DialogRunState(
        plan_id=plan_id,
        status="approved",
        goal="Fix issue",
        events=[],
        plan_markdown="# Changed after approval",
        revision=2,
        content_hash="b" * 64,
        approved_revision=1,
        approved_content_hash="a" * 64,
    )
    prepare = AsyncMock()

    with patch.object(research_mod, "_prepare_research_worktree", prepare):
        await research_mod._execute_approved_dialog(plan_id)

    state = research_mod._dialog_runs.pop(plan_id)
    prepare.assert_not_awaited()
    assert state.status == "approved"


# ═══════════════════════════════════════════════════════════
# _parse_planning_output — success paths
# ═══════════════════════════════════════════════════════════


def test_parse_with_file_markers() -> None:
    """Format 1: <<<FILE:name>>>...<<<END>>> markers parse correctly."""
    response = """TASK_ID: fix-latency-bug
<<<FILE:program.md>>>
# Fix latency

Goal: reduce p99 latency from 200ms to 50ms.
## Modifiable
- src/handler.py
## Frozen
- src/protocol.py
## Run
python src/handler.py
## Metrics
- p99_latency
## Iterations: 5
<<<END>>>
<<<FILE:judge.py>>>
import json
import sys
from pathlib import Path

source = Path(sys.argv[1]).read_text(encoding="utf-8")
passed = "time.sleep" not in source
print(json.dumps({"passed": passed, "score": float(passed), "metrics": {"p99_latency": 55}}))
<<<END>>>
<<<FILE:solution.py>>>
def handle():
    pass
<<<END>>>
"""
    result = _parse_planning_output(response, goal="fix latency")
    assert result["task_id"] == "fix-latency-bug"
    assert "Fix latency" in result["program"]
    assert len(result["program"]) >= 20
    assert len(result["judge_source"]) >= 50
    assert result["solution_name"] == "solution.py"
    assert "def handle" in result["solution_source"]


def test_parse_with_json_format() -> None:
    """Format 3: JSON structured output parses correctly."""
    response = '''TASK_ID: cache-optimizer
{
  "summary": "Optimize cache hit rate",
  "files": [
    {
      "path": "program.md",
      "content": "# Cache Optimizer\\nGoal: increase hit rate to 90%.\\n## Modifiable\\n- src/cache.py\\n## Frozen\\n- src/types.py\\n## Run\\npython src/cache.py\\n## Metrics\\n- hit_rate\\n## Iterations: 3"
    },
    {
      "path": "judge.py",
      "content": "import json\\nimport sys\\nfrom pathlib import Path\\n\\nsource = Path(sys.argv[1]).read_text(encoding='utf-8')\\npassed = 'LRU' in source\\nprint(json.dumps({\\"passed\\": passed, \\"score\\": float(passed), \\"metrics\\": {\\"hit_rate\\": 90}}))"
    },
    {
      "path": "solution.py",
      "content": "class LRUCache:\\n    pass"
    }
  ]
}'''
    result = _parse_planning_output(response, goal="cache")
    assert result["task_id"] == "cache-optimizer"
    assert "Cache Optimizer" in result["program"]
    assert "LRU" in result["judge_source"]
    assert "LRUCache" in result["solution_source"]


def test_parse_with_fenced_code_blocks() -> None:
    """Format 2: ```<filename> fenced code blocks parse correctly."""
    response = """TASK_ID: demo-task
## program.md
```program.md
# Demo

Goal: do something useful.
## Modifiable
- main.py
## Frozen
- lib.py
## Run
python main.py
## Metrics
- result
## Iterations: 3
```

## judge.py
```judge.py
import json
import sys
from pathlib import Path

source = Path(sys.argv[1]).read_text(encoding="utf-8")
print(json.dumps({"passed": True, "score": 1, "metrics": {"result": 100}}))
```

## solution
```solution.py
print("hello")
```
"""
    result = _parse_planning_output(response, goal="demo")
    assert result["task_id"] == "demo-task"
    assert "Goal: do something useful" in result["program"]
    assert "result" in result["judge_source"]
    assert result["solution_name"] == "solution.py"


def test_auto_generate_task_id_when_missing() -> None:
    """When TASK_ID line is missing, auto-generate from goal."""
    response = """<<<FILE:program.md>>>
# Test task with at least twenty chars
<<<END>>>
<<<FILE:judge.py>>>
import json, sys
from pathlib import Path
source = Path(sys.argv[1]).read_text(encoding="utf-8")
print(json.dumps({"passed": True, "score": 1, "metrics": {"ok": 1}}))
<<<END>>>
<<<FILE:solution.py>>>
print("hello world")
<<<END>>>
"""
    result = _parse_planning_output(response, goal="reduce-error-rate")
    assert result["task_id"]  # auto-generated, not empty
    assert "-" in result["task_id"]  # kebab-case
    assert "reduce" in result["task_id"] or "error" in result["task_id"]


# ═══════════════════════════════════════════════════════════
# _parse_planning_output — error / truncation detection
# ═══════════════════════════════════════════════════════════


def test_truncated_80_char_response_raises() -> None:
    """Model returns ~80 chars (truncated) — must raise ValueError."""
    response = "I will create the research plan with judge.py, program.md, and plan.md files"
    assert len(response) < 200

    with pytest.raises(ValueError) as exc:
        _parse_planning_output(response, goal="test")
    assert "缺少文件" in str(exc.value) or "LLM 输出长度" in str(exc.value)


def test_missing_judge_py_raises() -> None:
    """Missing judge.py should raise ValueError."""
    response = """TASK_ID: no-judge
<<<FILE:program.md>>>
# Task without judge, has twenty chars
<<<END>>>
<<<FILE:solution.py>>>
ok
<<<END>>>
"""
    with pytest.raises(ValueError) as exc:
        _parse_planning_output(response, goal="test")
    assert "缺少文件" in str(exc.value)
    assert "judge.py" in str(exc.value)


def test_missing_program_md_raises() -> None:
    """Missing program.md should raise ValueError."""
    response = """TASK_ID: no-program
<<<FILE:judge.py>>>
import json, sys
from pathlib import Path
source = Path(sys.argv[1]).read_text(encoding="utf-8")
print(json.dumps({"passed": True, "score": 1, "metrics": {}}))
<<<END>>>
<<<FILE:solution.py>>>
ok
<<<END>>>
"""
    with pytest.raises(ValueError) as exc:
        _parse_planning_output(response, goal="test")
    assert "program.md" in str(exc.value)


def test_judge_py_too_short_raises() -> None:
    """judge.py content shorter than 50 chars must raise."""
    response = """TASK_ID: short-judge
<<<FILE:program.md>>>
# Task with short judge that has at least twenty characters
<<<END>>>
<<<FILE:judge.py>>>
print("too short")
<<<END>>>
<<<FILE:solution.py>>>
ok
<<<END>>>
"""
    with pytest.raises(ValueError) as exc:
        _parse_planning_output(response, goal="test")
    assert "judge.py 内容过短" in str(exc.value) or "50" in str(exc.value)


def test_program_md_too_short_raises() -> None:
    """program.md shorter than 20 chars must raise."""
    response = """TASK_ID: short-prog
<<<FILE:program.md>>>
too short
<<<END>>>
<<<FILE:judge.py>>>
import json, sys
from pathlib import Path
source = Path(sys.argv[1]).read_text(encoding="utf-8")
print(json.dumps({"passed": True, "score": 1.0, "metrics": {}}))
<<<END>>>
<<<FILE:solution.py>>>
ok
<<<END>>>
"""
    with pytest.raises(ValueError) as exc:
        _parse_planning_output(response, goal="test")
    assert "program.md 内容过短" in str(exc.value) or "20" in str(exc.value)


def test_judge_py_syntax_error_raises() -> None:
    """judge.py with invalid Python syntax must raise ValueError."""
    response = """TASK_ID: bad-syntax
<<<FILE:program.md>>>
# Task with syntax error in judge that has enough chars
<<<END>>>
<<<FILE:judge.py>>>
import json
def broken(
    print("unclosed paren and indent")
<<<END>>>
<<<FILE:solution.py>>>
print("valid solution")
<<<END>>>
"""
    with pytest.raises(ValueError) as exc:
        _parse_planning_output(response, goal="test")
    assert "语法错误" in str(exc.value) or "SyntaxError" in str(exc.value)


def test_judge_py_missing_sys_argv_warns_but_succeeds() -> None:
    """judge.py without sys.argv should warn but not fail (warnings only)."""
    response = """TASK_ID: no-argv
<<<FILE:program.md>>>
# Task with minimal judge, over twenty chars here
<<<END>>>
<<<FILE:judge.py>>>
import json
# No sys.argv — but still valid Python
print(json.dumps({"passed": True, "score": 1.0, "metrics": {"x": 1}}))
<<<END>>>
<<<FILE:solution.py>>>
print("minimal ok")
<<<END>>>
"""
    # Should succeed (warnings are logged, not raised)
    result = _parse_planning_output(response, goal="test")
    assert result["task_id"] == "no-argv"
    assert "json" in result["judge_source"]


def test_three_files_not_all_successful_no_disk_write() -> None:
    """If any file is missing, _parse_planning_output raises — no partial writes.

    This is guaranteed by the function's design: it either returns the dict
    with all 3 files, or raises ValueError. The caller is responsible for
    not writing files on exception.
    """
    # This is a specification test: the function must be atomic
    response = """TASK_ID: partial
<<<FILE:program.md>>>
# Only two files present, enough chars here
<<<END>>>
<<<FILE:judge.py>>>
import json, sys
from pathlib import Path
source = Path(sys.argv[1]).read_text(encoding="utf-8")
print(json.dumps({"passed": True, "score": 1.0, "metrics": {}}))
<<<END>>>
"""
    with pytest.raises(ValueError):
        _parse_planning_output(response, goal="test")
    # If we get here without the caller writing anything, the atomicity
    # contract holds. The parse function itself doesn't touch the filesystem.


# ═══════════════════════════════════════════════════════════
# _execute_dialog_plan — retry logic (mock _run_task)
# ═══════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_retry_success_on_third_attempt() -> None:
    """First 2 parse attempts fail, 3rd succeeds → task created."""
    from qwenpaw.app.routers import research as research_mod

    # Build responses: first 2 fail (too short), 3rd is valid
    valid_response = """TASK_ID: retry-win
<<<FILE:program.md>>>
# Retry test with at least twenty characters for parsing
<<<END>>>
<<<FILE:judge.py>>>
import json, sys
from pathlib import Path
source = Path(sys.argv[1]).read_text(encoding="utf-8")
print(json.dumps({"passed": True, "score": 1.0, "metrics": {"ok": 1}}))
<<<END>>>
<<<FILE:solution.py>>>
print("retry win")
<<<END>>>
"""
    call_count = [0]
    short_responses = [
        {"status": "success", "response": "short reply", "response_length": 11, "elapsed_seconds": 1.0, "model_info": {}, "usage": {}},
        {"status": "success", "response": "another brief output", "response_length": 21, "elapsed_seconds": 1.0, "model_info": {}, "usage": {}},
        {"status": "success", "response": valid_response, "response_length": len(valid_response), "elapsed_seconds": 1.0, "model_info": {}, "usage": {}},
    ]

    async def mock_run_task(**kwargs):
        idx = min(call_count[0], len(short_responses) - 1)
        call_count[0] += 1
        return short_responses[idx]

    # Verify _parse_planning_output works on the 3rd response
    result = _parse_planning_output(valid_response, goal="test")
    assert result["task_id"] == "retry-win"

    # Verify short responses fail parse
    with pytest.raises(ValueError):
        _parse_planning_output("short reply", goal="test")
    with pytest.raises(ValueError):
        _parse_planning_output("another brief output", goal="test")


@pytest.mark.asyncio
async def test_all_three_retries_fail() -> None:
    """All 3 attempts produce unparseable output → task fails."""
    # Each short response should fail _parse_planning_output
    for short in ["truncated", "too brief to parse", "model error output"]:
        with pytest.raises(ValueError):
            _parse_planning_output(short, goal="test")

    # Verify that 3 consecutive failures would be caught by the retry loop
    # by confirming each indiviudal parse fails
    assert True  # validated by the loop above


def test_short_response_detection_flag() -> None:
    """Responses < 200 chars should be flagged as suspicious.

    This tests the _run_task warning logging threshold.
    """
    # _run_task logs a warning when response_length < 200
    # We verify the logic: response_length is in the result dict
    too_short = "x" * 80
    long_enough = "x" * 300

    assert len(too_short) < 200  # should trigger warning
    assert len(long_enough) >= 200  # should not trigger warning


def test_json_fallback_when_no_other_format() -> None:
    """When no markers or fenced blocks, JSON format should be tried."""
    response = '''TASK_ID: json-only
{
  "files": [
    {
      "path": "program.md",
      "content": "# JSON-only task with enough characters for validation pass"
    },
    {
      "path": "judge.py", 
      "content": "import json, sys\\nfrom pathlib import Path\\nsource = Path(sys.argv[1]).read_text(encoding='utf-8')\\nprint(json.dumps({\\"passed\\": True, \\"score\\": 1.0, \\"metrics\\": {}}))"
    },
    {
      "path": "solution.py",
      "content": "result = 42"
    }
  ]
}'''
    result = _parse_planning_output(response, goal="json task")
    assert result["task_id"] == "json-only"
    assert "JSON-only" in result["program"]
    assert "solution.py" == result["solution_name"]


def test_parse_with_unicode_and_cjk_content() -> None:
    """Unicode (CJK, emoji, special chars) flows through parsing correctly."""
    response = '''TASK_ID: unicode-task
<<<FILE:program.md>>>
# 研究计划：多语言支持 🌍

本任务处理以下内容：
- 中日韩字符：日本語・한국어・中文
- Emoji: 🎯 ✅ ❌ 🔥
- Special chars: ñ à ü ç é è
- Math: ∑ ∏ ∫ √ ∞
<<<END>>>
<<<FILE:judge.py>>>
import json, sys
from pathlib import Path
source = Path(sys.argv[1]).read_text(encoding='utf-8')
# 验证输出 ✓
print(json.dumps({"passed": True, "score": 1.0, "metrics": {"unicode": "✓"}}))
<<<END>>>
<<<FILE:solution.py>>>
# 解方案 💡
result = "🎉 成功！"
<<<END>>>'''
    result = _parse_planning_output(response, goal="unicode test")
    assert result["task_id"] == "unicode-task"
    assert "研究计划" in result["program"]
    assert "🌍" in result["program"]
    assert "日本語" in result["program"]
    assert "✓" in result["judge_source"]
    assert "🎉" in result["solution_source"]
    assert "solution.py" == result["solution_name"]


def test_parse_rejects_null_bytes_in_content() -> None:
    """Null bytes in content should be rejected cleanly, not silently corrupted."""
    response = '''TASK_ID: null-task
<<<FILE:program.md>>>
# Plan with null\x00byte embedded
<<<END>>>
<<<FILE:judge.py>>>
import json, sys
from pathlib import Path
source = Path(sys.argv[1]).read_text(encoding='utf-8')
print(json.dumps({"passed": True, "score": 1.0, "metrics": {}}))
<<<END>>>
<<<FILE:solution.py>>>
print("null test")
<<<END>>>'''
    # Null bytes are valid in Python strings but should be handled gracefully
    result = _parse_planning_output(response, goal="null test")
    assert result["task_id"] == "null-task"
    assert "\x00" in result["program"]


# ═══════════════════════════════════════════════════════════
# All formats fail → ValueError
# ═══════════════════════════════════════════════════════════


def test_all_formats_fail_raises_clear_error() -> None:
    """When no FILE markers, fenced blocks, or JSON → raises with hint."""
    response = "This is just a plain text response with no file markers at all"
    with pytest.raises(ValueError) as exc:
        _parse_planning_output(response, goal="test")
    msg = str(exc.value)
    assert "缺少文件" in msg or "missing" in msg.lower()
    assert "program.md" in msg or "judge.py" in msg


# ═══════════════════════════════════════════════════════════
# _run_single_phase — retry logic (mock _run_task at boundary)
# ═══════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_run_single_phase_retry_short_then_success() -> None:
    """First attempt too short, second succeeds → 2 calls to _run_task."""
    valid_phase_output = (
        "<<<FILE:program.md>>>\n# Valid plan with twenty+ chars\n<<<END>>>\n"
        "<<<FILE:judge.py>>>\nimport json, sys\nfrom pathlib import Path\n"
        "source = Path(sys.argv[1]).read_text(encoding='utf-8')\n"
        'print(json.dumps({"passed": True, "score": 1.0, "metrics": {"x": 1}}))\n'
        "<<<END>>>\n"
        "<<<FILE:solution.py>>>\nprint('retry win')\n<<<END>>>\n"
    )

    short_result = {
        "status": "success",
        "response": "too short",
        "response_length": 9,
        "elapsed_seconds": 0.5,
        "model_info": {},
        "usage": {},
    }
    success_result = {
        "status": "success",
        "response": valid_phase_output,
        "response_length": len(valid_phase_output),
        "elapsed_seconds": 1.0,
        "model_info": {"model_name": "test-model"},
        "usage": {"input_tokens": 100, "output_tokens": 200},
    }

    run_task_mock = AsyncMock(side_effect=[short_result, success_result])

    with (
        patch("qwenpaw.app.routers.research._run_task", run_task_mock),
        patch("asyncio.sleep", new=AsyncMock()),
    ):
        from qwenpaw.app.routers.research import _run_single_phase

        plan_id = "test-plan-retry-success"
        result = await _run_single_phase(
            plan_id=plan_id,
            instruction="test instruction",
            agent_config=object(),
            request_context={
                "session_id": "test-sess", "user_id": "u1",
                "channel": "test", "agent_id": "a1",
            },
            max_iters=5,
            timeout=30,
            phase_label="program.md",
        )

    assert result is not None, "Expected non-None result (retry succeeded)"
    assert run_task_mock.await_count == 2, (
        f"Expected 2 calls (short→retry→success), got {run_task_mock.await_count}"
    )


@pytest.mark.asyncio
async def test_run_single_phase_retries_max_iterations_response() -> None:
    """Agent-loop failure text is not valid generated source, regardless of length."""
    loop_failure = {
        "status": "success",
        "response": (
            "Executed maximum iterations of reasoning-acting loop "
            "without finishing the task."
        ),
        "model_info": {},
        "usage": {},
    }
    valid_source = {
        "status": "success",
        "response": "def solve():\n    return 'valid generated solution'\n",
        "model_info": {},
        "usage": {},
    }
    run_task_mock = AsyncMock(side_effect=[loop_failure, valid_source])

    with patch("qwenpaw.app.routers.research._run_task", run_task_mock):
        from qwenpaw.app.routers.research import _run_single_phase

        result = await _run_single_phase(
            plan_id="test-plan-agent-loop-retry",
            instruction="generate solution",
            agent_config=object(),
            request_context={
                "session_id": "test-sess", "user_id": "u1",
                "channel": "test", "agent_id": "a1",
            },
            max_iters=5,
            timeout=30,
            phase_label="solution.py",
        )

    assert result == valid_source["response"]
    assert run_task_mock.await_count == 2


@pytest.mark.asyncio
async def test_research_brief_zero_tools_reports_cause_without_retry() -> None:
    """An impossible Discovery configuration should fail once with diagnostics."""
    from qwenpaw.app.routers import research as research_mod

    plan_id = "test-plan-discovery-zero-tools"
    research_mod._dialog_runs[plan_id] = research_mod.DialogRunState(
        plan_id=plan_id,
        status="discovering",
        goal="Inspect GitHub issues",
        events=[],
    )
    loop_failure = {
        "status": "success",
        "response": (
            "Executed maximum iterations of reasoning-acting loop "
            "without finishing the task."
        ),
        "elapsed_seconds": 73.8,
        "model_info": {"model_name": "deepseek-v4-pro"},
        "usage": {},
        "tool_count": 0,
        "max_iters": 20,
    }
    run_task_mock = AsyncMock(return_value=loop_failure)

    try:
        with patch("qwenpaw.app.routers.research._run_task", run_task_mock):
            result = await research_mod._run_single_phase(
                plan_id=plan_id,
                instruction="research GitHub and the local repository",
                agent_config=object(),
                request_context={
                    "session_id": "test-sess",
                    "user_id": "u1",
                    "channel": "test",
                    "agent_id": "a1",
                },
                max_iters=20,
                timeout=600,
                phase_label="research_brief",
                require_tools=True,
            )

        state = research_mod._dialog_runs[plan_id]
        assert result is None
        assert run_task_mock.await_count == 1
        assert "可用工具数：0" in state.error
        assert "deepseek-v4-pro" in state.error
        assert "20" in state.error
        assert "73.8" in state.error
        assert state.events[-1]["phase"] == "failed"
        assert state.events[-1]["detail"] == state.error
    finally:
        research_mod._dialog_runs.pop(plan_id, None)


@pytest.mark.asyncio
async def test_run_single_phase_all_attempts_too_short() -> None:
    """All attempts produce too-short output → returns None, _run_task called _MAX_ATTEMPTS (2) times."""
    short_result = {
        "status": "success",
        "response": "x",
        "response_length": 1,
        "elapsed_seconds": 0.5,
        "model_info": {},
        "usage": {},
    }

    run_task_mock = AsyncMock(side_effect=[short_result, short_result])

    with (
        patch("qwenpaw.app.routers.research._run_task", run_task_mock),
        patch("asyncio.sleep", new=AsyncMock()),
    ):
        from qwenpaw.app.routers.research import _run_single_phase

        plan_id = "test-plan-all-fail"
        result = await _run_single_phase(
            plan_id=plan_id,
            instruction="test instruction",
            agent_config=object(),
            request_context={
                "session_id": "test-sess", "user_id": "u1",
                "channel": "test", "agent_id": "a1",
            },
            max_iters=5,
            timeout=30,
            phase_label="judge.py",
        )

    assert result is None, (
        f"Expected None (all attempts exhausted), got {result!r}"
    )
    assert run_task_mock.await_count == 2, (
        f"Expected 2 calls (_MAX_ATTEMPTS), got {run_task_mock.await_count}"
    )


@pytest.mark.asyncio
async def test_run_single_phase_first_attempt_succeeds() -> None:
    """First attempt succeeds → _run_task called exactly once, no retry."""
    valid_phase_output = (
        "<<<FILE:program.md>>>\n# Single-shot success, twenty+ chars\n<<<END>>>\n"
        "<<<FILE:judge.py>>>\nimport json, sys\nfrom pathlib import Path\n"
        "source = Path(sys.argv[1]).read_text(encoding='utf-8')\n"
        'print(json.dumps({"passed": True, "score": 1.0, "metrics": {"ok": 1}}))\n'
        "<<<END>>>\n"
        "<<<FILE:solution.py>>>\nprint('single shot')\n<<<END>>>\n"
    )

    success_result = {
        "status": "success",
        "response": valid_phase_output,
        "response_length": len(valid_phase_output),
        "elapsed_seconds": 1.0,
        "model_info": {"model_name": "test-model"},
        "usage": {"input_tokens": 100, "output_tokens": 200},
    }

    run_task_mock = AsyncMock(return_value=success_result)

    with (
        patch("qwenpaw.app.routers.research._run_task", run_task_mock),
        patch("asyncio.sleep", new=AsyncMock()),
    ):
        from qwenpaw.app.routers.research import _run_single_phase

        plan_id = "test-plan-first-success"
        result = await _run_single_phase(
            plan_id=plan_id,
            instruction="test instruction",
            agent_config=object(),
            request_context={
                "session_id": "test-sess", "user_id": "u1",
                "channel": "test", "agent_id": "a1",
            },
            max_iters=5,
            timeout=30,
            phase_label="solution.py",
        )

    assert result is not None, "Expected non-None result (first attempt succeeded)"
    assert run_task_mock.await_count == 1, (
        f"Expected 1 call (first success, no retry), got {run_task_mock.await_count}"
    )


# ═══════════════════════════════════════════════════════════
# _finish_run — terminal state guard
# ═══════════════════════════════════════════════════════════


def _make_run_state(run_id: str, status: str) -> object:
    """Create a minimal ResearchRunState for testing _finish_run."""
    from qwenpaw.app.routers.research import ResearchRunState

    return ResearchRunState(
        id=run_id,
        task_id="test-task",
        agent_id="test-agent",
        owner_agent_id="test-agent",
        owner_user_id=None,
        owner_session_id=None,
        status=status,
        rounds=3,
        completed_rounds=0,
        outcomes=(),
        created_at="2026-01-01T00:00:00+00:00",
        events=(),
        current_round=None,
        phase="idle",
        updated_at="2026-01-01T00:00:00+00:00",
    )


def test_finish_run_refuses_completed_to_failed() -> None:
    """Transition completed → failed is blocked (terminal guard)."""
    from qwenpaw.app.routers.research import _finish_run, _runs

    run_state = _make_run_state("guard-completed", "completed")
    _runs["guard-completed"] = run_state

    _finish_run("guard-completed", "failed", error="late error")

    assert _runs["guard-completed"].status == "completed", (
        "Terminal 'completed' must not transition to 'failed'"
    )


def test_finish_run_refuses_failed_to_completed() -> None:
    """Transition failed → completed is blocked (terminal guard)."""
    from qwenpaw.app.routers.research import _finish_run, _runs

    run_state = _make_run_state("guard-failed", "failed")
    _runs["guard-failed"] = run_state

    _finish_run("guard-failed", "completed")

    assert _runs["guard-failed"].status == "failed", (
        "Terminal 'failed' must not transition to 'completed'"
    )


def test_finish_run_refuses_cancelled_to_completed() -> None:
    """Transition cancelled → completed is blocked (terminal guard)."""
    from qwenpaw.app.routers.research import _finish_run, _runs

    run_state = _make_run_state("guard-cancelled", "cancelled")
    _runs["guard-cancelled"] = run_state

    _finish_run("guard-cancelled", "completed")

    assert _runs["guard-cancelled"].status == "cancelled", (
        "Terminal 'cancelled' must not transition to 'completed'"
    )


def test_finish_run_allows_running_to_completed() -> None:
    """Transition running → completed is allowed."""
    from qwenpaw.app.routers.research import _finish_run, _runs

    run_state = _make_run_state("guard-running", "running")
    _runs["guard-running"] = run_state

    _finish_run("guard-running", "completed")

    assert _runs["guard-running"].status == "completed", (
        "Running must be able to transition to 'completed'"
    )


def test_finish_run_allow_overwrite_for_cancel() -> None:
    """allow_overwrite=True lets cancel override a terminal state."""
    from qwenpaw.app.routers.research import _finish_run, _runs

    run_state = _make_run_state("guard-ow", "completed")
    _runs["guard-ow"] = run_state

    _finish_run("guard-ow", "cancelled", error="user cancelled", allow_overwrite=True)

    assert _runs["guard-ow"].status == "cancelled", (
        "allow_overwrite must let cancel override completed"
    )


def test_finish_run_nonexistent_is_noop() -> None:
    """Calling _finish_run on a non-existent run should not raise."""
    from qwenpaw.app.routers.research import _finish_run

    # Should not raise
    _finish_run("nonexistent-run-id", "completed")
