from dataclasses import dataclass, field
from datetime import datetime

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


def _parse_iso_datetime(value: str) -> datetime | None:
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def _is_stale(trigger: TriggerContext, now: datetime | None) -> bool:
    """Missing `now` or a missing/malformed `trigger.expires_at` means staleness genuinely cannot
    be determined -- this returns False (not stale) in both cases, preserving the existing
    (pre-fix) behavior for data the contract simply didn't supply, rather than inventing a new
    policy for it. `now` is never sourced from wall-clock time here — the caller (api/app.py's
    tick handler) is the only place that resolves it, from the request's own `TickRequest.now`,
    exactly mirroring how `already_suppressed` is resolved by the caller before this function is
    ever called.

    A timezone-aware-vs-naive mismatch is different in kind: both values ARE present and parsed,
    just not safely comparable (Python raises TypeError on that comparison directly). Every real
    timestamp documented anywhere in the challenge package -- now, expires_at, received_at,
    delivered_at, stored_at, submitted_at, with zero exceptions -- uses a "Z"-suffixed (aware)
    format, and every datetime this codebase itself produces is likewise always aware; a naive
    value on either side is off-contract input, not a normal, unremarkable case. Treated as
    stale (fail-closed) here, matching the one convention applied everywhere else in this
    decision layer: ambiguous or insufficient evidence blocks an action, it never waves it
    through -- this was the sole exception to that convention, found on review, not a case the
    contract's own "restraint is rewarded, spam is penalized" philosophy argues the other way on."""
    if now is None or trigger.expires_at is None:
        return False
    expires = _parse_iso_datetime(trigger.expires_at)
    if expires is None:
        return False
    if (now.tzinfo is None) != (expires.tzinfo is None):
        return True
    return now > expires


def decide(
    merchant: MerchantContext,
    trigger: TriggerContext,
    customer: CustomerContext | None = None,
    *,
    already_suppressed: bool = False,
    category: CategoryContext | None = None,
    now: datetime | None = None,
) -> Decision:
    """Pure and deterministic: no state lookups happen here. Callers resolve
    `already_suppressed` from the SuppressionStore before calling. `category` is optional (and
    unused by opportunity types that don't need it, e.g. festival_upcoming) so existing callers
    that only ever handled one category-agnostic trigger kind don't need to change. `now` is the
    tick's own evaluation time (from `TickRequest.now`, threaded through by the caller) — used
    only to compare against `trigger.expires_at`; omitting it (the default) reproduces the exact
    pre-existing behavior, since no caller before this fix ever supplied it."""
    suppression_key = _suppression_key(merchant, trigger)
    send_as = _send_as(trigger)

    if _is_stale(trigger, now):
        return Decision(
            send=False,
            dominant_signal="stale_trigger",
            action_type="none",
            cta="none",
            send_as=send_as,
            facts_allowed=[],
            suppression_key=suppression_key,
            reason="This trigger's expires_at has already passed as of the current tick's `now` — treating it as stale rather than acting on outdated context.",
            evidence=["trigger.expires_at", "tick.now"],
            confidence=1.0,
        )

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
