"""Synchronous API client used by the direct Campaign CLI."""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

_TERMINAL = {"delivered", "needs_revision", "blocked", "failed", "cancelled"}


class CampaignApiError(RuntimeError):
    """Raised when the local QwenPaw API rejects or cannot serve a request."""


@dataclass(frozen=True)
class CampaignWaitResult:
    state: dict[str, Any]
    elapsed_seconds: float


class CampaignApiClient:
    def __init__(
        self,
        base_url: str,
        *,
        agent_id: str = "default",
        timeout_seconds: float = 30.0,
        urlopen_func: Any = urlopen,
    ) -> None:
        normalized = base_url.rstrip("/")
        if not normalized.startswith(("http://", "https://")):
            raise ValueError("Campaign API URL must use http or https")
        self.base_url = normalized
        self.agent_id = agent_id
        self.timeout_seconds = timeout_seconds
        self.urlopen_func = urlopen_func

    def info(self) -> dict[str, Any]:
        return self._request("GET", "/research/campaigns-info")

    def start(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self._request("POST", "/research/campaigns/run", payload)

    def get(self, campaign_id: str) -> dict[str, Any]:
        return self._request("GET", f"/research/campaigns/{campaign_id}")

    def cancel(self, campaign_id: str) -> dict[str, Any]:
        return self._request(
            "POST",
            f"/research/campaigns/{campaign_id}/cancel",
            {},
        )

    def wait(
        self,
        campaign_id: str,
        *,
        timeout_seconds: float,
        poll_interval_seconds: float = 2.0,
        on_event: Callable[[dict[str, Any]], None] | None = None,
    ) -> CampaignWaitResult:
        if timeout_seconds <= 0:
            raise ValueError("Campaign wait timeout must be positive")
        if poll_interval_seconds <= 0:
            raise ValueError("Campaign poll interval must be positive")
        started = time.monotonic()
        seen_sequences: set[int] = set()
        while True:
            state = self.get(campaign_id)
            for event in state.get("events", ()):
                if not isinstance(event, dict):
                    continue
                sequence = int(event.get("sequence") or 0)
                if sequence in seen_sequences:
                    continue
                seen_sequences.add(sequence)
                if on_event is not None:
                    on_event(event)
            if str(state.get("status", "")) in _TERMINAL:
                return CampaignWaitResult(
                    state=state,
                    elapsed_seconds=time.monotonic() - started,
                )
            if time.monotonic() - started >= timeout_seconds:
                raise TimeoutError(
                    f"Campaign {campaign_id} did not finish within "
                    f"{timeout_seconds:.0f} seconds"
                )
            time.sleep(poll_interval_seconds)

    def _request(
        self,
        method: str,
        path: str,
        payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        body = None
        headers = {
            "Accept": "application/json",
            "User-Agent": "QwenPaw-Campaign-CLI",
            "X-Agent-Id": self.agent_id,
        }
        if payload is not None:
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            headers["Content-Type"] = "application/json"
        request = Request(
            self.base_url + path,
            data=body,
            headers=headers,
            method=method,
        )
        try:
            with self.urlopen_func(
                request,
                timeout=self.timeout_seconds,
            ) as response:
                raw = response.read().decode("utf-8")
        except HTTPError as exc:
            try:
                detail_raw = exc.read().decode("utf-8")
                detail = json.loads(detail_raw).get("detail", detail_raw)
            except (UnicodeError, json.JSONDecodeError, AttributeError):
                detail = str(exc.reason)
            raise CampaignApiError(
                f"Campaign API returned HTTP {exc.code}: {detail}"
            ) from exc
        except (URLError, TimeoutError, OSError) as exc:
            raise CampaignApiError(
                f"Campaign API is unavailable at {self.base_url}: "
                f"{type(exc).__name__}: {exc}"
            ) from exc
        try:
            result = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise CampaignApiError(
                "Campaign API returned a non-JSON response"
            ) from exc
        if not isinstance(result, dict):
            raise CampaignApiError("Campaign API returned an invalid payload")
        return result


def write_campaign_report(
    state: dict[str, Any],
    output: Path,
    *,
    elapsed_seconds: float | None = None,
) -> tuple[Path, Path]:
    json_path = output.expanduser().resolve()
    if json_path.suffix.lower() != ".json":
        json_path = json_path / "campaign-result.json"
    json_path.parent.mkdir(parents=True, exist_ok=True)
    payload = dict(state)
    if elapsed_seconds is not None:
        payload["cli_elapsed_seconds"] = elapsed_seconds
    json_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    outcome = state.get("outcome") if isinstance(state.get("outcome"), dict) else {}
    delivery = (
        outcome.get("delivery")
        if isinstance(outcome.get("delivery"), dict)
        else {}
    )
    artifacts = outcome.get("artifacts") if isinstance(outcome, dict) else []
    artifact_rows = "\n".join(
        "| {} | {} | {} | `{}` |".format(
            item.get("artifact_type", "unknown"),
            item.get("step_id", ""),
            "yes" if item.get("verified") else "no",
            str(item.get("content_hash", ""))[:16],
        )
        for item in artifacts
        if isinstance(item, dict)
    ) or "| none | - | - | - |"
    markdown_path = json_path.with_suffix(".md")
    markdown_path.write_text(
        "# AutoResearch Issue Campaign\n\n"
        f"- Campaign: `{state.get('campaign_id', '')}`\n"
        f"- Status: `{state.get('status', '')}`\n"
        f"- Repository: `{state.get('repository', '')}`\n"
        f"- Issue: `#{state.get('issue_number', '')}`\n"
        f"- Delivery mode: `{outcome.get('delivery_mode', '')}`\n"
        f"- Worktree: `{state.get('worktree_path', '')}`\n"
        f"- Branch: `{state.get('branch', '')}`\n"
        f"- Commit: `{delivery.get('commit_sha', '')}`\n"
        f"- Change request: `{delivery.get('url', '')}`\n"
        f"- Error: `{state.get('error', '')}`\n\n"
        "## Verified artifacts\n\n"
        "| Type | Step | Verified | SHA-256 |\n"
        "|---|---|---:|---|\n"
        f"{artifact_rows}\n",
        encoding="utf-8",
    )
    return json_path, markdown_path
