from __future__ import annotations

from pathlib import Path

import pytest

from symphony.config import build_service_config, validate_dispatch_config
from symphony.errors import TemplateRenderError, WorkflowError
from symphony.models import Issue
from symphony.template import render_prompt
from symphony.workflow import install_bundled_workflows, load_workflow, select_workflow_path, split_front_matter


def test_workflow_front_matter_and_prompt_parse(tmp_path: Path) -> None:
    workflow_path = tmp_path / "WORKFLOW.md"
    workflow_path.write_text(
        """---
tracker:
  kind: linear
  api_key: $LINEAR_API_KEY
  project_slug: demo
workspace:
  root: $WORK_ROOT/child
agent:
  max_concurrent_agents_by_state:
    Todo: 2
    bad: 0
---
Hello {{ issue.identifier }}.
""",
        encoding="utf-8",
    )

    workflow = load_workflow(workflow_path)
    config = build_service_config(workflow, env={"LINEAR_API_KEY": "secret", "WORK_ROOT": str(tmp_path / "root")})

    validate_dispatch_config(config)
    assert workflow.prompt_template == "Hello {{ issue.identifier }}."
    assert config.tracker.api_key == "secret"
    assert config.workspace.root == (tmp_path / "root" / "child").resolve()
    assert config.agent.max_concurrent_agents_by_state == {"todo": 2}


def test_default_workflow_path_uses_cwd(tmp_path: Path) -> None:
    assert select_workflow_path(cwd=tmp_path) == (tmp_path / "WORKFLOW.md").resolve()


def test_named_workflow_uses_bundled_template_when_no_global_file(tmp_path: Path) -> None:
    path = select_workflow_path("dashboard", config_dir=tmp_path / "missing")
    assert path.name == "WORKFLOW.jira-project.dashboard-only.md"
    assert path.exists()


def test_named_workflow_prefers_config_dir(tmp_path: Path) -> None:
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    workflow_path = config_dir / "WORKFLOW.jira-project.dashboard-only.md"
    workflow_path.write_text("prompt", encoding="utf-8")

    assert select_workflow_path("dashboard", config_dir=config_dir) == workflow_path.resolve()


def test_install_bundled_workflows_copies_templates(tmp_path: Path) -> None:
    installed = install_bundled_workflows(target_dir=tmp_path)
    installed_names = {path.name for path in installed}

    assert "WORKFLOW.jira-project.dashboard-only.md" in installed_names
    assert "WORKFLOW.jira-project.jira.md" in installed_names


def test_front_matter_non_map_is_typed_error() -> None:
    with pytest.raises(WorkflowError) as exc:
        split_front_matter("---\n- nope\n---\nbody")
    assert exc.value.code == "workflow_front_matter_not_a_map"


def test_prompt_rendering_is_strict() -> None:
    issue = Issue(id="1", identifier="ABC-1", title="T", state="Todo")
    rendered = render_prompt("{{ issue.identifier }} {{ attempt or 'first' }}", issue, None)
    assert rendered == "ABC-1 first"

    with pytest.raises(TemplateRenderError) as exc:
        render_prompt("{{ issue.missing }}", issue, None)
    assert exc.value.code == "template_render_error"


def test_missing_linear_api_key_fails_validation(tmp_path: Path) -> None:
    workflow_path = tmp_path / "WORKFLOW.md"
    workflow_path.write_text(
        """---
tracker:
  kind: linear
  api_key: $LINEAR_API_KEY
  project_slug: demo
---
body
""",
        encoding="utf-8",
    )
    config = build_service_config(load_workflow(workflow_path), env={})
    with pytest.raises(Exception) as exc:
        validate_dispatch_config(config)
    assert getattr(exc.value, "code") == "missing_tracker_api_key"
