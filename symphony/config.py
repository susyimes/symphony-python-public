from __future__ import annotations

import os
import re
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping

from .errors import ConfigError
from .models import WorkflowDefinition, normalize_state

ENV_TOKEN_RE = re.compile(r"\$([A-Za-z_][A-Za-z0-9_]*)")


@dataclass(slots=True)
class TrackerConfig:
    kind: str | None = None
    endpoint: str | None = "https://api.linear.app/graphql"
    base_url: str | None = None
    api_key: str | None = None
    email: str | None = None
    api_token: str | None = None
    project_slug: str | None = None
    project_key: str | None = None
    jql: str | None = None
    required_labels: list[str] = field(default_factory=list)
    active_states: list[str] = field(default_factory=lambda: ["Todo", "In Progress"])
    terminal_states: list[str] = field(default_factory=lambda: ["Closed", "Cancelled", "Canceled", "Duplicate", "Done"])

    @property
    def active_state_keys(self) -> set[str]:
        return {normalize_state(state) for state in self.active_states}

    @property
    def terminal_state_keys(self) -> set[str]:
        return {normalize_state(state) for state in self.terminal_states}


@dataclass(slots=True)
class PollingConfig:
    interval_ms: int = 30000


@dataclass(slots=True)
class WorkspaceConfig:
    root: Path = field(default_factory=lambda: Path(tempfile.gettempdir()) / "symphony_workspaces")


@dataclass(slots=True)
class HooksConfig:
    after_create: str | None = None
    before_run: str | None = None
    after_run: str | None = None
    before_remove: str | None = None
    timeout_ms: int = 60000


@dataclass(slots=True)
class AgentConfig:
    max_concurrent_agents: int = 10
    max_turns: int = 20
    max_retry_backoff_ms: int = 300000
    max_concurrent_agents_by_state: dict[str, int] = field(default_factory=dict)


@dataclass(slots=True)
class CodexConfig:
    command: str = "codex app-server"
    approval_policy: Any = None
    thread_sandbox: Any = None
    turn_sandbox_policy: Any = None
    turn_timeout_ms: int = 3600000
    read_timeout_ms: int = 5000
    stall_timeout_ms: int = 300000


@dataclass(slots=True)
class ServerConfig:
    port: int | None = None
    host: str = "127.0.0.1"


@dataclass(slots=True)
class ServiceConfig:
    workflow_path: Path
    tracker: TrackerConfig
    polling: PollingConfig
    workspace: WorkspaceConfig
    hooks: HooksConfig
    agent: AgentConfig
    codex: CodexConfig
    server: ServerConfig = field(default_factory=ServerConfig)


