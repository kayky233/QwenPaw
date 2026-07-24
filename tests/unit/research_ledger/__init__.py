"""Unit tests for research_ledger schema — 7 table definitions, constraints, types."""

import pytest

from qwenpaw.research_ledger.schema import (
    Base,
    ResearchArtifact,
    ResearchDialogRun,
    ResearchEvent,
    ResearchOutcome,
    ResearchRun,
    ResearchSubmission,
    ResearchTask,
)


class TestSchemaTableCount:
    """Verify exactly 7 tables are registered."""

    def test_all_tables_registered(self):
        tables = Base.metadata.tables
        assert len(tables) == 7
        assert sorted(tables.keys()) == [
            "research_artifacts",
            "research_dialog_runs",
            "research_events",
            "research_outcomes",
            "research_runs",
            "research_submissions",
            "research_tasks",
        ]


class TestResearchTask:
    """Table 1: research_tasks."""

    def test_columns(self):
        cols = {c.name: c for c in ResearchTask.__table__.columns}
        assert "task_id" in cols
        assert "program" in cols
        assert "solution_name" in cols
        assert "solution_source" in cols
        assert "judge_source" in cols
        assert "created_at" in cols
        # task_id is primary key
        assert cols["task_id"].primary_key
        # program is non-null Text
        assert not cols["program"].nullable


class TestResearchRun:
    """Table 2: research_runs."""

    def test_columns(self):
        cols = {c.name: c for c in ResearchRun.__table__.columns}
        assert "run_id" in cols
        assert "task_id" in cols
        assert "agent_id" in cols
        assert "status" in cols
        assert "phase" in cols
        assert "rounds" in cols
        assert "completed_rounds" in cols
        assert "current_round" in cols
        assert "error" in cols
        assert "started_at" in cols
        assert "finished_at" in cols
        assert cols["run_id"].primary_key
        assert cols["current_round"].nullable  # can be null

    def test_task_foreign_key(self):
        fks = list(ResearchRun.__table__.foreign_keys)
        assert len(fks) == 1
        fk = fks[0]
        assert fk.column.table.name == "research_tasks"
        assert fk.column.name == "task_id"

    def test_unique_run_id_constraint(self):
        constraints = {c.name: c for c in ResearchRun.__table__.constraints if c.name}
        assert "uq_research_runs_run_id" in constraints


class TestResearchOutcome:
    """Table 3: research_outcomes."""

    def test_columns(self):
        cols = {c.name: c for c in ResearchOutcome.__table__.columns}
        assert "id" in cols
        assert "run_id" in cols
        assert "round" in cols
        assert "status" in cols
        assert "passed" in cols
        assert "baseline_score" in cols
        assert "candidate_score" in cols
        assert "improvement" in cols
        assert "metrics" in cols
        assert "error" in cols
        assert "parent_source" in cols
        assert "candidate_source" in cols
        assert cols["id"].primary_key
        assert cols["candidate_score"].nullable

    def test_unique_run_round(self):
        constraints = {c.name: c for c in ResearchOutcome.__table__.constraints if c.name}
        assert "uq_outcome_run_round" in constraints


class TestResearchEvent:
    """Table 4: research_events."""

    def test_columns(self):
        cols = {c.name: c for c in ResearchEvent.__table__.columns}
        assert "id" in cols
        assert "run_id" in cols
        assert "phase" in cols
        assert "timestamp" in cols
        assert "round" in cols
        assert "detail" in cols
        assert cols["round"].nullable


class TestResearchDialogRun:
    """Table 5: research_dialog_runs."""

    def test_columns(self):
        cols = {c.name: c for c in ResearchDialogRun.__table__.columns}
        assert "plan_id" in cols
        assert "task_id" in cols
        assert "run_id" in cols
        assert "status" in cols
        assert "goal" in cols
        assert "rounds" in cols
        assert "error" in cols
        assert cols["plan_id"].primary_key
        assert cols["task_id"].nullable  # SET NULL on FK delete
        assert cols["run_id"].nullable


class TestResearchSubmission:
    """Table 6: research_submissions."""

    def test_columns(self):
        cols = {c.name: c for c in ResearchSubmission.__table__.columns}
        assert "id" in cols
        assert "outcome_id" in cols
        assert "url" in cols
        assert "language" in cols
        assert "status" in cols
        assert "verdict" in cols
        assert "error" in cols


class TestResearchArtifact:
    """Table 7: research_artifacts."""

    def test_columns(self):
        cols = {c.name: c for c in ResearchArtifact.__table__.columns}
        assert "id" in cols
        assert "run_id" in cols
        assert "outcome_id" in cols
        assert "local_path" in cols
        assert "content_hash" in cols
        assert "size_bytes" in cols
        assert "content_type" in cols
        assert cols["outcome_id"].nullable

    def test_unique_run_path(self):
        constraints = {c.name: c for c in ResearchArtifact.__table__.constraints if c.name}
        assert "uq_artifact_run_path" in constraints
