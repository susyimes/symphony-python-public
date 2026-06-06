from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Awaitable, Callable, Any

from .agent import AgentRunner, WorkerResult
from .config import ServiceConfig
from .errors import SymphonyError
from .logging_utils import log_event
from .models import CodexTotals, Issue, normalize_state, to_iso, utc_now
from .runtime import WorkflowRuntime
from .tracker import IssueTracker
from .workspace import WorkspaceManager


RunnerFactory = Callable[[ServiceConfig, WorkspaceManager], AgentRunner]


@dataclass(slots=True)
class RetryEntry:
    issue_id: str
    identifier: str
    attempt: int
    due_at_ms: float
    error: str | None = None
    issue_url: str | None = None
    timer_handle: asyncio.TimerHandle | None = None


@dataclass(slots=True)
class RunningEntry:
    issue: Issue
    task: asyncio.Task[WorkerResult]
    started_at: datetime
    retry_attempt: int | None = None
    session_id: str | None = None
    codex_app_server_pid: int | None = None
    last_codex_event: str | None = None
    last_codex_timestamp: datetime | None = None
    last_codex_message: str | None = None
    codex_input_tokens: int = 0
    codex_output_tokens: int = 0
    codex_total_tokens: int = 0
    last_reported_input_tokens: int = 0
    last_reported_output_tokens: int = 0
    last_reported_total_tokens: int = 0
    turn_count: int = 0


@dataclass(slots=True)
class OrchestratorState:
    poll_interval_ms: int
    max_concurrent_agents: int
    running: dict[str, RunningEntry] = field(default_factory=dict)
    claimed: set[str] = field(default_factory=set)
    retry_attempts: dict[str, RetryEntry] = field(default_factory=dict)
    completed: set[str] = field(default_factory=set)
    codex_totals: CodexTotals = field(default_factory=CodexTotals)
    codex_rate_limits: dict[str, Any] | None = None


