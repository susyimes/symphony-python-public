from __future__ import annotations

import asyncio
from datetime import timedelta
from pathlib import Path

import pytest

from conftest import make_issue, workflow_text
from symphony.agent import WorkerResult
from symphony.models import BlockerRef, utc_now
from symphony.orchestrator import Orchestrator, sort_for_dispatch
from symphony.runtime import WorkflowRuntime
from symphony.workflow import select_workflow_path


class FakeTracker:
    def __init__(self) -> None:
        self.candidates = []
        self.by_ids = {}
        self.by_states = []

    async def fetch_candidate_issues(self):
        return list(self.candidates)

    async def fetch_issues_by_states(self, state_names):
        return list(self.by_states)

    async def fetch_issue_states_by_ids(self, issue_ids):
        return [self.by_ids[issue_id] for issue_id in issue_ids if issue_id in self.by_ids]


class ImmediateRunner:
    def __init__(self, normal: bool = True) -> None:
        self.normal = normal

    async def run_attempt(self, issue, attempt, *, on_event=None):
        return WorkerResult(issue.id, issue.identifier, self.normal, "normal" if self.normal else "failed", None)


class BlockingRunner:
    async def run_attempt(self, issue, attempt, *, on_event=None):
        await asyncio.Event().wait()
        return WorkerResult(issue.id, issue.identifier, True, "normal")


def make_runtime(tmp_path: Path, extra: str = "") -> WorkflowRuntime:
    workflow_path = tmp_path / "WORKFLOW.md"
    workflow_path.write_text(workflow_text(extra), encoding="utf-8")
    runtime = WorkflowRuntime(select_workflow_path(workflow_path))
    runtime.load_startup()
    return runtime


def test_dispatch_sort_order() -> None:
    newest_high = make_issue("A-2", issue_id="2", priority=1)
    oldest_low = make_issue("A-1", issue_id="1", priority=2)
    no_priority = make_issue("A-3", issue_id="3", priority=None)
    newest_high.created_at = utc_now()

    assert [issue.identifier for issue in sort_for_dispatch([no_priority, oldest_low, newest_high])] == ["A-2", "A-1", "A-3"]


def test_todo_blocker_rule(tmp_path: Path) -> None:
    runtime = make_runtime(tmp_path)
    tracker = FakeTracker()
    orchestrator = Orchestrator(runtime, tracker, runner_factory=lambda config, manager: ImmediateRunner())

    blocked = make_issue()
    blocked.blocked_by = [BlockerRef(id="b", identifier="ABC-0", state="In Progress")]
    assert orchestrator.should_dispatch(blocked) is False

    unblocked = make_issue(issue_id="issue-2")
    unblocked.blocked_by = [BlockerRef(id="b", identifier="ABC-0", state="Done")]
    assert orchestrator.should_dispatch(unblocked) is True


@pytest.mark.asyncio
async def test_normal_worker_exit_schedules_continuation_retry(tmp_path: Path) -> None:
    runtime = make_runtime(tmp_path)
    tracker = FakeTracker()
    issue = make_issue()
    tracker.candidates = [issue]
    orchestrator = Orchestrator(runtime, tracker, runner_factory=lambda config, manager: ImmediateRunner(normal=True))

    await orchestrator.tick_once()
    await asyncio.sleep(0)
    await orchestrator.reconcile_running_issues()

    retry = orchestrator.state.retry_attempts[issue.id]
    assert retry.attempt == 1
    assert retry.error is None
    await orchestrator.stop()


@pytest.mark.asyncio
async def test_abnormal_worker_exit_uses_backoff_cap(tmp_path: Path) -> None:
    runtime = make_runtime(tmp_path)
    tracker = FakeTracker()
    issue = make_issue()
    tracker.candidates = [issue]
    orchestrator = Orchestrator(runtime, tracker, runner_factory=lambda config, manager: ImmediateRunner(normal=False))

    await orchestrator.tick_once()
    await asyncio.sleep(0)
    await orchestrator.reconcile_running_issues()

    retry = orchestrator.state.retry_attempts[issue.id]
    assert retry.attempt == 1
    assert retry.error == "failed"

    orchestrator.schedule_retry(issue.id, issue.identifier, 5, error="again")
    retry = orchestrator.state.retry_attempts[issue.id]
    remaining = retry.due_at_ms
    assert remaining > 0
    await orchestrator.stop()


@pytest.mark.asyncio
async def test_terminal_reconciliation_cleans_and_releases_without_retry(tmp_path: Path) -> None:
    runtime = make_runtime(tmp_path)
    tracker = FakeTracker()
    active = make_issue()
    done = make_issue(state="Done")
    tracker.by_ids = {active.id: done}
    orchestrator = Orchestrator(runtime, tracker, runner_factory=lambda config, manager: BlockingRunner())
    orchestrator.dispatch_issue(active, attempt=None)

    await orchestrator.reconcile_running_issues()

    assert active.id not in orchestrator.state.running
    assert active.id not in orchestrator.state.retry_attempts
    assert active.id not in orchestrator.state.claimed
    await orchestrator.stop()


@pytest.mark.asyncio
async def test_stall_detection_schedules_retry(tmp_path: Path) -> None:
    runtime = make_runtime(tmp_path, "codex:\n  command: codex app-server\n  stall_timeout_ms: 1\n")
    tracker = FakeTracker()
    issue = make_issue()
    tracker.by_ids = {issue.id: issue}
    orchestrator = Orchestrator(runtime, tracker, runner_factory=lambda config, manager: BlockingRunner())
    orchestrator.dispatch_issue(issue, attempt=None)
    orchestrator.state.running[issue.id].started_at = utc_now() - timedelta(seconds=10)

    await orchestrator.reconcile_running_issues()

    assert issue.id in orchestrator.state.retry_attempts
    assert orchestrator.state.retry_attempts[issue.id].error == "stalled"
    await orchestrator.stop()
