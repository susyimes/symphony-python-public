from __future__ import annotations

import logging
from typing import Any


SECRET_HINTS = ("api_key", "token", "secret", "password", "authorization")


def configure_logging(level: str = "INFO") -> None:
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )


def safe_value(key: str, value: Any) -> str:
    lowered = key.lower()
    if any(hint in lowered for hint in SECRET_HINTS):
        return "<redacted>"
    text = str(value)
    if len(text) > 500:
        return text[:497] + "..."
    return text.replace("\n", "\\n")


def log_event(logger: logging.Logger, level: int, event: str, **fields: Any) -> None:
    pairs = [f"event={event}"]
    for key in sorted(fields):
        value = fields[key]
        if value is None:
            continue
        pairs.append(f"{key}={safe_value(key, value)}")
    logger.log(level, " ".join(pairs))
