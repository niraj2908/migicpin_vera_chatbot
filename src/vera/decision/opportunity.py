from dataclasses import dataclass, field

from vera.domain.models import CustomerState, MerchantState, Trigger

# OpportunityScore = trigger_strength + merchant_relevance + category_fit + customer_fit
#                   + timeliness + actionability + engagement_potential - fatigue
# Each subscore is 0-1; the sum is normalized by MAX_RAW_SCORE so callers compare on a 0-1 scale.
MAX_RAW_SCORE = 7.0


@dataclass
class Opportunity:
    name: str
    action_type: str
    score: float
    cta: str
    facts: list[str] = field(default_factory=list)
    evidence: list[str] = field(default_factory=list)
    reason: str = ""


def _clamp(value: float, low: float = 0.0, high: float = 1.0) -> float:
    return max(low, min(high, value))


def _fallback_opportunity() -> Opportunity:
    return Opportunity(
        name="no_strong_opportunity",
        action_type="none",
        score=0.0,
        cta="",
        facts=[],
        evidence=[],
        reason="No trigger produced a sufficiently relevant, evidence-backed opportunity.",
    )


def _festival_opportunity(
    merchant: MerchantState, trigger: Trigger, customer: CustomerState | None
) -> Opportunity | None:
    if trigger.trigger_type != "festival":
        return None

    days = trigger.days_to_event
    trigger_strength = 1.0 if days is not None and 0 <= days <= 5 else 0.4
    merchant_relevance = 1.0 if merchant.offers else 0.5
    category_fit = _clamp(trigger.category_relevance)
    customer_fit = 0.5 if customer is None else (0.7 if customer.consent else 0.0)
    timeliness = _clamp(1 - (days / 7)) if days is not None else 0.3
    actionability = 1.0 if merchant.offers else 0.4
    engagement_potential = 0.6 if merchant.rating is None else _clamp(0.5 + (merchant.rating - 3) / 10)
    fatigue_penalty = _clamp(merchant.campaign_fatigue + (customer.fatigue if customer else 0.0) / 2)

    raw = (
        trigger_strength
        + merchant_relevance
        + category_fit
        + customer_fit
        + timeliness
        + actionability
        + engagement_potential
        - fatigue_penalty
    )
    score = _clamp(raw / MAX_RAW_SCORE)

    facts = [f"{trigger.event} is {days} day(s) away" if days is not None else trigger.event]
    for offer in merchant.offers:
        if offer.discount_pct is not None:
            facts.append(f"{offer.discount_pct:.0f}% off {offer.name}")
        elif offer.final_price is not None:
            facts.append(f"{offer.name} at ₹{offer.final_price:.0f}")

    return Opportunity(
        name=f"festival:{trigger.event}",
        action_type="festival_campaign",
        score=score,
        cta="Book now" if merchant.offers else "Learn more",
        facts=facts,
        evidence=[
            "trigger.event",
            "trigger.days_to_event",
            "merchant.category",
            "merchant.offers",
        ],
        reason=(
            f"{trigger.event} is approaching and merchant category "
            f"'{merchant.category}' is relevant to it."
        ),
    )


_GENERATORS = (_festival_opportunity,)


def generate_opportunities(
    merchant: MerchantState, trigger: Trigger, customer: CustomerState | None
) -> list[Opportunity]:
    opportunities = [_fallback_opportunity()]
    for generator in _GENERATORS:
        opportunity = generator(merchant, trigger, customer)
        if opportunity is not None:
            opportunities.append(opportunity)
    return opportunities
