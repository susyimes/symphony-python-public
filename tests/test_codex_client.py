from __future__ import annotations

import pytest

import symphony.codex_client as codex_client
from symphony.codex_client import codex_shell_argv, linear_graphql_tool
from symphony.config import TrackerConfig


def test_codex_shell_prefers_bash(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(codex_client.os, "name", "posix")
    monkeypatch.setattr("symphony.codex_client.shutil.which", lambda name: "/bin/bash" if name == "bash" else None)
    assert codex_shell_argv("codex app-server") == ["/bin/bash", "-lc", "codex app-server"]


def test_codex_shell_prefers_codex_cmd_on_windows(monkeypatch: pytest.MonkeyPatch) -> None:
    def which(name: str) -> str | None:
        if name == "codex.cmd":
            return "C:/Users/example/AppData/Roaming/npm/codex.cmd"
        if name == "bash":
            return "C:/Windows/System32/bash.exe"
        return None

    monkeypatch.setattr(codex_client.os, "name", "nt")
    monkeypatch.setattr("symphony.codex_client.shutil.which", which)

    assert codex_shell_argv("codex app-server --stdio") == [
        "C:/Users/example/AppData/Roaming/npm/codex.cmd",
        "app-server",
        "--stdio",
    ]


@pytest.mark.asyncio
async def test_linear_graphql_tool_rejects_missing_auth() -> None:
    result = await linear_graphql_tool(TrackerConfig(kind="linear", api_key=None, project_slug="p"), {"query": "{ viewer { id } }"})
    assert result["success"] is False
    assert "auth" in result["contentItems"][0]["text"].lower()


@pytest.mark.asyncio
async def test_linear_graphql_tool_rejects_multiple_operations() -> None:
    result = await linear_graphql_tool(
        TrackerConfig(kind="linear", api_key="key", project_slug="p"),
        {"query": "query A { viewer { id } } mutation B { x }"},
    )
    assert result["success"] is False
    assert "exactly one operation" in result["contentItems"][0]["text"]
