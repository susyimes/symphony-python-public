from __future__ import annotations

import json
from typing import Any

import httpx

from .config import TrackerConfig
from .errors import TrackerError
from .models import BlockerRef, Issue, parse_timestamp

PAGE_SIZE = 50
NETWORK_TIMEOUT_SECONDS = 30.0
SEARCH_PATH = "/rest/api/3/search/jql"
ISSUE_FIELDS = ["summary", "description", "status", "priority", "labels", "issuelinks", "created", "updated"]

PRIORITY_ORDER = {
    "highest": 1,
    "high": 2,
    "medium": 3,
    "low": 4,
    "lowest": 5,
}


class JiraClient:
    def __init__(self, config: TrackerConfig, *, client: httpx.AsyncClient | None = None) -> None:
        self.config = config
        self._client = client
        self._owns_client = client is None

    async def aclose(self) -> None:
        if self._owns_client and self._client is not None:
            await self._client.aclose()

    async def fetch_candidate_issues(self) -> list[Issue]:
        if not self.config.jql and not self.config.active_states:
            return []
        jql = self.config.jql or build_issue_jql(
            project_key=self.config.project_key,
            states=self.config.active_states,
            labels=self.config.required_labels,
        )
        return await self._search(jql)

    async def fetch_issues_by_states(self, state_names: list[str]) -> list[Issue]:
        if not state_names:
            return []
        jql = build_issue_jql(project_key=self.config.project_key, states=state_names, labels=[])
        return await self._search(jql)

    async def fetch_issue_states_by_ids(self, issue_ids: list[str]) -> list[Issue]:
        if not issue_ids:
            return []
        keys = ", ".join(jql_quote(issue_id) for issue_id in issue_ids)
        return await self._search(f"issuekey in ({keys})")

    async def _search(self, jql: str) -> list[Issue]:
        next_page_token: str | None = None
        issues: list[Issue] = []
        while True:
            body: dict[str, Any] = {
                "jql": jql,
                "maxResults": PAGE_SIZE,
                "fields": ISSUE_FIELDS,
                "fieldsByKeys": True,
            }
            if next_page_token:
                body["nextPageToken"] = next_page_token
            data = await self._post_json(SEARCH_PATH, body)
            nodes = data.get("issues")
            if not isinstance(nodes, list):
                raise TrackerError("jira_unknown_payload", "Jira search response did not include issues")
            issues.extend(normalize_issue(node, self.config.base_url or "") for node in nodes if isinstance(node, dict))
            next_page_token = _maybe_str(data.get("nextPageToken"))
            if data.get("isLast") is True or not next_page_token:
                return issues

    async def _post_json(self, path: str, body: dict[str, Any]) -> dict[str, Any]:
        if not self.config.base_url:
            raise TrackerError("missing_tracker_base_url", "Jira base URL is missing")
        if not self.config.email or not self.config.api_token:
            raise TrackerError("missing_tracker_api_token", "Jira email/api token is missing")
        client = self._client or httpx.AsyncClient(timeout=NETWORK_TIMEOUT_SECONDS)
        if self._client is None:
            self._client = client
        url = self.config.base_url.rstrip("/") + path
        try:
            response = await client.post(
                url,
                headers={"Accept": "application/json", "Content-Type": "application/json"},
                auth=(self.config.email, self.config.api_token),
                json=body,
                timeout=NETWORK_TIMEOUT_SECONDS,
            )
        except httpx.RequestError as exc:
            raise TrackerError("jira_api_request", str(exc)) from exc
        if response.status_code != 200:
            raise TrackerError("jira_api_status", f"Jira returned HTTP {response.status_code}")
        try:
            payload = response.json()
        except json.JSONDecodeError as exc:
            raise TrackerError("jira_unknown_payload", "Jira response was not JSON") from exc
        if not isinstance(payload, dict):
            raise TrackerError("jira_unknown_payload", "Jira response was not a JSON object")
        if payload.get("errorMessages") or payload.get("errors"):
            raise TrackerError("jira_api_errors", "Jira returned errors", detail=payload)
        return payload


