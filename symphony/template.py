from __future__ import annotations

from jinja2 import Environment, StrictUndefined, TemplateError, TemplateSyntaxError, UndefinedError

from .errors import TemplateRenderError
from .models import Issue

DEFAULT_PROMPT = "You are working on an issue from Linear."


def render_prompt(template_text: str, issue: Issue, attempt: int | None) -> str:
    text = template_text.strip() or DEFAULT_PROMPT
    env = Environment(undefined=StrictUndefined, autoescape=False)
    try:
        template = env.from_string(text)
    except TemplateSyntaxError as exc:
        raise TemplateRenderError("template_parse_error", str(exc)) from exc

    try:
        return template.render(issue=issue.to_template_dict(), attempt=attempt).strip()
    except UndefinedError as exc:
        raise TemplateRenderError("template_render_error", str(exc)) from exc
    except TemplateError as exc:
        raise TemplateRenderError("template_render_error", str(exc)) from exc
