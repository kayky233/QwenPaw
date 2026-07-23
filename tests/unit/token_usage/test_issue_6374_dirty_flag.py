# -*- coding: utf-8 -*-
"""Regression tests for Issue #6374: token usage dirty-flag persistence bug."""
from __future__ import annotations

import asyncio
import json
from pathlib import Path
from unittest.mock import ANY, MagicMock, patch

import pytest

from qwenpaw.token_usage.buffer import TokenUsageBuffer, _UsageEvent
from qwenpaw.token_usage.storage import load_data


@pytest.fixture
def tmp_path_autouse(tmp_path: Path) -> Path:
    return tmp_path


# ── Test: dirty flag stays True after failed save ──

@pytest.mark.asyncio
async def test_dirty_after_failed_flush(tmp_path: Path):
    """Bug repro: dirty flag should remain True when save_data_sync fails."""
    usage_file = tmp_path / "usage.json"
    buf = TokenUsageBuffer(usage_file)

    # Seed the cache and mark dirty
    buf._disk_cache = {"2026-07-23": {"openai:gpt-4": {"prompt_tokens": 100}}}
    buf._dirty = True

    # Make save_data_sync fail by returning False (simulating OSError)
    with patch(
        "qwenpaw.token_usage.buffer.save_data_sync",
        return_value=False,
    ):
        await buf._flush_once()

    # Key assertion: dirty flag must still be True after failed write
    assert buf._dirty is True, (
        "BUG: dirty flag was cleared after a failed write. "
        "The next periodic flush would skip this data."
    )


# ── Test: dirty flag is cleared on successful save ──

@pytest.mark.asyncio
async def test_dirty_cleared_after_successful_flush(tmp_path: Path):
    """Dirty flag should be cleared after a successful write."""
    usage_file = tmp_path / "usage.json"
    buf = TokenUsageBuffer(usage_file)

    buf._disk_cache = {"2026-07-23": {"openai:gpt-4": {"prompt_tokens": 100}}}
    buf._dirty = True

    # Actually write to disk
    await buf._flush_once()

    assert buf._dirty is False
    assert usage_file.exists()


# ── Test: retry after failed flush succeeds ──

@pytest.mark.asyncio
async def test_retry_after_transient_failure(tmp_path: Path):
    """A later flush should succeed after a transient failure is resolved."""
    usage_file = tmp_path / "usage.json"
    buf = TokenUsageBuffer(usage_file)

    buf._disk_cache = {"2026-07-23": {"openai:gpt-4": {"prompt_tokens": 100}}}
    buf._dirty = True

    # First call: simulate failure
    call_count = 0

    def _failing_save(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return False
        # Second call: delegate to real implementation
        from qwenpaw.token_usage.storage import save_data_sync as real_save
        return real_save(*args, **kwargs)

    with patch(
        "qwenpaw.token_usage.buffer.save_data_sync",
        side_effect=_failing_save,
    ):
        # First flush — fails
        await buf._flush_once()
        assert buf._dirty is True
        assert not usage_file.exists()

        # Second flush — succeeds
        await buf._flush_once()
        assert buf._dirty is False
        assert usage_file.exists()

    # Verify data integrity
    loaded = json.loads(usage_file.read_text())
    assert loaded["2026-07-23"]["openai:gpt-4"]["prompt_tokens"] == 100


# ── Test: concurrent events preserve dirty state ──

@pytest.mark.asyncio
async def test_concurrent_event_preserves_dirty(tmp_path: Path):
    """Events arriving between snapshot and write keep buffer dirty."""
    usage_file = tmp_path / "usage.json"
    buf = TokenUsageBuffer(usage_file)

    buf._disk_cache = {"2026-07-23": {"openai:gpt-4": {"prompt_tokens": 100}}}
    buf._dirty = True

    write_started = asyncio.Event()

    async def _simulate_concurrent_event():
        await write_started.wait()
        buf._disk_cache["2026-07-23"]["openai:gpt-4"]["prompt_tokens"] += 50
        buf._dirty = True

    original_save = __import__(
        "qwenpaw.token_usage.storage", fromlist=["save_data_sync"]
    ).save_data_sync

    def _delayed_save(path, data):
        write_started.set()
        return original_save(path, data)

    with patch(
        "qwenpaw.token_usage.buffer.save_data_sync",
        side_effect=_delayed_save,
    ):
        # Start flush and concurrent mutation in parallel
        await asyncio.gather(
            buf._flush_once(),
            _simulate_concurrent_event(),
        )

    # The concurrent event happened after the snapshot — dirty must stay True
    assert buf._dirty is True, (
        "BUG: concurrent event's dirty state was lost. "
        "The new tokens will never be persisted."
    )


# ── Test: force flush still works ──

@pytest.mark.asyncio
async def test_force_flush_succeeds(tmp_path: Path):
    """Force flush should write even when not dirty."""
    usage_file = tmp_path / "usage.json"
    buf = TokenUsageBuffer(usage_file)

    buf._disk_cache = {}
    buf._dirty = False

    await buf._flush_once(force=True)

    assert usage_file.exists()
    assert buf._dirty is False
