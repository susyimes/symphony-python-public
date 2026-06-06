from __future__ import annotations

import pytest

from symphony.config import TrackerConfig
from symphony.errors import TrackerError
from symphony.linear import ISSUE_STATES_BY_ID_QUERY, CANDIDATE_QUERY, LinearClient, normalize_issue


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

    async def post(self, endpoint: str, *, headers: dict, json: dict, timeout: float) -> FakeResponse:
        self.posts.append({"endpoint": endpoint, "headers": headers, "json": json, "timeout": timeout})
        return FakeResponse(self.pages.pop(0))


def test_normalize_issue_labels_blockers_and_timestamps() -> None:
    issue = normalize_issue(
        {
            "id": "id1",
            "identifier": "ABC-1",
            "title": "Title",
            "description": None,
            "priority": "not-int",
            "state": {"name": "Todo"},
            "labels": {"nodes": [{"name": " Codex "}, {"name": "Backend"}]},
            "inverseRelations": {
                "nodes": [
                    {"type": "blocks", "issue": {"id": "b1", "identifier": "ABC-0", "state": {"name": "In Progress"}}},
                    {"type": "relates", "issue": {"id": "x"}},
                ]
            },
            "createdAt": "2026-01-01T00:00:00Z",
        }
    )

    assert issue.labels == ["codex", "backend"]
    assert issue.priority is None
    assert issue.blocked_by[0].identifier == "ABC-0"
    assert issue.created_at is not None


@pytest.mark.asyncio
async def test_candidate_fetch_paginates_and_uses_project_slug_filter() -> None:
    pages = [
        {
            "data": {
                "issues": {
                    "nodes": [{"id": "1", "identifier": "A-1", "title": "one", "state": {"name": "Todo"}}],
                    "pageInfo": {"hasNextPage": True, "endCursor": "cursor-1"},
                }
            }
        },
        {
            "data": {
                "issues": {
                    "nodes": [{"id": "2", "identifier": "A-2", "title": "two", "state": {"name": "In Progress"}}],
                    "pageInfo": {"hasNextPage": False, "endCursor": None},
                }
            }
        },
    ]
    fake = FakeHttpClient(pages)
    client = LinearClient(TrackerConfig(kind="linear", api_key="key", project_slug="proj"), client=fake)  # type: ignore[arg-type]

    issues = await client.fetch_candidate_issues()

    assert [issue.identifier for issue in issues] == ["A-1", "A-2"]
    assert len(fake.posts) == 2
    assert "slugId" in fake.posts[0]["json"]["query"]
    assert fake.posts[0]["json"]["variables"]["projectSlug"] == "proj"
    assert fake.posts[1]["json"]["variables"]["after"] == "cursor-1"


@pytest.mark.asyncio
async def test_empty_fetch_by_states_skips_api_call() -> None:
    fake = FakeHttpClient([])
    client = LinearClient(TrackerConfig(kind="linear", api_key="key", project_slug="proj"), client=fake)  # type: ignore[arg-type]

    assert await client.fetch_issues_by_states([]) == []
    assert fake.posts == []


def test_issue_state_query_uses_graphql_id_list_type() -> None:
    assert "$ids: [ID!]!" in ISSUE_STATES_BY_ID_QUERY
    assert "slugId" in CANDIDATE_QUERY


@pytest.mark.asyncio
async def test_graphql_errors_are_mapped() -> None:
    fake = FakeHttpClient([{"errors": [{"message": "bad"}]}])
    client = LinearClient(TrackerConfig(kind="linear", api_key="key", project_slug="proj"), client=fake)  # type: ignore[arg-type]

    with pytest.raises(TrackerError) as exc:
        await client.fetch_candidate_issues()
    assert exc.value.code == "linear_graphql_errors"