def build_service_config(
    workflow: WorkflowDefinition,
    *,
    env: Mapping[str, str] | None = None,
    cli_port: int | None = None,
) -> ServiceConfig:
    source = workflow.config
    env_map = env if env is not None else os.environ
    base_dir = workflow.path.parent

    tracker_raw = _section(source, "tracker")
    tracker_kind = _optional_str(tracker_raw.get("kind"))
    if tracker_kind == "linear" or tracker_kind is None:
        endpoint = _optional_str(tracker_raw.get("endpoint")) or "https://api.linear.app/graphql"
    else:
        endpoint = _optional_str(tracker_raw.get("endpoint"))
    raw_api_key = tracker_raw.get("api_key", "$LINEAR_API_KEY" if tracker_kind == "linear" else None)
    raw_jira_token = tracker_raw.get("api_token", tracker_raw.get("api_key", "$JIRA_API_TOKEN" if tracker_kind == "jira" else None))
    raw_jira_email = tracker_raw.get("email", "$JIRA_EMAIL" if tracker_kind == "jira" else None)
    tracker = TrackerConfig(
        kind=tracker_kind,
        endpoint=endpoint,
        base_url=_optional_str(tracker_raw.get("base_url") or (tracker_raw.get("endpoint") if tracker_kind == "jira" else None)),
        api_key=_resolve_scalar_env(raw_api_key, env_map),
        email=_resolve_scalar_env(raw_jira_email, env_map),
        api_token=_resolve_scalar_env(raw_jira_token, env_map),
        project_slug=_optional_str(tracker_raw.get("project_slug")),
        project_key=_optional_str(tracker_raw.get("project_key")),
        jql=_optional_str(tracker_raw.get("jql")),
        required_labels=[str(label).strip().lower() for label in _list(tracker_raw.get("required_labels"), [])],
        active_states=[str(state) for state in _list(tracker_raw.get("active_states"), ["Todo", "In Progress"])],
        terminal_states=[
            str(state)
            for state in _list(
                tracker_raw.get("terminal_states"),
                ["Closed", "Cancelled", "Canceled", "Duplicate", "Done"],
            )
        ],
    )

    polling_raw = _section(source, "polling")
    polling = PollingConfig(interval_ms=_int(polling_raw.get("interval_ms"), 30000, field_name="polling.interval_ms"))
    if polling.interval_ms <= 0:
        raise ConfigError("invalid_config", "polling.interval_ms must be positive")

    workspace_raw = _section(source, "workspace")
    raw_root = workspace_raw.get("root", str(Path(tempfile.gettempdir()) / "symphony_workspaces"))
    workspace = WorkspaceConfig(root=_resolve_path(raw_root, base_dir, env_map))

    hooks_raw = _section(source, "hooks")
    hooks = HooksConfig(
        after_create=_optional_str(hooks_raw.get("after_create")),
        before_run=_optional_str(hooks_raw.get("before_run")),
        after_run=_optional_str(hooks_raw.get("after_run")),
        before_remove=_optional_str(hooks_raw.get("before_remove")),
        timeout_ms=_int(hooks_raw.get("timeout_ms"), 60000, field_name="hooks.timeout_ms"),
    )
    if hooks.timeout_ms <= 0:
        raise ConfigError("invalid_config", "hooks.timeout_ms must be positive")

    agent_raw = _section(source, "agent")
    agent = AgentConfig(
        max_concurrent_agents=_int(agent_raw.get("max_concurrent_agents"), 10, field_name="agent.max_concurrent_agents"),
        max_turns=_int(agent_raw.get("max_turns"), 20, field_name="agent.max_turns"),
        max_retry_backoff_ms=_int(
            agent_raw.get("max_retry_backoff_ms"),
            300000,
            field_name="agent.max_retry_backoff_ms",
        ),
        max_concurrent_agents_by_state=_state_limit_map(agent_raw.get("max_concurrent_agents_by_state")),
    )
    if agent.max_concurrent_agents <= 0:
        raise ConfigError("invalid_config", "agent.max_concurrent_agents must be positive")
    if agent.max_turns <= 0:
        raise ConfigError("invalid_config", "agent.max_turns must be positive")
    if agent.max_retry_backoff_ms <= 0:
        raise ConfigError("invalid_config", "agent.max_retry_backoff_ms must be positive")

    codex_raw = _section(source, "codex")
    codex = CodexConfig(
        command=_optional_str(codex_raw.get("command")) or "codex app-server",
        approval_policy=codex_raw.get("approval_policy"),
        thread_sandbox=codex_raw.get("thread_sandbox"),
        turn_sandbox_policy=codex_raw.get("turn_sandbox_policy"),
        turn_timeout_ms=_int(codex_raw.get("turn_timeout_ms"), 3600000, field_name="codex.turn_timeout_ms"),
        read_timeout_ms=_int(codex_raw.get("read_timeout_ms"), 5000, field_name="codex.read_timeout_ms"),
        stall_timeout_ms=_int(codex_raw.get("stall_timeout_ms"), 300000, field_name="codex.stall_timeout_ms"),
    )
    if not codex.command.strip():
        raise ConfigError("invalid_config", "codex.command must be non-empty")
    if codex.turn_timeout_ms <= 0 or codex.read_timeout_ms <= 0:
        raise ConfigError("invalid_config", "codex turn/read timeouts must be positive")

    server_raw = _section(source, "server")
    raw_server_port = cli_port if cli_port is not None else server_raw.get("port")
    server = ServerConfig(port=None if raw_server_port is None else _int(raw_server_port, 0, field_name="server.port"))
    if server.port is not None and server.port < 0:
        raise ConfigError("invalid_config", "server.port must be >= 0")

    return ServiceConfig(
        workflow_path=workflow.path,
        tracker=tracker,
        polling=polling,
        workspace=workspace,
        hooks=hooks,
        agent=agent,
        codex=codex,
        server=server,
    )


