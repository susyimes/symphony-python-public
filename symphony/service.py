from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from .config import ServiceConfig
from .http_server import HttpStatusServer
from .logging_utils import log_event
from .orchestrator import Orchestrator
from .runtime import WorkflowRuntime
from .tracker_factory import create_tracker


class SymphonyService:
    def __init__(self, workflow_path: Path, *, cli_port: int | None = None, logger: logging.Logger | None = None) -> None:
        self.logger = logger or logging.getLogger(__name__)
        self.runtime = WorkflowRuntime(workflow_path, cli_port=cli_port, logger=self.logger)
        self.runtime.load_startup()
        _, config = self.runtime.require()
        self.tracker = create_tracker(config.tracker)
        self.orchestrator = Orchestrator(self.runtime, self.tracker, logger=self.logger)
        self.http: HttpStatusServer | None = None
        self._config = config

    @property
    def config(self) -> ServiceConfig:
        _, config = self.runtime.require()
        return config

    async def start(self, *, once: bool = False) -> None:
        port = self.config.server.port
        if port is not None:
            self.http = HttpStatusServer(self.orchestrator, host=self.config.server.host, port=port)
            await self.http.start()
            log_event(self.logger, logging.INFO, "http_started", host=self.config.server.host, port=self.http.bound_port or port)
        if once:
            await self.orchestrator.startup_terminal_workspace_cleanup()
            await self.orchestrator.tick_once()
            return
        await self.orchestrator.start()

    async def stop(self) -> None:
        await self.orchestrator.stop()
        if self.http:
            await self.http.stop()
        await self.tracker.aclose()


async def run_service(workflow_path: Path, *, cli_port: int | None, once: bool = False) -> None:
    service = SymphonyService(workflow_path, cli_port=cli_port)
    try:
        await service.start(once=once)
    finally:
        await service.stop()
