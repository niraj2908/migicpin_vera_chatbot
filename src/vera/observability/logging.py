"""One structured log line per decision-relevant event.

Deliberately stdlib-only (no logging service, no external sink) — this is for debugging judge
runs after the fact, not a monitoring product. Never pass secrets, raw API keys, or full
LLM prompts/responses through `log_event`; pass identifiers and outcomes instead.
"""

import json
import logging
import time
from typing import Any

from vera.security.redact import redact_secrets

logger = logging.getLogger("vera")
if not logger.handlers:
    _handler = logging.StreamHandler()
    _handler.setFormatter(logging.Formatter("%(message)s"))
    logger.addHandler(_handler)
    logger.setLevel(logging.INFO)

_DENYLIST = {"api_key", "authorization", "token", "secret", "password"}


def log_event(event: str, **fields: Any) -> None:
    safe_fields = {
        k: redact_secrets(v) if isinstance(v, str) else v
        for k, v in fields.items()
        if k.lower() not in _DENYLIST
    }
    logger.info(json.dumps({"event": event, "ts": time.time(), **safe_fields}, default=str))
