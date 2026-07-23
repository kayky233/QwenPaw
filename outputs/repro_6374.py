#!/usr/bin/env python3
"""Standalone E2E reproduction script for Issue #6374.

This script exercises the full round-trip: seed → inject failure → flush →
verify dirty flag → retry flush → verify data.

Usage:
    uv run --python 3.12 python outputs/repro_6374.py
"""
from __future__ import annotations

import asyncio
import json
import tempfile
from pathlib import Path
from unittest.mock import patch


async def run_all_cases() -> dict:
    """Return structured results for all 5 E2E cases."""
    results: dict = {}

    # ── Import after potential git checkout ──
    from qwenpaw.token_usage.buffer import TokenUsageBuffer
    from qwenpaw.token_usage.storage import save_data_sync as real_save

    # ───────────────────────────────────────────────────────
    # C1: Single OSError — dirty must survive
    # ───────────────────────────────────────────────────────
    with tempfile.TemporaryDirectory() as td:
        usage_file = Path(td) / "usage.json"
        buf = TokenUsageBuffer(usage_file)
        buf._disk_cache = {
            "2026-07-23": {
                "openai:gpt-4": {
                    "provider_id": "openai",
                    "model_name": "gpt-4",
                    "prompt_tokens": 500,
                    "completion_tokens": 200,
                    "call_count": 3,
                }
            }
        }
        buf._dirty = True

        with patch(
            "qwenpaw.token_usage.buffer.save_data_sync",
            return_value=False,
        ):
            await buf._flush_once()

        c1 = {
            "dirty_after_failed_flush": buf._dirty,
            "file_exists_after_failed": usage_file.exists(),
        }
        results["C1_single_OSError"] = c1

    # ───────────────────────────────────────────────────────
    # C2: Transient failure → retry success
    # ───────────────────────────────────────────────────────
    with tempfile.TemporaryDirectory() as td:
        usage_file = Path(td) / "usage.json"
        buf = TokenUsageBuffer(usage_file)
        buf._disk_cache = {
            "2026-07-23": {
                "openai:gpt-4": {
                    "provider_id": "openai",
                    "model_name": "gpt-4",
                    "prompt_tokens": 500,
                    "completion_tokens": 200,
                    "call_count": 3,
                }
            }
        }
        buf._dirty = True

        call_count = 0

        def _interleaved_save(path, data):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return False
            return real_save(path, data)

        with patch(
            "qwenpaw.token_usage.buffer.save_data_sync",
            side_effect=_interleaved_save,
        ):
            await buf._flush_once()   # should fail
            await buf._flush_once()   # should succeed

        # read back file
        file_data = {}
        if usage_file.exists():
            file_data = json.loads(usage_file.read_text())

        c2 = {
            "write_attempts_after_retry": call_count,
            "file_exists_after_retry": usage_file.exists(),
            "persisted_prompt_tokens": file_data.get("2026-07-23", {})
            .get("openai:gpt-4", {})
            .get("prompt_tokens", 0),
        }
        results["C2_transient_retry"] = c2

    # ───────────────────────────────────────────────────────
    # C3: Concurrent event during flush
    # ───────────────────────────────────────────────────────
    with tempfile.TemporaryDirectory() as td:
        usage_file = Path(td) / "usage.json"
        buf = TokenUsageBuffer(usage_file)
        buf._disk_cache = {
            "2026-07-23": {
                "openai:gpt-4": {
                    "provider_id": "openai",
                    "model_name": "gpt-4",
                    "prompt_tokens": 100,
                    "completion_tokens": 0,
                    "call_count": 1,
                }
            }
        }
        buf._dirty = True

        write_started = asyncio.Event()
        signal_patched = asyncio.Event()

        def _patched_save(path, data):
            """Simulate a write that takes long enough for a concurrent event."""
            signal_patched.set()
            return real_save(path, data)

        async def _inject_concurrent_event():
            await signal_patched.wait()
            # Simulate a new event arriving
            day = buf._disk_cache.setdefault("2026-07-23", {})
            entry = day.setdefault("openai:gpt-4", {
                "provider_id": "openai",
                "model_name": "gpt-4",
                "prompt_tokens": 0,
                "completion_tokens": 0,
                "call_count": 0,
            })
            entry["prompt_tokens"] += 50
            buf._dirty = True

        with patch(
            "qwenpaw.token_usage.buffer.save_data_sync",
            side_effect=_patched_save,
        ):
            await asyncio.gather(
                buf._flush_once(),
                _inject_concurrent_event(),
            )

        c3 = {
            "dirty_after_concurrent": buf._dirty,
        }
        results["C3_concurrent_event"] = c3

    # ───────────────────────────────────────────────────────
    # C4: Normal path (no failure)
    # ───────────────────────────────────────────────────────
    with tempfile.TemporaryDirectory() as td:
        usage_file = Path(td) / "usage.json"
        buf = TokenUsageBuffer(usage_file)
        buf._disk_cache = {
            "2026-07-23": {
                "openai:gpt-4": {
                    "provider_id": "openai",
                    "model_name": "gpt-4",
                    "prompt_tokens": 100,
                    "completion_tokens": 50,
                    "call_count": 2,
                }
            }
        }
        buf._dirty = True
        await buf._flush_once()

        file_data = {}
        if usage_file.exists():
            file_data = json.loads(usage_file.read_text())

        c4 = {
            "file_exists": usage_file.exists(),
            "dirty_after_normal": buf._dirty,
            "persisted_prompt_tokens": file_data.get("2026-07-23", {})
            .get("openai:gpt-4", {})
            .get("prompt_tokens", 0),
        }
        results["C4_normal_path"] = c4

    # ───────────────────────────────────────────────────────
    # C5: Force flush
    # ───────────────────────────────────────────────────────
    with tempfile.TemporaryDirectory() as td:
        usage_file = Path(td) / "usage.json"
        buf = TokenUsageBuffer(usage_file)
        buf._disk_cache = {"2026-07-23": {}}
        buf._dirty = False
        await buf._flush_once(force=True)

        c5 = {
            "file_exists_after_force": usage_file.exists(),
            "dirty_after_force": buf._dirty,
        }
        results["C5_force_flush"] = c5

    return results


