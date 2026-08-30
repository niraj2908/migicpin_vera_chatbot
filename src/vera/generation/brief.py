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
    # True for the proactive /v1/tick send that opens a conversation (build_brief's default,
    # since try_reserve() guarantees conversation_id is new whenever build_brief is called);
    # for_reply() always sets this False, since by construction a /v1/reply send is never the
    # first message in its conversation. Lets both composers stop re-introducing the sending
    # merchant on turn 2+ -- challenge-brief.md SS11 names this exact anti-pattern
    # ("Re-introducing yourself after the first message") as one the judge penalizes.
    is_first_message: bool = True


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
    return replace(original, reply_intent=reply_intent, is_first_message=False)
