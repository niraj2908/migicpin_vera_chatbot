from dataclasses import dataclass, field

from vera.decision.opportunity import Opportunity, generate_opportunities
from vera.domain.models import CustomerState, MerchantState, Trigger

SEND_THRESHOLD = 0.5
FATIGUE_SUPPRESS_THRESHOLD = 0.85


@dataclass
class Decision:
    send: bool
    action_type: str
    cta: str
    identity: str
    urgency: str
    facts_allowed: list[str]
    suppression_key: str
    reason: str
    evidence: list[str] = field(default_factory=list)
    confidence: float = 0.0


def _urgency(trigger: Trigger) -> str:
    if trigger.days_to_event is None:
        return "low"
    if trigger.days_to_event <= 2:
        return "high"
    if trigger.days_to_event <= 5:
        return "medium"
    return "low"


def _suppression_key(merchant: MerchantState, trigger: Trigger) -> str:
    return f"{merchant.merchant_id}:{trigger.trigger_type}:{trigger.event}"


def decide(
    merchant: MerchantState, trigger: Trigger, customer: CustomerState | None = None
) -> Decision:
    opportunities = generate_opportunities(merchant, trigger, customer)
    best: Opportunity = max(opportunities, key=lambda o: o.score)
    suppression_key = _suppression_key(merchant, trigger)

    if merchant.campaign_fatigue >= FATIGUE_SUPPRESS_THRESHOLD:
        return Decision(
            send=False,
            action_type="none",
            cta="",
            identity=merchant.name,
            urgency="low",
            facts_allowed=[],
            suppression_key=suppression_key,
            reason="Merchant campaign fatigue is above the suppression threshold.",
            evidence=["merchant.campaign_fatigue"],
            confidence=1.0,
        )

    if best.score < SEND_THRESHOLD:
        return Decision(
            send=False,
            action_type="none",
            cta="",
            identity=merchant.name,
            urgency="low",
            facts_allowed=[],
            suppression_key=suppression_key,
            reason=best.reason,
            evidence=best.evidence,
            confidence=1.0 - best.score,
        )

    return Decision(
        send=True,
        action_type=best.action_type,
        cta=best.cta,
        identity=merchant.name,
        urgency=_urgency(trigger),
        facts_allowed=best.facts,
        suppression_key=suppression_key,
        reason=best.reason,
        evidence=best.evidence,
        confidence=best.score,
    )
