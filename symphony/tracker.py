from __future__ import annotations

from typing import Protocol

from .models import Issue


class IssueTracker(Protocol):
    async def fetch_candidate_issues(self) -> list[Issue]:
        ...

    async def fetch_issues_by_states(self, state_names: list[str]) -> list[Issue]:
        ...

    async def fetch_issue_states_by_ids(self, issue_ids: list[str]) -> list[Issue]:
        ...
