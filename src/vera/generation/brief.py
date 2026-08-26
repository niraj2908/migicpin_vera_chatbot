from dataclasses import dataclass

from vera.decision.compiler import Decision
from vera.domain.models import MerchantState


@dataclass(frozen=True)
class CompositionBrief:
    """Everything the LLM is allowed to know and say. No field it can silently expand."""

    category: str
    merchant_name: str
    facts: list[str]
    cta: str
    identity: str
    urgency: str
    max_chars: int = 280


def build_brief(decision: Decision, merchant: MerchantState) -> CompositionBrief:
    return CompositionBrief(
        category=merchant.category,
        merchant_name=merchant.name,
        facts=decision.facts_allowed,
        cta=decision.cta,
        identity=decision.identity,
        urgency=decision.urgency,
    )
