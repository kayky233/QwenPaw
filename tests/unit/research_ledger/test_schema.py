"""Unit tests for research_ledger schema — 7 tables, constraints, types."""

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
    def test_primary_key_and_columns(self):
        cols = {c.name: c for c in ResearchTask.__table__.columns}
        assert cols["task_id"].primary_key
        assert not cols["program"].nullable
        assert "created_at" in cols


class TestResearchRun:
    def test_primary_key(self):
        assert ResearchRun.__table__.columns["run_id"].primary_key

    def test_foreign_key_to_task(self):
        fks = list(ResearchRun.__table__.foreign_keys)
        assert len(fks) == 1
        assert fks[0].column.table.name == "research_tasks"

    def test_current_round_nullable(self):
        assert ResearchRun.__table__.columns["current_round"].nullable

    def test_unique_run_id_constraint(self):
        cs = {c.name: c for c in ResearchRun.__table__.constraints if c.name}
        assert "uq_research_runs_run_id" in cs


class TestResearchOutcome:
    def test_primary_key_is_id(self):
        assert ResearchOutcome.__table__.columns["id"].primary_key

    def test_candidate_score_nullable(self):
        assert ResearchOutcome.__table__.columns["candidate_score"].nullable

    def test_unique_run_round_constraint(self):
        cs = {c.name: c for c in ResearchOutcome.__table__.constraints if c.name}
        assert "uq_outcome_run_round" in cs


class TestResearchEvent:
    def test_round_nullable(self):
        assert ResearchEvent.__table__.columns["round"].nullable


class TestResearchDialogRun:
    def test_plan_id_pk(self):
        assert ResearchDialogRun.__table__.columns["plan_id"].primary_key

    def test_task_id_nullable(self):
        assert ResearchDialogRun.__table__.columns["task_id"].nullable

    def test_run_id_nullable(self):
        assert ResearchDialogRun.__table__.columns["run_id"].nullable


class TestResearchSubmission:
    def test_id_pk(self):
        assert ResearchSubmission.__table__.columns["id"].primary_key


class TestResearchArtifact:
    def test_outcome_id_nullable(self):
        assert ResearchArtifact.__table__.columns["outcome_id"].nullable

    def test_unique_run_path_constraint(self):
        cs = {c.name: c for c in ResearchArtifact.__table__.constraints if c.name}
        assert "uq_artifact_run_path" in cs
