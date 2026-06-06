from __future__ import annotations

import json
from typing import Any

import httpx

from .config import TrackerConfig
from .errors import TrackerError
from .models import BlockerRef, Issue, parse_timestamp

PAGE_SIZE = 50
NETWORK_TIMEOUT_SECONDS = 30.0


CANDIDATE_QUERY = """
query SymphonyCandidateIssues($projectSlug: String!, $activeStates: [String!], $after: String) {
  issues(
    first: 50
    after: $after
    filter: {
      project: { slugId: { eq: $projectSlug } }
      state: { name: { in: $activeStates } }
    }
  ) {
    nodes {
      id
      identifier
      title
      description
      priority
      branchName
      url
      createdAt
      updatedAt
      state { name }
      labels { nodes { name } }
      inverseRelations { nodes { type issue { id identifier state { name } } relatedIssue { id identifier state { name } } } }
    }
    pageInfo { hasNextPage endCursor }
  }
}
""".strip()


STATES_QUERY = """
query SymphonyIssuesByStates($projectSlug: String!, $stateNames: [String!], $after: String) {
  issues(
    first: 50
    after: $after
    filter: {
      project: { slugId: { eq: $projectSlug } }
      state: { name: { in: $stateNames } }
    }
  ) {
    nodes {
      id
      identifier
      title
      description
      priority
      branchName
      url
      createdAt
      updatedAt
      state { name }
      labels { nodes { name } }
      inverseRelations { nodes { type issue { id identifier state { name } } relatedIssue { id identifier state { name } } } }
    }
    pageInfo { hasNextPage endCursor }
  }
}
""".strip()


ISSUE_STATES_BY_ID_QUERY = """
query SymphonyIssueStatesByIds($ids: [ID!]!) {
  issues(first: 50, filter: { id: { in: $ids } }) {
    nodes {
      id
      identifier
      title
      description
      priority
      branchName
      url
      createdAt
      updatedAt
      state { name }
      labels { nodes { name } }
      inverseRelations { nodes { type issue { id identifier state { name } } relatedIssue { id identifier state { name } } } }
    }
    pageInfo { hasNextPage endCursor }
  }
}
""".strip()