def build_issue_jql(*, project_key: str | None, states: list[str], labels: list[str]) -> str:
    clauses: list[str] = []
    if project_key:
        clauses.append(f"project = {jql_quote(project_key)}")
    if states:
        state_values = ", ".join(jql_quote(state) for state in states)
        clauses.append(f"status in ({state_values})")
    for label in labels:
        clauses.append(f"labels = {jql_quote(label)}")
    if not clauses:
        clauses.append("order by created ASC")
        return " ".join(clauses)
    return " AND ".join(clauses) + " ORDER BY priority ASC, created ASC"


def normalize_issue(node: dict[str, Any], base_url: str) -> Issue:
    fields = node.get("fields")
    fields = fields if isinstance(fields, dict) else {}
    key = str(node.get("key") or "")
    description = adf_to_text(fields.get("description"))

    return Issue(
        id=key,
        identifier=key,
        title=str(fields.get("summary") or ""),
        description=description,
        priority=priority_to_int(fields.get("priority")),
        state=_status_name(fields.get("status")) or "",
        branch_name=None,
        url=f"{base_url.rstrip('/')}/browse/{key}" if base_url and key else _maybe_str(node.get("self")),
        labels=[str(label).strip().lower() for label in fields.get("labels", []) if str(label).strip()],
        blocked_by=blocked_by_from_links(fields.get("issuelinks")),
        created_at=parse_timestamp(fields.get("created")),
        updated_at=parse_timestamp(fields.get("updated")),
    )


def blocked_by_from_links(raw_links: Any) -> list[BlockerRef]:
    if not isinstance(raw_links, list):
        return []
    blockers: list[BlockerRef] = []
    for link in raw_links:
        if not isinstance(link, dict):
            continue
        link_type = link.get("type") if isinstance(link.get("type"), dict) else {}
        inward_name = str(link_type.get("inward") or "").lower()
        outward_name = str(link_type.get("outward") or "").lower()
        if isinstance(link.get("inwardIssue"), dict) and _means_blocked_by(inward_name):
            blockers.append(_blocker_from_link_issue(link["inwardIssue"]))
        if isinstance(link.get("outwardIssue"), dict) and _means_blocked_by(outward_name):
            blockers.append(_blocker_from_link_issue(link["outwardIssue"]))
    return blockers


def adf_to_text(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        return value
    if not isinstance(value, dict):
        return str(value)
    parts: list[str] = []
    _collect_adf_text(value, parts)
    text = "".join(parts)
    lines = [line.rstrip() for line in text.splitlines()]
    return "\n".join(line for line in lines if line.strip()).strip() or None


def priority_to_int(value: Any) -> int | None:
    if not isinstance(value, dict):
        return None
    name = str(value.get("name") or "").strip().lower()
    if name in PRIORITY_ORDER:
        return PRIORITY_ORDER[name]
    raw_id = value.get("id")
    try:
        return int(raw_id)
    except (TypeError, ValueError):
        return None


def jql_quote(value: str) -> str:
    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def _collect_adf_text(node: Any, parts: list[str]) -> None:
    if isinstance(node, dict):
        node_type = node.get("type")
        if node_type == "text":
            parts.append(str(node.get("text") or ""))
        if node_type in {"paragraph", "heading", "blockquote"} and parts and not parts[-1].endswith("\n"):
            parts.append("\n")
        for child in node.get("content", []) if isinstance(node.get("content"), list) else []:
            _collect_adf_text(child, parts)
        if node_type in {"paragraph", "heading", "blockquote", "listItem"}:
            parts.append("\n")
    elif isinstance(node, list):
        for child in node:
            _collect_adf_text(child, parts)


def _blocker_from_link_issue(issue: dict[str, Any]) -> BlockerRef:
    fields = issue.get("fields")
    fields = fields if isinstance(fields, dict) else {}
    return BlockerRef(
        id=_maybe_str(issue.get("key") or issue.get("id")),
        identifier=_maybe_str(issue.get("key")),
        state=_status_name(fields.get("status")),
    )


def _means_blocked_by(value: str) -> bool:
    return "blocked by" in value or "depends on" in value or "requires" in value


def _status_name(value: Any) -> str | None:
    if isinstance(value, dict):
        return _maybe_str(value.get("name"))
    return _maybe_str(value)


def _maybe_str(value: Any) -> str | None:
    if value is None:
        return None
    return str(value)
