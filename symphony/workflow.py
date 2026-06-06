from __future__ import annotations

import os
import shutil
from pathlib import Path
from typing import Any

import yaml

from .errors import WorkflowError
from .models import WorkflowDefinition

GLOBAL_CONFIG_ENV = "SYMPHONY_HOME"

WORKFLOW_ALIASES = {
    "dashboard": "WORKFLOW.jira-project.dashboard-only.md",
    "jira-project": "WORKFLOW.jira-project.jira.md",
}


def global_config_dir() -> Path:
    configured = os.environ.get(GLOBAL_CONFIG_ENV)
    if configured:
        return Path(configured).expanduser().resolve()
    return (Path.home() / ".symphony").resolve()


def bundled_workflow_dir() -> Path:
    return Path(__file__).resolve().parent / "workflows"


def workflow_aliases() -> dict[str, str]:
    return dict(WORKFLOW_ALIASES)


def select_workflow_path(
    explicit: str | Path | None = None,
    *,
    cwd: str | Path | None = None,
    config_dir: str | Path | None = None,
) -> Path:
    if explicit:
        text = str(explicit)
        path = Path(text).expanduser()
        if path.exists() or _looks_like_path(text):
            return path.resolve()
        resolved = resolve_named_workflow(text, config_dir=config_dir)
        if resolved is not None:
            return resolved
        return path.resolve()
    base = Path(cwd or Path.cwd())
    return (base / "WORKFLOW.md").resolve()


def resolve_named_workflow(name: str, *, config_dir: str | Path | None = None) -> Path | None:
    filename = WORKFLOW_ALIASES.get(name, name)
    candidates = [
        Path(config_dir).expanduser().resolve() / filename if config_dir is not None else global_config_dir() / filename,
        bundled_workflow_dir() / filename,
    ]
    if not filename.endswith(".md"):
        candidates.extend(
            [
                (Path(config_dir).expanduser().resolve() if config_dir is not None else global_config_dir()) / f"{filename}.md",
                bundled_workflow_dir() / f"{filename}.md",
                (Path(config_dir).expanduser().resolve() if config_dir is not None else global_config_dir()) / f"WORKFLOW.{filename}.md",
                bundled_workflow_dir() / f"WORKFLOW.{filename}.md",
            ]
        )
    for candidate in candidates:
        if candidate.exists():
            return candidate.resolve()
    return None


def install_bundled_workflows(*, target_dir: str | Path | None = None, overwrite: bool = False) -> list[Path]:
    destination = Path(target_dir).expanduser().resolve() if target_dir is not None else global_config_dir()
    destination.mkdir(parents=True, exist_ok=True)
    installed: list[Path] = []
    for filename in sorted(set(WORKFLOW_ALIASES.values())):
        source = bundled_workflow_dir() / filename
        if not source.exists():
            continue
        target = destination / filename
        if target.exists() and not overwrite:
            continue
        shutil.copyfile(source, target)
        installed.append(target)
    return installed


def load_workflow(path: str | Path) -> WorkflowDefinition:
    workflow_path = Path(path).expanduser().resolve()
    try:
        text = workflow_path.read_text(encoding="utf-8")
        stat = workflow_path.stat()
    except FileNotFoundError as exc:
        raise WorkflowError("missing_workflow_file", f"workflow file not found: {workflow_path}") from exc
    except OSError as exc:
        raise WorkflowError("missing_workflow_file", f"workflow file cannot be read: {workflow_path}") from exc

    try:
        config, body = split_front_matter(text)
    except WorkflowError:
        raise
    except yaml.YAMLError as exc:
        raise WorkflowError("workflow_parse_error", f"invalid YAML front matter in {workflow_path}") from exc

    return WorkflowDefinition(
        path=workflow_path,
        config=config,
        prompt_template=body.strip(),
        mtime_ns=stat.st_mtime_ns,
    )


def split_front_matter(text: str) -> tuple[dict[str, Any], str]:
    lines = text.splitlines(keepends=True)
    if not lines or lines[0].strip() != "---":
        return {}, text.strip()

    end_index: int | None = None
    for index, line in enumerate(lines[1:], start=1):
        if line.strip() == "---":
            end_index = index
            break
    if end_index is None:
        raise WorkflowError("workflow_parse_error", "front matter starts with --- but has no closing ---")

    front_matter = "".join(lines[1:end_index])
    body = "".join(lines[end_index + 1 :])
    parsed = yaml.safe_load(front_matter) if front_matter.strip() else {}
    if parsed is None:
        parsed = {}
    if not isinstance(parsed, dict):
        raise WorkflowError("workflow_front_matter_not_a_map", "workflow front matter must decode to a map/object")
    return parsed, body.strip()


def _looks_like_path(value: str) -> bool:
    return any(separator in value for separator in ("/", "\\")) or value.endswith(".md") or value in {".", ".."}
