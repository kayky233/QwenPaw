"""Unit tests for FileLocalArtifactStore."""

import hashlib
import tempfile
from pathlib import Path

import pytest

from qwenpaw.research_ledger.artifacts import (
    ArtifactMetadata,
    FileLocalArtifactStore,
    LocalArtifactStore,
)


class TestArtifactMetadata:
    def test_frozen_dataclass(self):
        meta = ArtifactMetadata(
            run_id="r1",
            local_path="/tmp/r1/test.txt",
            content_hash="abc123",
            size_bytes=11,
            content_type="text/plain",
            created_at="2026-01-01T00:00:00Z",
        )
        assert meta.run_id == "r1"
        assert meta.content_type == "text/plain"
        assert meta.outcome_id is None

        meta_with_outcome = ArtifactMetadata(
            run_id="r1",
            local_path="/tmp/r1/test.txt",
            content_hash="abc123",
            size_bytes=11,
            content_type="text/plain",
            created_at="2026-01-01T00:00:00Z",
            outcome_id="out1",
        )
        assert meta_with_outcome.outcome_id == "out1"


class TestLocalArtifactStoreProtocol:
    def test_is_runtime_checkable(self):
        store = FileLocalArtifactStore(Path(tempfile.mkdtemp()))
        assert isinstance(store, LocalArtifactStore)

    def test_file_store_implements_protocol(self):
        store = FileLocalArtifactStore(Path(tempfile.mkdtemp()))
        assert isinstance(store, LocalArtifactStore)


class TestFileLocalArtifactStore:
    @pytest.fixture
    def store(self):
        root = Path(tempfile.mkdtemp())
        return FileLocalArtifactStore(root)

    @pytest.mark.asyncio
    async def test_store_and_retrieve_text(self, store):
        meta = await store.store(
            "run-1",
            "hello world",
            "test.txt",
            "text/plain",
        )
        assert meta.run_id == "run-1"
        assert meta.size_bytes == 11
        assert meta.content_type == "text/plain"
        expected_hash = hashlib.sha256(b"hello world").hexdigest()
        assert meta.content_hash == expected_hash

        content = await store.retrieve_text("run-1", "test.txt")
        assert content == "hello world"

    @pytest.mark.asyncio
    async def test_store_and_retrieve_bytes(self, store):
        data = b"\x00\x01\x02\x03"
        await store.store(
            "run-2",
            data,
            "binary.bin",
            "application/octet-stream",
        )
        result = await store.retrieve("run-2", "binary.bin")
        assert result == data

    @pytest.mark.asyncio
    async def test_retrieve_missing_raises(self, store):
        with pytest.raises(FileNotFoundError):
            await store.retrieve("run-1", "nonexistent.txt")

    @pytest.mark.asyncio
    async def test_delete_run_artifacts(self, store):
        await store.store("run-1", "a", "a.txt")
        await store.store("run-1", "b", "b.txt")
        await store.store("run-2", "c", "c.txt")

        count = await store.delete_run_artifacts("run-1")
        assert count == 2
        assert len(await store.run_artifact_paths("run-2")) == 1
        with pytest.raises(FileNotFoundError):
            await store.retrieve("run-1", "a.txt")

    @pytest.mark.asyncio
    async def test_delete_nonexistent_run(self, store):
        count = await store.delete_run_artifacts("nonexistent")
        assert count == 0

    @pytest.mark.asyncio
    async def test_run_artifact_paths(self, store):
        await store.store("run-1", "x", "x.txt")
        await store.store("run-1", "y", "sub/y.txt")

        paths = await store.run_artifact_paths("run-1")
        assert len(paths) == 2
        for path in paths:
            assert path.startswith("run-1/")

    @pytest.mark.asyncio
    async def test_run_artifact_paths_empty(self, store):
        paths = await store.run_artifact_paths("empty-run")
        assert paths == []

    @pytest.mark.asyncio
    async def test_atomic_write_no_tmp_leftover(self, store):
        await store.store("run-1", "content", "file.txt")
        run_dir = Path(store._root) / "run-1"
        files = list(run_dir.iterdir())
        assert len(files) == 1
        assert files[0].name == "file.txt"

    @pytest.mark.asyncio
    async def test_outcome_id_in_metadata(self, store):
        meta = await store.store(
            "run-1",
            "data",
            "data.txt",
            outcome_id="outcome-42",
        )
        assert meta.outcome_id == "outcome-42"
