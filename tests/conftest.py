from __future__ import annotations

import sys
import asyncio
import inspect
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from symphony.models import Issue  # noqa: E402


def pytest_pyfunc_call(pyfuncitem):
    if inspect.iscoroutinefunction(pyfuncitem.obj):
        kwargs = {name: pyfuncitem.funcargs[name] for name in pyfuncitem._fixtureinfo.argnames}
        asyncio.run(pyfuncitem.obj(**kwargs))
        return True
    return None


def make_issue(
    identifier: str = "ABC-1",
    *,
    issue_id: str = "issue-1",
    title: str = "Test issue",
    state: str = "Todo",
    priority: int | None = 1,
    labels: list[str] | None = None,
) -> Issue:
    return Issue(
        id=issue_id,
        identifier=identifier,
        title=title,
        state=state,
        priority=priority,
        labels=labels or ["codex"],
        created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )


def workflow_text(extra: str = "") -> str:
    return f"""---
tracker:
  kind: linear
  api_key: literal-key
  project_slug: proj
  required_labels: [codex]
workspace:
  root: ./workspaces
agent:
  max_retry_backoff_ms: 15000
codex:
  command: codex app-server
  stall_timeout_ms: 300000
{extra}
---
Work on {{{{ issue.identifier }}}}: {{{{ issue.title }}}} attempt={{{{ attempt or "first" }}}}
"""
