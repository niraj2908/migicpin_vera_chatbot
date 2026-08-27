from dataclasses import dataclass, field, replace

from vera.decision.compiler import Decision
from vera.domain.context import CategoryContext, CustomerContext, MerchantContext


@dataclass(frozen=True)
class CompositionBrief:
    """Everything the LLM is allowed to know and say. No field it can silently expand."""

    category_slug: str
    voice_tone: str
    vocab_allowed: list[str]
    vocab_taboo: list[str]
    merchant_name: str
    owner_first_name: str | None
    languages: list[str]
    facts: list[str]
    cta: str
    send_as: str
    dominant_signal: str
    customer_name: str | None = None
    customer_language_pref: str | None = None
    max_chars: int = 400
    forbidden_topics: list[str] = field(default_factory=list)
    reply_intent: str | None = None


def build_brief(
    decision: Decision,
    merchant: MerchantContext,
    category: CategoryContext,
    customer: CustomerContext | None = None,
) -> CompositionBrief:
    return CompositionBrief(
        category_slug=category.slug,
        voice_tone=category.voice_tone,
        vocab_allowed=category.vocab_allowed,
        vocab_taboo=category.vocab_taboo,
        merchant_name=merchant.name,
        owner_first_name=merchant.owner_first_name,
        languages=merchant.languages,
        facts=decision.facts_allowed,
        cta=decision.cta,
        send_as=decision.send_as,
        dominant_signal=decision.dominant_signal,
        customer_name=customer.name if customer else None,
        customer_language_pref=customer.language_pref if customer else None,
        forbidden_topics=category.vocab_taboo,
    )


def for_reply(original: CompositionBrief, reply_intent: str) -> CompositionBrief:
    """Rebuild the brief for a /v1/reply send: same grounded facts, new phrasing intent.

    The deterministic reply policy decides `reply_intent`; the LLM only rephrases around it.
    """
    return replace(original, reply_intent=reply_intent)