def print_results(label: str, results: dict) -> None:
    """Pretty-print results with pass/fail annotations."""
    print(f"\n{'═' * 60}")
    print(f"  {label}")
    print(f"{'═' * 60}")

    # Extract baseline metrics (same names as issue report)
    baseline = {
        "dirty_after_failed_flush": results["C1_single_OSError"]["dirty_after_failed_flush"],
        "write_attempts_after_retry": results["C2_transient_retry"]["write_attempts_after_retry"],
        "file_exists_after_retry": results["C2_transient_retry"]["file_exists_after_retry"],
    }

    print("\n  [Baseline metrics (from issue report)]")
    for k, v in baseline.items():
        print(f"  baseline {k} = {v}")

    print("\n  [Case-by-case]")
    for case, data in results.items():
        status = "✅" if _is_pass(case, data) else "❌"
        print(f"  {status} {case}: {data}")

    # Overall verdict
    all_ok = all(_is_pass(c, d) for c, d in results.items())
    print(f"\n  {'─' * 50}")
    print(f"  Overall: {'✅ ALL PASS' if all_ok else '❌ FAILURES DETECTED'}")
    print(f"{'═' * 60}\n")


def _is_pass(case: str, data: dict) -> bool:
    """Check expected values for fixed behavior."""
    expectations = {
        "C1_single_OSError":        lambda d: d["dirty_after_failed_flush"] is True,
        "C2_transient_retry":       lambda d: (
            d["write_attempts_after_retry"] == 2
            and d["file_exists_after_retry"] is True
            and d["persisted_prompt_tokens"] == 500
        ),
        "C3_concurrent_event":      lambda d: d["dirty_after_concurrent"] is True,
        "C4_normal_path":           lambda d: (
            d["file_exists"] is True
            and d["dirty_after_normal"] is False
        ),
        "C5_force_flush":           lambda d: d["file_exists_after_force"] is True,
    }
    checker = expectations.get(case)
    if checker is None:
        return True
    return checker(data)


if __name__ == "__main__":
    results = asyncio.run(run_all_cases())

    # Try to detect git branch/commit for labeling
    import subprocess
    try:
        commit = subprocess.check_output(
            ["git", "rev-parse", "--short", "HEAD"],
            text=True,
        ).strip()
        branch = subprocess.check_output(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            text=True,
        ).strip()
        label = f"commit={commit} branch={branch}"
    except Exception:
        label = "unknown"

    print_results(label, results)
