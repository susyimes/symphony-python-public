from __future__ import annotations

import pytest

from symphony.config import TrackerConfig, build_service_config, validate_dispatch_config
from symphony.jira import JiraClient, adf_to_text, blocked_by_from_links, build_issue_jql, jql_quote, normalize_issue
from symphony.tracker_factory import create_tracker
from symphony.workflow import load_workflow


class FakeResponse:
    def __init__(self, payload: dict, status_code: int = 200) -> None:
        self.payload = payload
        self.status_code = status_code

    def json(self) -> dict:
        return self.payload


class FakeHttpClient:
    def __init__(self, pages: list[dict]) -> None:
        self.pages = pages
        self.posts: list[dict] = []

    async def post(self, url: str, *, headers: dict, auth: tuple[str, str], json: dict, timeout: float) -> FakeResponse:
        self.posts.append({"url": url, "headers": headers, "auth": auth, "json": json, "timeout": timeout})
        return FakeResponse(self.pages.pop(0))


def test_jira_config_validates_and_factory_selects_jira(tmp_path) -> None:
    workflow_path = tmp_path / "WORKFLOW.md"
    workflow_path.write_text(
        """---
tracker:
  kind: jira
  base_url: https://example.atlassian.net
  email: $JIRA_EMAIL
  api_token: $JIRA_API_TOKEN
  project_key: ABC
---
prompt
""",
        encoding="utf-8",
    )

    config = build_service_config(load_workflow(workflow_path), env={"JIRA_EMAIL": "me@example.com", "JIRA_API_TOKEN": "token"})
    validate_dispatch_config(config)

    tracker = create_tracker(config.tracker)
    assert isinstance(tracker, JiraClient)
    assert config.tracker.email == "me@example.com"
    assert config.tracker.api_token == "token"


def test_jira_dashboard_only_config_does_not_require_credentials(tmp_path) -> None:
    workflow_path = tmp_path / "WORKFLOW.md"
    workflow_path.write_text(
        """---
tracker:
  kind: jira
  base_url: https://example.atlassian.net
  email: $JIRA_EMAIL
  api_token: $JIRA_API_TOKEN
  project_key: ABC
  active_states: []
  terminal_states: []
---
prompt
""",
        encoding="utf-8",
    )

    config = build_service_config(load_workflow(workflow_path), env={})
    validate_dispatch_config(config)

    assert config.tracker.email is None
    assert config.tracker.api_token is None


def test_jira_polling_config_still_requires_credentials(tmp_path) -> None:
    workflow_path = tmp_path / "WORKFLOW.md"
    workflow_path.write_text(
        """---
tracker:
  kind: jira
  base_url: https://example.atlassian.net
  email: $JIRA_EMAIL
  api_token: $JIRA_API_TOKEN
  project_key: ABC
  active_states: ["To Do"]
  terminal_states: []
---
prompt
""",
        encoding="utf-8",
    )

    config = build_service_config(load_workflow(workflow_path), env={})
    with pytest.raises(Exception) as exc:
        validate_dispatch_config(config)
    assert getattr(exc.value, "code") == "missing_tracker_email"


def test_jql_builder_quotes_values() -> None:
    assert jql_quote('A "B"') == '"A \\"B\\""'
    assert build_issue_jql(project_key="ABC", states=["To Do", "In Progress"], labels=["codex"]) == (
        'project = "ABC" AND status in ("To Do", "In Progress") AND labels = "codex" '
        "ORDER BY priority ASC, created ASC"
    )


def test_adf_description_is_flattened_for_prompt() -> None:
    assert adf_to_text(
        {
            "type": "doc",
            "content": [
                {"type": "paragraph", "content": [{"type": "text", "text": "First"}]},
                {"type": "paragraph", "content": [{"type": "text", "text": "Second"}]},
            ],
        }
    ) == "First\nSecond"


