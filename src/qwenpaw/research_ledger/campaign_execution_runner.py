"""Credential-isolated subprocess execution for Issue Campaign validation."""

from __future__ import annotations

import os
import re
from dataclasses import replace
from pathlib import Path

from .execution_runner import (
    CommandRequest,
    CommandResult,
    ExecutionCapabilities,
    LocalSubprocessRunner,
)

_ALLOWED_ENVIRONMENT = frozenset(
    {
        "PATH",
        "LANG",
        "LC_ALL",
        "LC_CTYPE",
        "TERM",
        "TMPDIR",
        "TEMP",
        "TMP",
        "VIRTUAL_ENV",
        "PYTHONPATH",
        "PYTHONUTF8",
        "PYTHONIOENCODING",
        "PYTHONUNBUFFERED",
        "UV_CACHE_DIR",
        "UV_PROJECT_ENVIRONMENT",
        "PIP_CACHE_DIR",
        "PIP_DISABLE_PIP_VERSION_CHECK",
        "PIP_CERT",
        "NPM_CONFIG_CACHE",
        "NODE_PATH",
        "RUSTUP_HOME",
        "GOMODCACHE",
        "GOCACHE",
        "JAVA_HOME",
        "SYSTEMROOT",
        "WINDIR",
        "COMSPEC",
        "PATHEXT",
        "SSL_CERT_FILE",
        "REQUESTS_CA_BUNDLE",
        "CURL_CA_BUNDLE",
        "NODE_EXTRA_CA_CERTS",
        "GIT_SSL_CAINFO",
    }
)
_BLOCKED_NAME_PARTS = (
    "TOKEN",
    "SECRET",
    "PASSWORD",
    "PASSWD",
    "API_KEY",
    "ACCESS_KEY",
    "PRIVATE_KEY",
    "CREDENTIAL",
    "AUTHORIZATION",
    "COOKIE",
    "SESSION",
)
_REDACTION_PATTERNS = (
    re.compile(r"(?i)(authorization\s*[:=]\s*bearer\s+)[^\s]+"),
    re.compile(
        r"(?i)((?:token|secret|password|passwd|api[_-]?key)\s*[:=]\s*)[^\s]+"
    ),
    re.compile(r"\bgh[pousr]_[A-Za-z0-9_]{20,}\b"),
    re.compile(r"\bgithub_pat_[A-Za-z0-9_]{20,}\b"),
    re.compile(r"\bsk-[A-Za-z0-9_-]{16,}\b"),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    re.compile(
        r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?"
        r"-----END [A-Z ]*PRIVATE KEY-----",
        re.DOTALL,
    ),
)


def _is_sensitive_name(name: str) -> bool:
    normalized = name.upper()
    return any(part in normalized for part in _BLOCKED_NAME_PARTS)


def redact_campaign_output(value: str) -> str:
    """Remove common credentials from persisted and reviewed command output."""

    redacted = value
    for pattern in _REDACTION_PATTERNS:
        redacted = pattern.sub(
            lambda match: (
                (match.group(1) if match.lastindex else "") + "[REDACTED]"
            ),
            redacted,
        )
    return redacted


class CampaignSubprocessRunner:
    """Run validation with a minimal environment and isolated HOME directory."""

    def __init__(
        self,
        home_dir: Path,
        *,
        source_environment: dict[str, str] | None = None,
    ) -> None:
        self.home_dir = home_dir.expanduser().resolve()
        self.home_dir.mkdir(parents=True, exist_ok=True)
        source = source_environment if source_environment is not None else os.environ
        self.base_environment = {
            key: str(value)
            for key, value in source.items()
            if key in _ALLOWED_ENVIRONMENT and not _is_sensitive_name(key)
        }
        # Deliberately do not preserve CARGO_HOME, GH config, cloud config, or
        # user HOME: each can contain long-lived credentials. Tool caches that
        # are safe and explicitly allowlisted remain available.
        self.base_environment.update(
            {
                "HOME": str(self.home_dir),
                "USERPROFILE": str(self.home_dir),
                "GIT_CONFIG_NOSYSTEM": "1",
                "GIT_TERMINAL_PROMPT": "0",
                "PIP_NO_INPUT": "1",
                "CI": "1",
            }
        )
        self.delegate = LocalSubprocessRunner()

    @property
    def capabilities(self) -> ExecutionCapabilities:
        return self.delegate.capabilities

    def run(self, request: CommandRequest) -> CommandResult:
        environment = dict(self.base_environment)
        for key, value in request.environment.items():
            if key in _ALLOWED_ENVIRONMENT and not _is_sensitive_name(key):
                environment[key] = str(value)
        isolated = replace(request, environment=environment)
        result = self.delegate.run(isolated)
        return replace(
            result,
            stdout=redact_campaign_output(result.stdout),
            stderr=redact_campaign_output(result.stderr),
        )
