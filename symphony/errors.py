from __future__ import annotations


class SymphonyError(Exception):
    """Base typed error exposed to the orchestrator and CLI."""

    def __init__(self, code: str, message: str, *, detail: object | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.detail = detail

    def __str__(self) -> str:
        return f"{self.code}: {self.message}"


class WorkflowError(SymphonyError):
    pass


class ConfigError(SymphonyError):
    pass


class TemplateRenderError(SymphonyError):
    pass


class WorkspaceError(SymphonyError):
    pass


class TrackerError(SymphonyError):
    pass


class CodexClientError(SymphonyError):
    pass
