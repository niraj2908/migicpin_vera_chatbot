from dataclasses import dataclass, field

from vera.decision.opportunity import Opportunity, generate_opportunities
from vera.domain.context import CategoryContext, CustomerContext, MerchantContext, TriggerContext

SEND_THRESHOLD = 0.5


@dataclass
class Decision:
    send: bool
    dominant_signal: str
    action_type: str
    cta: str
    send_as: str
    facts_allowed: list[str]
    suppression_key: str
    reason: str
    evidence: list[str] = field(default_factory=list)
    confidence: float = 0.0


def _send_as(trigger: TriggerContext) -> str:
    return "merchant_on_behalf" if trigger.customer_id else "vera"


def _suppression_key(merchant: MerchantContext, trigger: TriggerContext) -> str:
    return trigger.suppression_key or f"{merchant.merchant_id}:{trigger.kind}:{trigger.id}"


def decide(
    merchant: MerchantContext,
    trigger: TriggerContext,
    customer: CustomerContext | None = None,
    *,
    already_suppressed: bool = False,
    category: CategoryContext | None = None,
) -> Decision:
    """Pure and deterministic: no state lookups happen here. Callers resolve
    `already_suppressed` from the SuppressionStore before calling. `category` is optional (and
    unused by opportunity types that don't need it, e.g. festival_upcoming) so existing callers
    that only ever handled one category-agnostic trigger kind don't need to change."""
    suppression_key = _suppression_key(merchant, trigger)
    send_as = _send_as(trigger)

    if already_suppressed:
        return Decision(
            send=False,
            dominant_signal="suppressed",
            action_type="none",
            cta="none",
            send_as=send_as,
            facts_allowed=[],
            suppression_key=suppression_key,
            reason="This trigger's suppression_key has already been acted on for this merchant.",
            evidence=["suppression_key"],
            confidence=1.0,
        )

    opportunities = generate_opportunities(merchant, trigger, customer, category)
    best: Opportunity = max(opportunities, key=lambda o: o.score)

    if best.score < SEND_THRESHOLD:
        return Decision(
            send=False,
            dominant_signal=best.name,
            action_type="none",
            cta="none",
            send_as=send_as,
            facts_allowed=[],
            suppression_key=suppression_key,
            reason=best.reason,
            evidence=best.evidence,
            confidence=1.0 - best.score,
        )

    return Decision(
        send=True,
        dominant_signal=best.name,
        action_type=best.action_type,
        cta=best.cta,
        send_as=send_as,
        facts_allowed=best.facts,
        suppression_key=suppression_key,
        reason=best.reason,
        evidence=best.evidence,
        confidence=best.score,
    )
