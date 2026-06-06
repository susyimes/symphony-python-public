from __future__ import annotations

from pathlib import Path

import pytest

from symphony.config import HooksConfig
from symphony.errors import WorkspaceError
from symphony.workspace import WorkspaceManager, sanitize_workspace_key


@pytest.mark.asyncio
async def test_workspace_creation_reuse_and_after_create_hook(tmp_path: Path) -> None:
    hook = "python -c \"from pathlib import Path; Path('stamp.txt').write_text('created')\""
    manager = WorkspaceManager(tmp_path / "root", HooksConfig(after_create=hook, timeout_ms=10000))

    workspace = await manager.create_for_issue("ABC/123:thing")
    assert workspace.workspace_key == "ABC_123_thing"
    assert workspace.created_now is True
    assert (workspace.path / "stamp.txt").read_text() == "created"

    (workspace.path / "stamp.txt").write_text("kept")
    reused = await manager.create_for_issue("ABC/123:thing")
    assert reused.created_now is False
    assert (workspace.path / "stamp.txt").read_text() == "kept"


@pytest.mark.asyncio
async def test_existing_non_directory_workspace_path_fails(tmp_path: Path) -> None:
    root = tmp_path / "root"
    root.mkdir()
    (root / "ABC-1").write_text("not a directory")
    manager = WorkspaceManager(root, HooksConfig())

    with pytest.raises(WorkspaceError) as exc:
        await manager.create_for_issue("ABC-1")
    assert exc.value.code == "workspace_path_not_directory"


@pytest.mark.asyncio
async def test_before_remove_hook_is_best_effort_and_cleanup_stays_in_root(tmp_path: Path) -> None:
    hook = "python -c \"from pathlib import Path; Path('..', 'removed.txt').write_text('yes')\""
    manager = WorkspaceManager(tmp_path / "root", HooksConfig(before_remove=hook, timeout_ms=10000))
    workspace = await manager.create_for_issue("ABC-1")

    await manager.remove_for_issue("ABC-1")

    assert not workspace.path.exists()
    assert (tmp_path / "root" / "removed.txt").read_text() == "yes"


def test_sanitization_and_cwd_validation(tmp_path: Path) -> None:
    assert sanitize_workspace_key("../ABC 1") == ".._ABC_1"
    manager = WorkspaceManager(tmp_path / "root", HooksConfig())
    workspace_path = manager.path_for_identifier("../ABC 1")
    assert workspace_path.parent == (tmp_path / "root").resolve()
    with pytest.raises(WorkspaceError) as exc:
        manager.validate_agent_cwd(tmp_path, workspace_path)
    assert exc.value.code in {"invalid_workspace_cwd", "workspace_outside_root"}
