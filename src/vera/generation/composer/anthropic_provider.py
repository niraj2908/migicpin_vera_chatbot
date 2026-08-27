import json
import os
import time

from vera.generation.brief import CompositionBrief
from vera.generation.composer.shared import SYSTEM_PROMPT, build_provider_payload, extract_message

DEFAULT_MODEL = "claude-sonnet-5"
DEFAULT_TIMEOUT_SECONDS = 8.0


class AnthropicComposer:
    """Real Claude-backed composer. `last_latency_ms` / `last_usage` are populated after each
    call — for observability/eval tooling, not the decision path, which never reads them."""

    def __init__(self, model: str | None = None, timeout_seconds: float | None = None) -> None:
        if not os.environ.get("ANTHROPIC_API_KEY"):
            raise ValueError("ANTHROPIC_API_KEY is not set")

        from anthropic import Anthropic

        self._client = Anthropic()
        self.model = model or os.environ.get("ANTHROPIC_MODEL", DEFAULT_MODEL)
        self._timeout_seconds = timeout_seconds or float(
            os.environ.get("ANTHROPIC_TIMEOUT_SECONDS", DEFAULT_TIMEOUT_SECONDS)
        )
        self.last_latency_ms: float | None = None
        self.last_usage: dict[str, int] | None = None

    def compose(self, brief: CompositionBrief) -> str:
        payload = build_provider_payload(brief)

        start = time.monotonic()
        response = self._client.messages.create(
            model=self.model,
            max_tokens=300,
            timeout=self._timeout_seconds,
            # temperature isn't in this SDK build's typed create() overloads; extra_body passes
            # it straight through to the API request. The contract requires determinism
            # ("set temperature=0 if using LLMs") given the same inputs.
            extra_body={"temperature": 0},
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": json.dumps(payload)}],
        )
        self.last_latency_ms = (time.monotonic() - start) * 1000
        self.last_usage = {
            "input_tokens": response.usage.input_tokens,
            "output_tokens": response.usage.output_tokens,
        }

        block = response.content[0]
        if block.type != "text":
            raise ValueError(f"expected a text block from the model, got {block.type}")
        return extract_message(block.text)
