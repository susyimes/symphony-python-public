from __future__ import annotations

from .config import TrackerConfig
from .errors import ConfigError
from .jira import JiraClient
from .linear import LinearClient
from .tracker import IssueTracker


def create_tracker(config: TrackerConfig) -> IssueTracker:
    if config.kind == "linear":
        return LinearClient(config)
    if config.kind == "jira":
        return JiraClient(config)
    code = "unsupported_tracker_kind" if config.kind else "missing_tracker_kind"
    raise ConfigError(code, "tracker.kind must be 'linear' or 'jira'")
