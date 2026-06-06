from __future__ import annotations

import logging
from pathlib import Path
from typing import Mapping

from .config import ServiceConfig, build_service_config, validate_dispatch_config
from .errors import ConfigError, WorkflowError
from .logging_utils import log_event
from .models import WorkflowDefinition
from .workflow import load_workflow


class WorkflowRuntime:
    def __init__(
        self,
        workflow_path: Path,
        *,
        env: Mapping[str, str] | None = None,
        cli_port: int | None = None,
        logger: logging.Logger | None = None,
    ) -> None:
        self.workflow_path = workflow_path
        self.env = env
        self.cli_port = cli_port
        self.logger = logger or logging.getLogger(__name__)
        self.workflow: WorkflowDefinition | None = None
        self.config: ServiceConfig | None = None
        self.last_error: Exception | None = None

    def load_startup(self) -> None:
        workflow = load_workflow(self.workflow_path)
        config = build_service_config(workflow, env=self.env, cli_port=self.cli_port)
        validate_dispatch_config(config)
        self.workflow = workflow
        self.config = config
        self.last_error = None

    def maybe_reload(self) -> bool:
        if self.workflow is None or self.config is None:
            self.load_startup()
            return True
        try:
            current_mtime = self.workflow_path.stat().st_mtime_ns
        except OSError as exc:
            self.last_error = WorkflowError("missing_workflow_file", f"workflow file cannot be read: {self.workflow_path}")
            log_event(self.logger, logging.ERROR, "workflow_reload_failed", error=self.last_error)
            return False
        if current_mtime == self.workflow.mtime_ns:
            return False
        try:
            workflow = load_workflow(self.workflow_path)
            config = build_service_config(workflow, env=self.env, cli_port=self.cli_port)
            validate_dispatch_config(config)
        except (WorkflowError, ConfigError) as exc:
            self.last_error = exc
            log_event(self.logger, logging.ERROR, "workflow_reload_failed", error=exc)
            return False
        self.workflow = workflow
        self.config = config
        self.last_error = None
        log_event(self.logger, logging.INFO, "workflow_reloaded", path=self.workflow_path)
        return True

    def require(self) -> tuple[WorkflowDefinition, ServiceConfig]:
        if self.workflow is None or self.config is None:
            self.load_startup()
        assert self.workflow is not None and self.config is not None
        return self.workflow, self.config
