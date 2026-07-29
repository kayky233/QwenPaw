"""Normalize validation failures for deterministic diagnosis and retries."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import hashlib
import re


class FailureCategory(str, Enum):
    IMPLEMENTATION = "implementation"
    TEST = "test"
    SCOPE = "scope"
    DEPENDENCY = "dependency"
    ENVIRONMENT = "environment"
    FLAKY = "flaky"
    TIMEOUT = "timeout"
    BUILD = "build"
    LINT = "lint"
    TYPE = "type"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class FailureSignature:
    category: FailureCategory
    test_id: str | None
    exception_type: str | None
    message: str
    fingerprint: str


_TEST_ID_RE = re.compile(r"(?:FAILED|ERROR)\s+([^\s]+::[^\s]+)")
_EXCEPTION_RE = re.compile(r"\b([A-Za-z_][A-Za-z0-9_]*(?:Error|Exception))\b")


def classify_failure(stderr: str, stdout: str = "", timed_out: bool = False) -> FailureCategory:
    text = f"{stdout}\n{stderr}".lower()
    if timed_out or "timed out" in text or "timeout" in text:
        return FailureCategory.TIMEOUT
    if "scope" in text and ("outside" in text or "frozen" in text or "unapproved" in text):
        return FailureCategory.SCOPE
    if "modulenotfounderror" in text or "no module named" in text or "dependency" in text:
        return FailureCategory.DEPENDENCY
    if "mypy" in text or "type error" in text or "typecheck" in text:
        return FailureCategory.TYPE
    if "ruff" in text or "flake8" in text or "lint" in text:
        return FailureCategory.LINT
    if "npm run build" in text or "build failed" in text or "compilation failed" in text:
        return FailureCategory.BUILD
    if "connection refused" in text or "permission denied" in text or "not found" in text:
        return FailureCategory.ENVIRONMENT
    if "failed" in text or "assert" in text or "error" in text:
        return FailureCategory.IMPLEMENTATION
    return FailureCategory.UNKNOWN


def build_failure_signature(
    stderr: str,
    stdout: str = "",
    timed_out: bool = False,
) -> FailureSignature:
    text = f"{stdout}\n{stderr}".strip()
    test_match = _TEST_ID_RE.search(text)
    exception_match = _EXCEPTION_RE.search(text)
    normalized = re.sub(r"0x[0-9a-fA-F]+|\d+\.\d+s|/[^\s:]+", "<dynamic>", text)
    fingerprint = hashlib.sha256(normalized.encode("utf-8")).hexdigest()
    return FailureSignature(
        category=classify_failure(stderr, stdout, timed_out),
        test_id=test_match.group(1) if test_match else None,
        exception_type=exception_match.group(1) if exception_match else None,
        message=text[:500],
        fingerprint=fingerprint,
    )
