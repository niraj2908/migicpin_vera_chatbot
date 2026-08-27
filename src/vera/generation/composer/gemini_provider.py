import json
import os
import time

from vera.generation.brief import CompositionBrief
from vera.generation.composer.shared import (
    MESSAGE_RESPONSE_SCHEMA,
    SYSTEM_PROMPT,
    build_provider_payload,
    extract_message,
)

# gemini-2.5-flash is shut down; gemini-3.7-flash is the current stable low-latency model
# (verified against ai.google.dev/gemini-api/docs/models, not assumed).
DEFAULT_MODEL = "gemini-3.7-flash"
# The installed google-genai SDK (2.20.0) rejects a deadline below 10s client/API-side with a
# 400 before generation is attempted (found via real evaluation, not assumed) — 12.0 gives a
# small margin above that floor without changing the /v1/tick-level budget or reliability policy.
DEFAULT_TIMEOUT_SECONDS = 12.0


class GeminiComposer:
    """Real Gemini-backed composer, structured-output enforced via response_schema rather than
    prompt-only JSON instructions. `last_latency_ms` / `last_usage` populated after each call."""

    def __init__(self, model: str | None = None, timeout_seconds: float | None = None) -> None:
        if not os.environ.get("GEMINI_API_KEY"):
            raise ValueError("GEMINI_API_KEY is not set")

        from google import genai
        from google.genai import types

        self._types = types
        self._client = genai.Client(api_key=os.environ["GEMINI_API_KEY"])
        self.model = model or os.environ.get("GEMINI_MODEL", DEFAULT_MODEL)
        self._timeout_seconds = timeout_seconds or float(
            os.environ.get("GEMINI_TIMEOUT_SECONDS", DEFAULT_TIMEOUT_SECONDS)
        )
        self.last_latency_ms: float | None = None
        self.last_usage: dict[str, int] | None = None

    def compose(self, brief: CompositionBrief) -> str:
        payload = build_provider_payload(brief)

        start = time.monotonic()
        response = self._client.models.generate_content(
            model=self.model,
            contents=[json.dumps(payload)],
            config=self._types.GenerateContentConfig(
                system_instruction=SYSTEM_PROMPT,
                temperature=0,
                response_mime_type="application/json",
                response_schema=MESSAGE_RESPONSE_SCHEMA,
                http_options=self._types.HttpOptions(timeout=int(self._timeout_seconds * 1000)),
            ),
        )
        self.last_latency_ms = (time.monotonic() - start) * 1000

        usage = response.usage_metadata
        if usage is not None:
            self.last_usage = {
                "input_tokens": usage.prompt_token_count or 0,
                "output_tokens": usage.candidates_token_count or 0,
            }

        text = response.text
        if not text:
            raise ValueError("Gemini returned an empty response")
        return extract_message(text)