def test_normalize_jira_issue_maps_common_fields_and_blockers() -> None:
    raw = {
        "id": "10001",
        "key": "ABC-1",
        "fields": {
            "summary": "Fix checkout",
            "description": {"type": "doc", "content": [{"type": "paragraph", "content": [{"type": "text", "text": "Broken"}]}]},
            "status": {"name": "To Do"},
            "priority": {"name": "High", "id": "2"},
            "labels": ["Codex", "backend"],
            "created": "2026-01-01T00:00:00.000+0000",
            "updated": "2026-01-02T00:00:00.000+0000",
            "issuelinks": [
                {
                    "type": {"name": "Blocks", "inward": "is blocked by", "outward": "blocks"},
                    "inwardIssue": {"id": "10000", "key": "ABC-0", "fields": {"status": {"name": "Done"}}},
                }
            ],
        },
    }

    issue = normalize_issue(raw, "https://example.atlassian.net")

    assert issue.id == "ABC-1"
    assert issue.identifier == "ABC-1"
    assert issue.title == "Fix checkout"
    assert issue.description == "Broken"
    assert issue.priority == 2
    assert issue.state == "To Do"
    assert issue.url == "https://example.atlassian.net/browse/ABC-1"
    assert issue.labels == ["codex", "backend"]
    assert issue.blocked_by[0].identifier == "ABC-0"
    assert issue.created_at is not None


def test_blocker_links_ignore_outward_blocks_relation() -> None:
    links = [
        {
            "type": {"name": "Blocks", "inward": "is blocked by", "outward": "blocks"},
            "outwardIssue": {"id": "10002", "key": "ABC-2", "fields": {"status": {"name": "To Do"}}},
        }
    ]
    assert blocked_by_from_links(links) == []


@pytest.mark.asyncio
async def test_jira_search_uses_current_search_jql_endpoint_and_paginates() -> None:
    fake = FakeHttpClient(
        [
            {
                "issues": [{"key": "ABC-1", "fields": {"summary": "One", "status": {"name": "To Do"}}}],
                "isLast": False,
                "nextPageToken": "next",
            },
            {
                "issues": [{"key": "ABC-2", "fields": {"summary": "Two", "status": {"name": "In Progress"}}}],
                "isLast": True,
            },
        ]
    )
    config = TrackerConfig(
        kind="jira",
        base_url="https://example.atlassian.net/",
        email="me@example.com",
        api_token="token",
        project_key="ABC",
        required_labels=["codex"],
        active_states=["To Do", "In Progress"],
    )
    client = JiraClient(config, client=fake)  # type: ignore[arg-type]

    issues = await client.fetch_candidate_issues()

    assert [issue.identifier for issue in issues] == ["ABC-1", "ABC-2"]
    assert fake.posts[0]["url"] == "https://example.atlassian.net/rest/api/3/search/jql"
    assert fake.posts[0]["auth"] == ("me@example.com", "token")
    assert fake.posts[0]["json"]["jql"].startswith('project = "ABC"')
    assert fake.posts[1]["json"]["nextPageToken"] == "next"


@pytest.mark.asyncio
async def test_jira_fetch_candidates_noops_without_active_states_or_jql() -> None:
    fake = FakeHttpClient([])
    config = TrackerConfig(
        kind="jira",
        base_url="https://example.atlassian.net/",
        project_key="ABC",
        required_labels=["codex"],
        active_states=[],
    )
    client = JiraClient(config, client=fake)  # type: ignore[arg-type]

    issues = await client.fetch_candidate_issues()

    assert issues == []
    assert fake.posts == []


@pytest.mark.asyncio
async def test_fetch_issue_states_by_ids_uses_issue_keys() -> None:
    fake = FakeHttpClient([{"issues": [], "isLast": True}])
    config = TrackerConfig(kind="jira", base_url="https://example.atlassian.net", email="me@example.com", api_token="token")
    client = JiraClient(config, client=fake)  # type: ignore[arg-type]

    await client.fetch_issue_states_by_ids(["ABC-1", "ABC-2"])

    assert fake.posts[0]["json"]["jql"] == 'issuekey in ("ABC-1", "ABC-2")'


@pytest.mark.asyncio
async def test_jira_errors_are_mapped() -> None:
    fake = FakeHttpClient([{"errorMessages": ["bad request"]}])
    config = TrackerConfig(kind="jira", base_url="https://example.atlassian.net", email="me@example.com", api_token="token")
    client = JiraClient(config, client=fake)  # type: ignore[arg-type]

    with pytest.raises(Exception) as exc:
        await client.fetch_issue_states_by_ids(["ABC-1"])
    assert getattr(exc.value, "code") == "jira_api_errors"