def validate_dispatch_config(config: ServiceConfig) -> None:
    if config.tracker.kind == "linear":
        if not config.tracker.api_key:
            raise ConfigError("missing_tracker_api_key", "tracker.api_key is missing after environment resolution")
        if not config.tracker.project_slug:
            raise ConfigError("missing_tracker_project_slug", "tracker.project_slug is required for Linear")
    elif config.tracker.kind == "jira":
        if _jira_needs_remote_access(config.tracker):
            if not config.tracker.base_url:
                raise ConfigError("missing_tracker_base_url", "tracker.base_url is required for Jira")
            if not config.tracker.email:
                raise ConfigError("missing_tracker_email", "tracker.email is required for Jira basic auth")
            if not config.tracker.api_token:
                raise ConfigError("missing_tracker_api_token", "tracker.api_token is required for Jira")
            if not config.tracker.project_key and not config.tracker.jql:
                raise ConfigError("missing_tracker_project_key", "tracker.project_key or tracker.jql is required for Jira")
    else:
        code = "unsupported_tracker_kind" if config.tracker.kind else "missing_tracker_kind"
        raise ConfigError(code, "tracker.kind must be present and must be 'linear' or 'jira'")
    if not config.codex.command.strip():
        raise ConfigError("missing_codex_command", "codex.command is required")


def _jira_needs_remote_access(tracker: TrackerConfig) -> bool:
    return bool(tracker.jql or tracker.active_states or tracker.terminal_states)


def _section(source: Mapping[str, Any], key: str) -> dict[str, Any]:
    value = source.get(key, {})
    return value if isinstance(value, dict) else {}


def _optional_str(value: Any) -> str | None:
    if value is None:
        return None
    return str(value)


def _list(value: Any, default: list[Any]) -> list[Any]:
    if value is None:
        return list(default)
    if isinstance(value, list):
        return value
    return list(default)


def _int(value: Any, default: int, *, field_name: str) -> int:
    if value is None:
        return default
    if isinstance(value, bool):
        raise ConfigError("invalid_config", f"{field_name} must be an integer")
    try:
        return int(value)
    except (TypeError, ValueError) as exc:
        raise ConfigError("invalid_config", f"{field_name} must be an integer") from exc


def _resolve_scalar_env(value: Any, env: Mapping[str, str]) -> str | None:
    if value is None:
        return None
    text = str(value)
    match = re.fullmatch(r"\$([A-Za-z_][A-Za-z0-9_]*)", text)
    if match:
        resolved = env.get(match.group(1), "")
        return resolved or None
    return text


def _resolve_path(value: Any, base_dir: Path, env: Mapping[str, str]) -> Path:
    text = str(value)

    def replace(match: re.Match[str]) -> str:
        return env.get(match.group(1), "")

    expanded = ENV_TOKEN_RE.sub(replace, text)
    expanded = os.path.expanduser(expanded)
    path = Path(expanded)
    if not path.is_absolute():
        path = base_dir / path
    return path.resolve()


def _state_limit_map(value: Any) -> dict[str, int]:
    if not isinstance(value, dict):
        return {}
    limits: dict[str, int] = {}
    for key, raw_limit in value.items():
        try:
            limit = int(raw_limit)
        except (TypeError, ValueError):
            continue
        if limit > 0:
            limits[normalize_state(str(key))] = limit
    return limits