class LinearClient:
    def __init__(self, config: TrackerConfig, *, client: httpx.AsyncClient | None = None) -> None:
        self.config = config
        self._client = client
        self._owns_client = client is None

    async def aclose(self) -> None:
        if self._owns_client and self._client is not None:
            await self._client.aclose()

    async def fetch_candidate_issues(self) -> list[Issue]:
        return await self._fetch_paginated(
            CANDIDATE_QUERY,
            {"projectSlug": self.config.project_slug, "activeStates": self.config.active_states},
        )

    async def fetch_issues_by_states(self, state_names: list[str]) -> list[Issue]:
        if not state_names:
            return []
        return await self._fetch_paginated(
            STATES_QUERY,
            {"projectSlug": self.config.project_slug, "stateNames": state_names},
        )

    async def fetch_issue_states_by_ids(self, issue_ids: list[str]) -> list[Issue]:
        if not issue_ids:
            return []
        data = await self._post_graphql(ISSUE_STATES_BY_ID_QUERY, {"ids": issue_ids})
        nodes = data.get("issues", {}).get("nodes")
        if not isinstance(nodes, list):
            raise TrackerError("linear_unknown_payload", "Linear issue state response did not include issue nodes")
        return [normalize_issue(node) for node in nodes]

    async def execute_raw_graphql(self, query: str, variables: dict[str, Any] | None = None) -> dict[str, Any]:
        return await self._post_graphql(query, variables or {})

    async def _fetch_paginated(self, query: str, variables: dict[str, Any]) -> list[Issue]:
        after: str | None = None
        issues: list[Issue] = []
        while True:
            page_variables = dict(variables)
            page_variables["after"] = after
            data = await self._post_graphql(query, page_variables)
            connection = data.get("issues")
            if not isinstance(connection, dict):
                raise TrackerError("linear_unknown_payload", "Linear response did not include an issues connection")
            nodes = connection.get("nodes")
            page_info = connection.get("pageInfo")
            if not isinstance(nodes, list) or not isinstance(page_info, dict):
                raise TrackerError("linear_unknown_payload", "Linear response had malformed pagination payload")
            issues.extend(normalize_issue(node) for node in nodes)
            has_next = bool(page_info.get("hasNextPage"))
            end_cursor = page_info.get("endCursor")
            if not has_next:
                return issues
            if not end_cursor:
                raise TrackerError("linear_missing_end_cursor", "Linear pagination indicated next page without endCursor")
            after = str(end_cursor)

    async def _post_graphql(self, query: str, variables: dict[str, Any]) -> dict[str, Any]:
        if not self.config.api_key:
            raise TrackerError("missing_tracker_api_key", "Linear API key is missing")
        client = self._client or httpx.AsyncClient(timeout=NETWORK_TIMEOUT_SECONDS)
        if self._client is None:
            self._client = client
        try:
            response = await client.post(
                self.config.endpoint,
                headers={"Authorization": self.config.api_key, "Content-Type": "application/json"},
                json={"query": query, "variables": variables},
                timeout=NETWORK_TIMEOUT_SECONDS,
            )
        except httpx.RequestError as exc:
            raise TrackerError("linear_api_request", str(exc)) from exc
        if response.status_code != 200:
            raise TrackerError("linear_api_status", f"Linear returned HTTP {response.status_code}")
        try:
            payload = response.json()
        except json.JSONDecodeError as exc:
            raise TrackerError("linear_unknown_payload", "Linear response was not JSON") from exc
        if not isinstance(payload, dict):
            raise TrackerError("linear_unknown_payload", "Linear response was not a JSON object")
        if payload.get("errors"):
            raise TrackerError("linear_graphql_errors", "Linear GraphQL returned errors", detail=payload.get("errors"))
        data = payload.get("data")
        if not isinstance(data, dict):
            raise TrackerError("linear_unknown_payload", "Linear response did not contain data")
        return data


def normalize_issue(node: dict[str, Any]) -> Issue:
    labels = []
    for label_node in _nodes(node.get("labels")):
        name = str(label_node.get("name", "")).strip().lower()
        if name:
            labels.append(name)

    blocked_by: list[BlockerRef] = []
    for relation in _nodes(node.get("inverseRelations")) + _nodes(node.get("relations")):
        if str(relation.get("type", "")).lower() != "blocks":
            continue
        blocker = relation.get("issue") or relation.get("relatedIssue") or {}
        if isinstance(blocker, dict):
            blocked_by.append(
                BlockerRef(
                    id=_maybe_str(blocker.get("id")),
                    identifier=_maybe_str(blocker.get("identifier")),
                    state=_state_name(blocker),
                )
            )

    priority = node.get("priority")
    if isinstance(priority, bool) or not isinstance(priority, int):
        priority = None

    return Issue(
        id=str(node.get("id") or ""),
        identifier=str(node.get("identifier") or ""),
        title=str(node.get("title") or ""),
        description=_maybe_str(node.get("description")),
        priority=priority,
        state=_state_name(node) or "",
        branch_name=_maybe_str(node.get("branchName") or node.get("branch_name")),
        url=_maybe_str(node.get("url")),
        labels=labels,
        blocked_by=blocked_by,
        created_at=parse_timestamp(node.get("createdAt") or node.get("created_at")),
        updated_at=parse_timestamp(node.get("updatedAt") or node.get("updated_at")),
    )


def _nodes(connection: Any) -> list[dict[str, Any]]:
    if isinstance(connection, dict) and isinstance(connection.get("nodes"), list):
        return [node for node in connection["nodes"] if isinstance(node, dict)]
    if isinstance(connection, list):
        return [node for node in connection if isinstance(node, dict)]
    return []


def _state_name(node: dict[str, Any]) -> str | None:
    state = node.get("state")
    if isinstance(state, dict):
        return _maybe_str(state.get("name"))
    return _maybe_str(state)


def _maybe_str(value: Any) -> str | None:
    if value is None:
        return None
    return str(value)
