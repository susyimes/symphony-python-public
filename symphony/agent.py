from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Awaitable, Callable

from .codex_client import JsonRpcCodexClient
from .config import ServiceConfig
from .errors import SymphonyError
from .logging_utils import log_event
from .models import Issue, WorkflowDefinition
from .template import render_prompt
from .tracker import IssueTracker
from .workspace import WorkspaceManager

AgentEventCallback = Callable[[str, dict], Awaitable[None]]


@dataclass(slots=True)
class WorkerResult:
    issue_id: str
    issue_identifier: str
    normal: bool
    reason: str
    error: str | None = None


class AgentRunner:
    def __init__(
        self,
        config: ServiceConfig,
        workflow: WorkflowDefinition,
        tracker: IssueTracker,
        workspace_manager: WorkspaceManager,
        *,
        logger: logging.Logger | None = None,
    ) -> None:
        self.config = config
        self.workflow = workflow
        self.tracker = tracker
        self.workspace_manager = workspace_manager
        self.logger = logger or logging.getLogger(__name__)

    async def run_attempt(
        self,
        issue: Issue,
        attempt: int | None,
        *,
        on_event: AgentEventCallback | None = None,
    ) -> WorkerResult:
        workspace_path: Path | None = None
        codex: JsonRpcCodexClient | None = None
        try:
            log_event(self.logger, logging.INFO, "worker_preparing", issue_id=issue.id, issue_identifier=issue.identifier)
            workspace = await self.workspace_manager.create_for_issue(issue.identifier)
            workspace_path = workspace.path
            await self.workspace_manager.before_run(workspace.path)
            self.workspace_manager.validate_agent_cwd(workspace.path, workspace.path)

            codex = JsonRpcCodexClient(self.config.codex, tracker_config=self.config.tracker, logger=self.logger)
            await codex.start(workspace.path)

            current_issue = issue
            for turn_number in range(1, self.config.agent.max_turns + 1):
                prompt = build_turn_prompt(self.workflow, current_issue, attempt, turn_number)

                async def forward(event: dict) -> None:
                    if on_event:
                        await on_event(issue.id, event)

                result = await codex.run_turn(
                    prompt,
                    workspace.path,
                    title=f"{current_issue.identifier}: {current_issue.title}",
                    on_event=forward,
                )
                if not result.success:
                    return WorkerResult(issue.id, issue.identifier, False, "agent_turn_error", result.error)

                refreshed = await self.tracker.fetch_issue_states_by_ids([issue.id])
                if refreshed:
                    current_issue = refreshed[0]
                if current_issue.state_key not in self.config.tracker.active_state_keys:
                    break

            return WorkerResult(issue.id, issue.identifier, True, "normal")
        except SymphonyError as exc:
            return WorkerResult(issue.id, issue.identifier, False, exc.code, exc.message)
        except Exception as exc:
            return WorkerResult(issue.id, issue.identifier, False, "worker_error", str(exc))
        finally:
            if codex is not None:
                await codex.stop()
            if workspace_path is not None:
                await self.workspace_manager.after_run(workspace_path)


def build_turn_prompt(workflow: WorkflowDefinition, issue: Issue, attempt: int | None, turn_number: int) -> str:
    if turn_number == 1:
        return render_prompt(workflow.prompt_template, issue, attempt)
    return (
        f"Continue work on {issue.identifier}: {issue.title}.\n\n"
        "Use the existing thread and workspace context. Do not resend or restate the original task prompt. "
        "Inspect the current issue/repository state and keep moving toward the workflow-defined handoff."
    )