class Orchestrator:
    def __init__(
        self,
        runtime: WorkflowRuntime,
        tracker: IssueTracker,
        *,
        runner_factory: RunnerFactory | None = None,
        logger: logging.Logger | None = None,
    ) -> None:
        workflow, config = runtime.require()
        self.runtime = runtime
        self.tracker = tracker
        self.logger = logger or logging.getLogger(__name__)
        self.state = OrchestratorState(
            poll_interval_ms=config.polling.interval_ms,
            max_concurrent_agents=config.agent.max_concurrent_agents,
        )
        self._runner_factory = runner_factory or self._default_runner_factory
        self._tick_requested = asyncio.Event()
        self._stopped = asyncio.Event()
        self._lock = asyncio.Lock()
        self._workflow = workflow
        self._config = config

    async def start(self) -> None:
        await self.startup_terminal_workspace_cleanup()
        while not self._stopped.is_set():
            await self.tick_once()
            try:
                await asyncio.wait_for(self._tick_requested.wait(), timeout=self.state.poll_interval_ms / 1000)
                self._tick_requested.clear()
            except asyncio.TimeoutError:
                pass

    async def stop(self) -> None:
        self._stopped.set()
        for entry in list(self.state.running.values()):
            entry.task.cancel()
        await asyncio.gather(*(entry.task for entry in self.state.running.values()), return_exceptions=True)
        for retry in self.state.retry_attempts.values():
            if retry.timer_handle:
                retry.timer_handle.cancel()

    def request_tick(self) -> None:
        self._tick_requested.set()

    async def tick_once(self) -> None:
        async with self._lock:
            self.runtime.maybe_reload()
            self._workflow, self._config = self.runtime.require()
            self.state.poll_interval_ms = self._config.polling.interval_ms
            self.state.max_concurrent_agents = self._config.agent.max_concurrent_agents
            await self.reconcile_running_issues()
            try:
                issues = await self.tracker.fetch_candidate_issues()
            except SymphonyError as exc:
                log_event(self.logger, logging.ERROR, "candidate_fetch_failed", error=exc)
                return
            for issue in sort_for_dispatch(issues):
                if self.available_slots() <= 0:
                    break
                if self.should_dispatch(issue):
                    self.dispatch_issue(issue, attempt=None)

    async def startup_terminal_workspace_cleanup(self) -> None:
        try:
            issues = await self.tracker.fetch_issues_by_states(self._config.tracker.terminal_states)
        except Exception as exc:
            log_event(self.logger, logging.WARNING, "startup_cleanup_failed", error=exc)
            return
        manager = WorkspaceManager(self._config.workspace.root, self._config.hooks, logger=self.logger)
        for issue in issues:
            await manager.remove_for_issue(issue.identifier)

    async def reconcile_running_issues(self) -> None:
        await self._collect_finished_workers()
        await self._reconcile_stalls()
        if not self.state.running:
            return
        ids = list(self.state.running)
        try:
            refreshed = await self.tracker.fetch_issue_states_by_ids(ids)
        except Exception as exc:
            log_event(self.logger, logging.WARNING, "running_state_refresh_failed", error=exc)
            return
        by_id = {issue.id: issue for issue in refreshed}
        for issue_id, entry in list(self.state.running.items()):
            issue = by_id.get(issue_id)
            if issue is None:
                continue
            if issue.state_key in self._config.tracker.terminal_state_keys:
                await self.terminate_running_issue(issue_id, cleanup_workspace=True, reason="terminal_state", retry=False)
            elif issue.state_key in self._config.tracker.active_state_keys and self._has_required_labels(issue):
                entry.issue = issue
            else:
                await self.terminate_running_issue(issue_id, cleanup_workspace=False, reason="non_active_state", retry=False)

    def dispatch_issue(self, issue: Issue, attempt: int | None) -> None:
        if issue.id in self.state.claimed or issue.id in self.state.running:
            return
        manager = WorkspaceManager(self._config.workspace.root, self._config.hooks, logger=self.logger)
        runner = self._runner_factory(self._config, manager)
        task = asyncio.create_task(runner.run_attempt(issue, attempt, on_event=self.handle_codex_event))
        task.add_done_callback(lambda _task, issue_id=issue.id: self.request_tick())
        self.state.running[issue.id] = RunningEntry(issue=issue, task=task, retry_attempt=attempt, started_at=utc_now())
        self.state.claimed.add(issue.id)
        retry = self.state.retry_attempts.pop(issue.id, None)
        if retry and retry.timer_handle:
            retry.timer_handle.cancel()
        log_event(self.logger, logging.INFO, "issue_dispatched", issue_id=issue.id, issue_identifier=issue.identifier)

    async def handle_codex_event(self, issue_id: str, event: dict[str, Any]) -> None:
        entry = self.state.running.get(issue_id)
        if entry is None:
            return
        entry.last_codex_event = str(event.get("event") or "")
        timestamp = event.get("timestamp")
        entry.last_codex_timestamp = timestamp if isinstance(timestamp, datetime) else utc_now()
        entry.last_codex_message = str(event.get("message") or "")[:500]
        if event.get("session_id"):
            entry.session_id = str(event["session_id"])
        if event.get("codex_app_server_pid"):
            entry.codex_app_server_pid = int(event["codex_app_server_pid"])
        if event.get("event") == "turn_started":
            entry.turn_count += 1
        if event.get("event") == "rate_limits_updated":
            entry_payload = event.get("payload")
            self.state.codex_rate_limits = entry_payload if isinstance(entry_payload, dict) else {"payload": entry_payload}
        self._apply_token_usage(entry, event)

    async def terminate_running_issue(self, issue_id: str, *, cleanup_workspace: bool, reason: str, retry: bool) -> None:
        entry = self.state.running.get(issue_id)
        if entry is None:
            return
        entry.task.cancel()
        await asyncio.gather(entry.task, return_exceptions=True)
        self._finish_running_entry(issue_id, normal=False, error=reason, release=not retry)
        if cleanup_workspace:
            manager = WorkspaceManager(self._config.workspace.root, self._config.hooks, logger=self.logger)
            await manager.remove_for_issue(entry.issue.identifier)
            self.state.claimed.discard(issue_id)
        log_event(self.logger, logging.INFO, "running_terminated", issue_id=issue_id, issue_identifier=entry.issue.identifier, reason=reason)

    def should_dispatch(self, issue: Issue) -> bool:
        if not issue.id or not issue.identifier or not issue.title or not issue.state:
            return False
        if issue.state_key not in self._config.tracker.active_state_keys:
            return False
        if issue.state_key in self._config.tracker.terminal_state_keys:
            return False
        if issue.id in self.state.running or issue.id in self.state.claimed:
            return False
        if self.available_slots() <= 0 or not self._has_state_slot(issue.state_key):
            return False
        if not self._has_required_labels(issue):
            return False
        if issue.state_key == "todo":
            for blocker in issue.blocked_by:
                if normalize_state(blocker.state) not in self._config.tracker.terminal_state_keys:
                    return False
        return True

    def available_slots(self) -> int:
        return max(self.state.max_concurrent_agents - len(self.state.running), 0)

    def snapshot(self) -> dict[str, Any]:
        now = utc_now()
        running = [self._running_row(entry) for entry in self.state.running.values()]
        retrying = [self._retry_row(entry) for entry in self.state.retry_attempts.values()]
        totals = CodexTotals(
            input_tokens=self.state.codex_totals.input_tokens,
            output_tokens=self.state.codex_totals.output_tokens,
            total_tokens=self.state.codex_totals.total_tokens,
            seconds_running=self.state.codex_totals.seconds_running
            + sum((now - entry.started_at).total_seconds() for entry in self.state.running.values()),
        )
        return {
            "generated_at": to_iso(now),
            "counts": {"running": len(running), "retrying": len(retrying)},
            "running": running,
            "retrying": retrying,
            "codex_totals": totals.to_dict(),
            "rate_limits": self.state.codex_rate_limits,
            "last_error": str(self.runtime.last_error) if self.runtime.last_error else None,
        }

    def issue_snapshot(self, identifier: str) -> dict[str, Any] | None:
        for entry in self.state.running.values():
            if entry.issue.identifier == identifier:
                return {
                    "issue_identifier": identifier,
                    "issue_id": entry.issue.id,
                    "status": "running",
                    "workspace": {"path": str(WorkspaceManager(self._config.workspace.root, self._config.hooks).path_for_identifier(entry.issue.identifier))},
                    "running": self._running_row(entry),
                    "retry": None,
                    "recent_events": [],
                    "last_error": None,
                    "tracked": {},
                }
        for retry in self.state.retry_attempts.values():
            if retry.identifier == identifier:
                return {
                    "issue_identifier": identifier,
                    "issue_id": retry.issue_id,
                    "status": "retrying",
                    "workspace": {"path": str(WorkspaceManager(self._config.workspace.root, self._config.hooks).path_for_identifier(retry.identifier))},
                    "running": None,
                    "retry": self._retry_row(retry),
                    "recent_events": [],
                    "last_error": retry.error,
                    "tracked": {},
                }
        return None

    async def _collect_finished_workers(self) -> None:
        for issue_id, entry in list(self.state.running.items()):
            if not entry.task.done():
                continue
            try:
                result = entry.task.result()
            except asyncio.CancelledError:
                result = WorkerResult(issue_id, entry.issue.identifier, False, "cancelled", "task cancelled")
            except Exception as exc:
                result = WorkerResult(issue_id, entry.issue.identifier, False, "worker_error", str(exc))
            self._finish_running_entry(issue_id, normal=result.normal, error=result.error or result.reason)

    def _finish_running_entry(self, issue_id: str, *, normal: bool, error: str | None, release: bool = False) -> None:
        entry = self.state.running.pop(issue_id, None)
        if entry is None:
            return
        self.state.codex_totals.seconds_running += (utc_now() - entry.started_at).total_seconds()
        self.state.codex_totals.input_tokens += max(entry.codex_input_tokens - entry.last_reported_input_tokens, 0)
        self.state.codex_totals.output_tokens += max(entry.codex_output_tokens - entry.last_reported_output_tokens, 0)
        self.state.codex_totals.total_tokens += max(entry.codex_total_tokens - entry.last_reported_total_tokens, 0)
        if release:
            self.state.claimed.discard(issue_id)
            return
        if normal:
            self.state.completed.add(issue_id)
            self.schedule_retry(issue_id, entry.issue.identifier, 1, continuation=True, issue_url=entry.issue.url)
        else:
            next_attempt = (entry.retry_attempt or 0) + 1
            self.schedule_retry(issue_id, entry.issue.identifier, next_attempt, error=error, issue_url=entry.issue.url)

    def schedule_retry(
        self,
        issue_id: str,
        identifier: str,
        attempt: int,
        *,
        continuation: bool = False,
        error: str | None = None,
        issue_url: str | None = None,
    ) -> None:
        existing = self.state.retry_attempts.pop(issue_id, None)
        if existing and existing.timer_handle:
            existing.timer_handle.cancel()
        delay_ms = 1000 if continuation else min(10000 * (2 ** max(attempt - 1, 0)), self._config.agent.max_retry_backoff_ms)
        loop = asyncio.get_running_loop()
        handle = loop.call_later(delay_ms / 1000, lambda: asyncio.create_task(self.handle_retry(issue_id)))
        entry = RetryEntry(
            issue_id=issue_id,
            identifier=identifier,
            attempt=attempt,
            due_at_ms=time.monotonic() * 1000 + delay_ms,
            error=error,
            issue_url=issue_url,
            timer_handle=handle,
        )
        self.state.retry_attempts[issue_id] = entry
        self.state.claimed.add(issue_id)
        log_event(self.logger, logging.INFO, "retry_scheduled", issue_id=issue_id, issue_identifier=identifier, attempt=attempt, error=error)

    async def handle_retry(self, issue_id: str) -> None:
        async with self._lock:
            retry = self.state.retry_attempts.pop(issue_id, None)
            if retry is None:
                return
            try:
                candidates = await self.tracker.fetch_candidate_issues()
            except Exception:
                self.schedule_retry(issue_id, retry.identifier, retry.attempt + 1, error="retry poll failed", issue_url=retry.issue_url)
                return
            issue = next((candidate for candidate in candidates if candidate.id == issue_id), None)
            if issue is None:
                self.state.claimed.discard(issue_id)
                return
            if self.available_slots() <= 0 or not self._has_state_slot(issue.state_key):
                self.schedule_retry(issue_id, issue.identifier, retry.attempt + 1, error="no available orchestrator slots", issue_url=issue.url)
                return
            self.state.claimed.discard(issue_id)
            if self.should_dispatch(issue):
                self.dispatch_issue(issue, attempt=retry.attempt)
            else:
                self.state.claimed.discard(issue_id)

    async def _reconcile_stalls(self) -> None:
        stall_timeout_ms = self._config.codex.stall_timeout_ms
        if stall_timeout_ms <= 0:
            return
        now = utc_now()
        for issue_id, entry in list(self.state.running.items()):
            since = entry.last_codex_timestamp or entry.started_at
            elapsed_ms = (now - since).total_seconds() * 1000
            if elapsed_ms > stall_timeout_ms:
                await self.terminate_running_issue(issue_id, cleanup_workspace=False, reason="stalled", retry=True)

    def _has_required_labels(self, issue: Issue) -> bool:
        labels = {label.strip().lower() for label in issue.labels}
        for required in self._config.tracker.required_labels:
            if not required or required not in labels:
                return False
        return True

    def _has_state_slot(self, state_key: str) -> bool:
        limit = self._config.agent.max_concurrent_agents_by_state.get(state_key, self.state.max_concurrent_agents)
        count = sum(1 for entry in self.state.running.values() if entry.issue.state_key == state_key)
        return count < limit

    def _apply_token_usage(self, entry: RunningEntry, event: dict[str, Any]) -> None:
        payload = event.get("payload")
        if not isinstance(payload, dict):
            return
        totals = None
        if event.get("event") == "token_usage_updated":
            usage = payload.get("tokenUsage") or {}
            totals = usage.get("total")
        elif "total_token_usage" in payload:
            totals = payload.get("total_token_usage")
        if not isinstance(totals, dict):
            return
        entry.codex_input_tokens = _usage_int(totals, "inputTokens", "input_tokens")
        entry.codex_output_tokens = _usage_int(totals, "outputTokens", "output_tokens")
        entry.codex_total_tokens = _usage_int(totals, "totalTokens", "total_tokens")

    def _running_row(self, entry: RunningEntry) -> dict[str, Any]:
        return {
            "issue_id": entry.issue.id,
            "issue_identifier": entry.issue.identifier,
            "issue_url": entry.issue.url,
            "state": entry.issue.state,
            "session_id": entry.session_id,
            "turn_count": entry.turn_count,
            "last_event": entry.last_codex_event,
            "last_message": entry.last_codex_message,
            "started_at": to_iso(entry.started_at),
            "last_event_at": to_iso(entry.last_codex_timestamp),
            "tokens": {
                "input_tokens": entry.codex_input_tokens,
                "output_tokens": entry.codex_output_tokens,
                "total_tokens": entry.codex_total_tokens,
            },
        }

    def _retry_row(self, entry: RetryEntry) -> dict[str, Any]:
        remaining_ms = max(entry.due_at_ms - time.monotonic() * 1000, 0)
        due_at = datetime.now(timezone.utc) + timedelta(milliseconds=remaining_ms)
        return {
            "issue_id": entry.issue_id,
            "issue_identifier": entry.identifier,
            "issue_url": entry.issue_url,
            "attempt": entry.attempt,
            "due_at": to_iso(due_at),
            "error": entry.error,
        }

    def _default_runner_factory(self, config: ServiceConfig, manager: WorkspaceManager) -> AgentRunner:
        return AgentRunner(config, self._workflow, self.tracker, manager, logger=self.logger)


def sort_for_dispatch(issues: list[Issue]) -> list[Issue]:
    return sorted(issues, key=lambda issue: (issue.priority if issue.priority is not None else 9999, issue.created_at or datetime.max.replace(tzinfo=timezone.utc), issue.identifier))


def _usage_int(source: dict[str, Any], *keys: str) -> int:
    for key in keys:
        value = source.get(key)
        if isinstance(value, int) and not isinstance(value, bool):
            return value
    return 0
