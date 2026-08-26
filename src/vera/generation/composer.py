import json
import os
from typing import Protocol

from vera.generation.brief import CompositionBrief

SYSTEM_PROMPT = """You write a single short marketing message for a local merchant.

Rules you must never break:
- Use ONLY the facts listed in "facts". Never invent a price, date, offer, or discount not present there.
- The message must match the category's natural tone (a restaurant should not sound like a salon).
- Include a call to action that matches "cta" in meaning.
- Keep it under max_chars characters, no markdown, no placeholders like [name].
- Respond with JSON only: {"message": "..."}
"""


class Composer(Protocol):
    def compose(self, brief: CompositionBrief) -> str: ...


class TemplateComposer:
    """Deterministic, always-grounded composer. Used as the safety fallback and in tests."""

    def compose(self, brief: CompositionBrief) -> str:
        fact_text = "; ".join(brief.facts) if brief.facts else brief.merchant_name
        message = f"{brief.merchant_name}: {fact_text}. {brief.cta}."
        return message[: brief.max_chars]


class AnthropicComposer:
    def __init__(self, model: str = "claude-sonnet-5") -> None:
        from anthropic import Anthropic

        self._client = Anthropic()
        self._model = model

    def compose(self, brief: CompositionBrief) -> str:
        user_payload = {
            "category": brief.category,
            "merchant_name": brief.merchant_name,
            "facts": brief.facts,
            "cta": brief.cta,
            "identity": brief.identity,
            "urgency": brief.urgency,
            "max_chars": brief.max_chars,
        }
        response = self._client.messages.create(
            model=self._model,
            max_tokens=300,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": json.dumps(user_payload)}],
        )
        text = response.content[0].text
        parsed = json.loads(text)
        return str(parsed["message"])


def get_default_composer() -> Composer:
    if os.environ.get("ANTHROPIC_API_KEY"):
        return AnthropicComposer()
    return TemplateComposer()
