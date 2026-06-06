from __future__ import annotations

import asyncio
import logging
import os
import re
import shutil
from pathlib import Path

from .config import HooksConfig
from .errors import WorkspaceError
from .logging_utils import log_event
from .models import Workspace

WORKSPACE_KEY_RE = re.compile(r"[^A-Za-z0-9._-]")


def sanitize_workspace_key(identifier: str) -> str:
    key = WORKSPACE_KEY_RE.sub("_", identifier)
    return key or "_"


class WorkspaceManager:
    def __init__(self, root: Path, hooks: HooksConfig, *, logger: logging.Logger | None = None) -> None:
        self.root = root.resolve()
        self.hooks = hooks
        self.logger = logger or logging.getLogger(__name__)

    def path_for_identifier(self, identifier: str) -> Path:
        path = (self.root / sanitize_workspace_key(identifier)).resolve()
        self._ensure_inside_root(path)
        return path

    async def create_for_issue(self, identifier: str) -> Workspace:
        workspace_key = sanitize_workspace_key(identifier)
        path = (self.root / workspace_key).resolve()
        self._ensure_inside_root(path)
        self.root.mkdir(parents=True, exist_ok=True)

        created_now = False
        if path.exists():
            if not path.is_dir():
                raise WorkspaceError("workspace_path_not_directory", f"workspace path exists but is not a directory: {path}")
        else:
            path.mkdir(parents=True)
            created_now = True

        workspace = Workspace(path=path, workspace_key=workspace_key, created_now=created_now)
        if created_now and self.hooks.after_create:
            await self.run_hook("after_create", self.hooks.after_create, path, fatal=True)
        return workspace

    async def before_run(self, path: Path) -> None:
        if self.hooks.before_run:
            await self.run_hook("before_run", self.hooks.before_run, path, fatal=True)

    async def after_run(self, path: Path) -> None:
        if self.hooks.after_run:
            try:
                await self.run_hook("after_run", self.hooks.after_run, path, fatal=False)
            except WorkspaceError:
                pass

    async def remove_for_issue(self, identifier: str) -> None:
        path = self.path_for_identifier(identifier)
        if not path.exists():
            return
        if self.hooks.before_remove:
            try:
                await self.run_hook("before_remove", self.hooks.before_remove, path, fatal=False)
            except WorkspaceError:
                pass
        self._ensure_inside_root(path)
        if path.is_dir():
            shutil.rmtree(path)
        else:
            path.unlink()

    async def run_hook(self, name: str, script: str, cwd: Path, *, fatal: bool) -> None:
        cwd = cwd.resolve()
        self._ensure_inside_root(cwd)
        log_event(self.logger, logging.INFO, "hook_starting", hook=name, cwd=cwd)
        argv = hook_shell_argv(script)
        try:
            proc = await asyncio.create_subprocess_exec(
                *argv,
                cwd=str(cwd),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=self.hooks.timeout_ms / 1000)
        except asyncio.TimeoutError as exc:
            log_event(self.logger, logging.ERROR, "hook_timeout", hook=name, timeout_ms=self.hooks.timeout_ms)
            if fatal:
                raise WorkspaceError("hook_timeout", f"{name} hook timed out") from exc
            return
        except OSError as exc:
            log_event(self.logger, logging.ERROR, "hook_failed", hook=name, error=exc)
            if fatal:
                raise WorkspaceError("hook_failed", f"{name} hook failed to start") from exc
            return

        output = (stdout + stderr).decode("utf-8", errors="replace")
        if proc.returncode != 0:
            log_event(
                self.logger,
                logging.ERROR,
                "hook_failed",
                hook=name,
                returncode=proc.returncode,
                output=output[:1000],
            )
            if fatal:
                raise WorkspaceError("hook_failed", f"{name} hook exited with {proc.returncode}")
            return
        log_event(self.logger, logging.INFO, "hook_completed", hook=name, output=output[:1000])

    def validate_agent_cwd(self, cwd: Path, workspace_path: Path) -> None:
        cwd_resolved = cwd.resolve()
        workspace_resolved = workspace_path.resolve()
        self._ensure_inside_root(workspace_resolved)
        if cwd_resolved != workspace_resolved:
            raise WorkspaceError("invalid_workspace_cwd", f"agent cwd must be workspace path: {workspace_resolved}")

    def _ensure_inside_root(self, path: Path) -> None:
        root = self.root.resolve()
        target = path.resolve()
        try:
            target.relative_to(root)
        except ValueError as exc:
            raise WorkspaceError("workspace_outside_root", f"workspace path escapes root: {target}") from exc


def hook_shell_argv(script: str) -> list[str]:
    if os.name == "nt":
        return ["powershell.exe", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-Command", script]
    return ["sh", "-lc", script]
