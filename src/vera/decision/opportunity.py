from dataclasses import dataclass, field
from typing import Any

from vera.domain.context import CategoryContext, CustomerContext, MerchantContext, TriggerContext

# OpportunityScore = (trigger_strength + merchant_relevance + timeliness + actionability
#                     + engagement_potential) / MAX_RAW_SCORE, then scaled by urgency_factor.
MAX_RAW_SCORE = 5.0
TIMELINESS_WINDOW_DAYS = 14

# trigger.urgency (1-5) is documented (engagement-design.md's TriggerContext section) as ranking
# a trigger "against other queued triggers" — a judge/data-declared priority, distinct from the
# payload's own timing. Every festival_upcoming trigger in the base dataset sits at urgency=1
# (the floor), so this factor is a complete no-op there by design: urgency=1 -> 1.0x (unchanged),
# rising to 1.2x at urgency=5. It's a bounded tie-breaker, not a dominant term — deliberately too
# small to single-handedly turn an irrelevant/stale opportunity into a send, and it never runs at
# all for a category mismatch or an already-suppressed trigger, both of which are hard gates
# upstream of any scoring (see opportunity() returning None / compiler.py's suppression check).
_URGENCY_FACTOR_PER_LEVEL = 0.05
_URGENCY_BASELINE = 1

# merchant.signals corroboration: a small, capped, non-gating score nudge when the merchant's own
# upstream-derived signal tags (challenge-brief.md's DerivedSignal list, e.g. "stale_posts",
# "ctr_below_peer") share meaningful vocabulary with the CURRENT trigger's `kind`. Evidence: this
# exact correspondence — same underlying merchant state independently producing both a trigger
# and a matching signal tag — was checked across the full real dataset (25 triggers x their
# merchant's signals): 8 of 25 real (trigger, merchant) pairs show it, spanning 6 different
# trigger kinds and 5 different merchants (e.g. merchant signal "seasonal_dip_apr_may" alongside
# a "seasonal_perf_dip" trigger; "renewal_due_soon:12d" alongside "renewal_due"; "winback_eligible"
# alongside a "winback_eligible" trigger) — a real, repeated pattern, not a coincidence tied to
# any one specific tag string. This is generic token-overlap against whatever `trigger.kind`
# happens to be, never a lookup table of known tag strings, so it applies identically to any
# unseen trigger kind or signal tag sharing this naming convention, and safely contributes
# nothing when it doesn't recognize a match — required, since the contract's own documented
# `signals` examples ("stale_posts", "ctr_below_peer", "dormant") are bare, unsuffixed tags with
# no established colon/value convention to parse for magnitude; only vocabulary, never a value,
# is ever extracted here.
#
# Threshold (>=2 overlapping tokens): a 1-token overlap is too weak to trust — checked directly,
# a merchant signal "perf_spike" (the OPPOSITE of a dip) shares one token ("perf") with a
# "perf_dip" trigger's kind, which a 1-token threshold would incorrectly treat as corroboration
# for a genuinely contradictory signal. Requiring >=2 tokens excludes that false positive while
# still capturing 7 of the 8 real corroborating pairs above (the one exception, "ipl_match_today"
# vs "ipl_eligible_locality", shares only "ipl" — a real but weaker, more tangential pairing,
# correctly excluded at this bar).
#
# Magnitude (+0.03, applied at most once regardless of how many signals match): comparable to,
# but below, one _URGENCY_FACTOR_PER_LEVEL step (0.05) — small enough that it can never
# single-handedly turn an irrelevant or already-gated-out opportunity into a send (every
# generator's own hard gates run first and return None before this is ever applied), only nudge
# an opportunity that already independently qualified.
_SIGNAL_CORROBORATION_BONUS = 0.03
_SIGNAL_TOKEN_OVERLAP_MIN = 2
_DURATION_SUFFIX_UNITS = ("d", "h", "m")


def _signal_tokens(value: str) -> set[str]:
    """Vocabulary only, never a value: strips any colon-suffixed value and duration-shaped
    tokens ("22d", "14h") before splitting on "_", since the contract's own documented `signals`
    examples never commit to a colon/suffix convention — only the tag's own words are compared."""
    base = value.split(":")[0].lower()
    tokens: set[str] = set()
    for tok in base.split("_"):
        if not tok or tok.isdigit():
            continue
        if len(tok) > 1 and tok[-1] in _DURATION_SUFFIX_UNITS and tok[:-1].isdigit():
            continue
        tokens.add(tok)
    return tokens


def _signal_corroboration_bonus(merchant: MerchantContext, trigger: TriggerContext) -> float:
    kind_tokens = _signal_tokens(trigger.kind)
    for signal in merchant.signals:
        if len(_signal_tokens(signal) & kind_tokens) >= _SIGNAL_TOKEN_OVERLAP_MIN:
            return _SIGNAL_CORROBORATION_BONUS
    return 0.0


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
        cta="none",
        reason="No trigger produced a sufficiently relevant, evidence-backed opportunity.",
    )


def _festival_opportunity(
    merchant: MerchantContext,
    trigger: TriggerContext,
    customer: CustomerContext | None,
    category: CategoryContext | None,
) -> Opportunity | None:
    if trigger.kind != "festival_upcoming":
        return None

    payload: dict[str, Any] = trigger.payload
    festival = payload.get("festival")
    days_until_raw = payload.get("days_until")
    days_until: float | None = days_until_raw if isinstance(days_until_raw, (int, float)) else None
    category_relevance = payload.get("category_relevance", [])

    if not festival or merchant.category_slug not in category_relevance:
        return None
    # Hard gate, not a soft floor -- the same discipline renewal_due already applies to its own
    # days_remaining field, reusing that exact TIMELINESS_WINDOW_DAYS constant rather than
    # inventing a new number. Found necessary via adversarial audit: the real seed's own
    # festival_upcoming trigger is 188 days out (the brief's own canonical example is "Diwali in
    # 4 days", challenge-brief.md line 130) -- previously, ANY out-of-window days_until fell back
    # to a fixed, non-decaying 0.3/0.2 "weak but nonzero" pair, and merchant_relevance +
    # actionability + engagement_potential alone already sum to 2.6/5.0 = 0.52 (itself above
    # SEND_THRESHOLD) whenever a real offer exists -- meaning an offer could rescue a send at 15
    # days or 15 years away identically; the score was never actually sensitive to *how far* past
    # the window a festival was. Only a hard gate closes this regardless of offer/urgency.
    if days_until is None or not (0 <= days_until <= TIMELINESS_WINDOW_DAYS):
        return None

    trigger_strength = 1.0  # gated above: only ever reached inside the window
    timeliness = _clamp(1 - (days_until / TIMELINESS_WINDOW_DAYS))

    active_offers = merchant.active_offers
    has_offer = bool(active_offers)
    merchant_relevance = 1.0 if has_offer else 0.5
    actionability = 1.0 if has_offer else 0.3
    engagement_potential = 0.6

    raw = trigger_strength + merchant_relevance + timeliness + actionability + engagement_potential
    urgency_factor = 1.0 + _URGENCY_FACTOR_PER_LEVEL * (trigger.urgency - _URGENCY_BASELINE)
    score = _clamp((raw / MAX_RAW_SCORE) * urgency_factor)

    facts = [f"{festival} is {days_until} day(s) away"]
    for offer in active_offers:
        title = offer.get("title")
        if title:
            facts.append(str(title))

    return Opportunity(
        name=f"festival:{festival}",
        action_type="festival_campaign",
        score=score,
        cta="binary_yes_no" if has_offer else "open_ended",
        facts=facts,
        evidence=[
            "trigger.payload.festival",
            "trigger.payload.days_until",
            "trigger.payload.category_relevance",
            "trigger.urgency",
            "merchant.category_slug",
            "merchant.offers",
        ],
        reason=(
            f"{festival} is approaching and '{merchant.category_slug}' is listed in the "
            f"trigger's relevant categories."
        ),
    )


# Below what magnitude is a dip not worth a reassurance message at all? Chosen from the actual
# seed instance (-30%, clearly worth it) with headroom below it, not an arbitrary number picked
# to make a test pass. A single-digit fluctuation isn't a "dip" worth Vera commenting on.
_MEANINGFUL_DIP_THRESHOLD = -0.10
_STRONG_DIP_THRESHOLD = -0.15


def _seasonal_dip_reframe_opportunity(
    merchant: MerchantContext,
    trigger: TriggerContext,
    customer: CustomerContext | None,  # shared generator signature; unused here, seasonal_perf_dip is merchant-scoped
    category: CategoryContext | None,
) -> Opportunity | None:
    if trigger.kind != "seasonal_perf_dip":
        return None

    payload: dict[str, Any] = trigger.payload
    metric = payload.get("metric")
    delta_pct_raw = payload.get("delta_pct")
    delta_pct: float | None = delta_pct_raw if isinstance(delta_pct_raw, (int, float)) else None
    is_expected_seasonal = payload.get("is_expected_seasonal") is True

    # The entire premise of this trigger kind is "this dip is normal, don't overreact" — if the
    # judge ever sends one without that flag true, there's no grounded reframe story to tell, so
    # we don't fabricate one. Likewise, a dip smaller than the meaningful threshold is nothing to
    # reframe at all — this is a hard gate, not just a low score, matching how a category
    # mismatch hard-gates festival_upcoming rather than merely scoring it low (a low-but-nonzero
    # score here would still be beaten by fixed non-magnitude terms below and send anyway).
    if not is_expected_seasonal or metric is None or delta_pct is None:
        return None
    if delta_pct > _MEANINGFUL_DIP_THRESHOLD:
        return None

    magnitude_signal = 1.0 if delta_pct <= _STRONG_DIP_THRESHOLD else 0.4

    active_offers = merchant.active_offers
    has_offer = bool(active_offers)
    actionability = 1.0 if has_offer else 0.5  # a concrete pivot offer helps, but "draft a
    # retention idea" (Case Study 7's own pattern) is still a real next step without one.

    seasonal_digest = (category.digest_items(kind="seasonal") if category else [])[:1]
    digest_support = 1.0 if seasonal_digest else 0.3

    trigger_relevance = 1.0  # gated above: only ever reached when is_expected_seasonal is true

    raw = trigger_relevance + magnitude_signal + actionability + digest_support
    urgency_factor = 1.0 + _URGENCY_FACTOR_PER_LEVEL * (trigger.urgency - _URGENCY_BASELINE)
    score = _clamp((raw / 4.0) * urgency_factor)

    facts = [f"{metric} down {abs(delta_pct) * 100:.0f}% this week"]
    if seasonal_digest:
        item = seasonal_digest[0]
        title = item.get("title")
        source = item.get("source")
        if title:
            facts.append(f"{title}" + (f" ({source})" if source else ""))
    member_count = merchant.total_active_members
    if member_count is not None:
        facts.append(f"{member_count} active members")
    for offer in active_offers:
        title = offer.get("title")
        if title:
            facts.append(str(title))

    return Opportunity(
        name="seasonal_dip_reframe",
        action_type="seasonal_dip_reframe",
        score=score,
        cta="open_ended",
        facts=facts,
        evidence=[
            "trigger.payload.metric",
            "trigger.payload.delta_pct",
            "trigger.payload.is_expected_seasonal",
            "trigger.urgency",
            "category.digest",
            "merchant.customer_aggregate.total_active_members",
            "merchant.offers",
        ],
        reason=(
            f"{metric} is down {abs(delta_pct) * 100:.0f}% but the trigger marks this as an "
            f"expected seasonal pattern, not a real problem — worth reassuring the merchant "
            f"and redirecting to retention rather than staying silent or suggesting a discount."
        ),
    )


# The specific consent scope value this trigger kind requires, per the real seed customer record
# (c_010_rashmi_for_m007: consent.scope includes "winback_offers") — not an invented category.
_WINBACK_CONSENT_SCOPE = "winback_offers"


def _customer_lapsed_winback_opportunity(
    merchant: MerchantContext,
    trigger: TriggerContext,
    customer: CustomerContext | None,
    category: CategoryContext | None,  # unused: no category digest/vocab lookup needed for this decision
) -> Opportunity | None:
    if trigger.kind != "customer_lapsed_hard":
        return None

    # Customer-scoped and consent-gated: without a pushed CustomerContext we cannot verify
    # consent, and without explicit consent for winback-type outreach we must not contact them
    # at all — both are hard gates, not scoring inputs, mirroring how category mismatch and an
    # un-flagged seasonal dip hard-gate the other two generators rather than merely scoring low.
    if customer is None:
        return None
    if _WINBACK_CONSENT_SCOPE not in customer.consent_scope:
        return None

    # "customer_lapsed_hard" (as opposed to a milder "_soft" sibling kind referenced in the
    # brief) is itself the judge/data pipeline's own classification that this lapse is
    # significant — we don't second-guess that with our own days-since-visit threshold the way
    # seasonal_perf_dip's magnitude is judged ourselves (that trigger kind carries no such
    # pre-classification). Trusting an explicit upstream classification over reinventing our own
    # threshold is the more evidence-disciplined choice here.
    trigger_relevance = 1.0

    active_offers = merchant.active_offers
    has_offer = bool(active_offers)
    merchant_relevance = 1.0 if has_offer else 0.5
    actionability = 1.0 if has_offer else 0.3
    engagement_potential = 0.6  # same constant as festival_upcoming's, for the same reason:
    # a fixed, documented baseline rather than a per-generator invented number.

    raw = trigger_relevance + merchant_relevance + actionability + engagement_potential
    urgency_factor = 1.0 + _URGENCY_FACTOR_PER_LEVEL * (trigger.urgency - _URGENCY_BASELINE)
    score = _clamp((raw / 3.6) * urgency_factor)

    payload: dict[str, Any] = trigger.payload
    days_since = payload.get("days_since_last_visit")
    previous_focus = payload.get("previous_focus")

    facts: list[str] = []
    evidence: list[str] = [
        "trigger.kind",
        "trigger.payload.days_since_last_visit",
        "trigger.payload.previous_focus",
        "trigger.urgency",
        "customer.consent.scope",
        "merchant.offers",
    ]
    # Real field (customer.relationship.visits_total), never read by any generator until now.
    # Fact-only enrichment, no scoring change -- same discipline the peer_stats fix already
    # applied to milestone_reached: a real, grounded number added to composition, never a new
    # scoring input. Omitted at 0 (or absent), matching the "never state a hollow fact" pattern
    # already used for chronic_rx_count/total_active_members elsewhere in this file -- a
    # customer_lapsed_hard trigger with 0 prior visits would be a contradictory data shape
    # (the trigger's own premise is a customer who WAS visiting and stopped), not a real case to
    # design a phrasing for.
    visits_total = customer.visits_total
    if visits_total is not None and visits_total > 0:
        facts.append(f"{visits_total} visit(s) with you before this")
        evidence.append("customer.relationship.visits_total")
    if isinstance(days_since, (int, float)):
        facts.append(f"it has been {int(days_since)} days since your last visit")
    if isinstance(previous_focus, str) and previous_focus:
        facts.append(f"your previous focus was {previous_focus.replace('_', ' ')}")
    for offer in active_offers:
        title = offer.get("title")
        if title:
            facts.append(str(title))

    return Opportunity(
        name="customer_winback",
        action_type="customer_winback",
        score=score,
        cta="binary_yes_no" if has_offer else "open_ended",
        facts=facts,
        evidence=evidence,
        reason=(
            "This customer has an explicit hard-lapse trigger and has consented to winback "
            "outreach — worth a warm, no-pressure check-in rather than staying silent."
        ),
    )


# Per the real seed customer record (c_001_priya_for_m001): consent.scope includes
# "recall_reminders" — the exact scope this trigger kind requires, not an invented category.
_RECALL_CONSENT_SCOPE = "recall_reminders"


def _recall_due_opportunity(
    merchant: MerchantContext,
    trigger: TriggerContext,
    customer: CustomerContext | None,
    category: CategoryContext | None,  # unused: no category digest/vocab lookup needed for this decision
) -> Opportunity | None:
    if trigger.kind != "recall_due":
        return None

    # Same two hard gates as customer_lapsed_winback, same reasoning: customer-scoped and
    # consent-gated, not scoring inputs.
    if customer is None:
        return None
    if _RECALL_CONSENT_SCOPE not in customer.consent_scope:
        return None

    # No days-until-due math is done here, deliberately: "recall_due" (like
    # "customer_lapsed_hard") is itself the judge/data pipeline's classification that this
    # recall window has genuinely opened — the payload gives last_service_date/due_date as raw
    # ISO strings with no pre-computed delta, and re-deriving "how overdue" ourselves would need
    # a `now` input none of our opportunity generators have ever required, for a magnitude
    # signal the trigger's own existence already implies. Using the dates as grounded
    # composition facts (below) is enough; recomputing them isn't.
    payload: dict[str, Any] = trigger.payload
    service_due = payload.get("service_due")
    last_service_date = payload.get("last_service_date")
    due_date = payload.get("due_date")
    available_slots = payload.get("available_slots")
    slot_labels = [
        str(s["label"]) for s in available_slots if isinstance(s, dict) and s.get("label")
    ] if isinstance(available_slots, list) else []

    trigger_relevance = 1.0
    has_slots = bool(slot_labels)
    slots_signal = 1.0 if has_slots else 0.5

    active_offers = merchant.active_offers
    has_offer = bool(active_offers)
    offer_signal = 1.0 if has_offer else 0.5

    engagement_potential = 0.6  # same fixed baseline the other two generators use

    raw = trigger_relevance + slots_signal + offer_signal + engagement_potential
    urgency_factor = 1.0 + _URGENCY_FACTOR_PER_LEVEL * (trigger.urgency - _URGENCY_BASELINE)
    score = _clamp((raw / 3.6) * urgency_factor)

    facts: list[str] = []
    evidence: list[str] = [
        "trigger.kind",
        "trigger.payload.service_due",
        "trigger.payload.last_service_date",
        "trigger.payload.available_slots",
        "trigger.urgency",
        "customer.consent.scope",
        "merchant.offers",
    ]
    # Same fact-only enrichment as the sibling winback generator above -- a real, grounded
    # number, never a scoring input, omitted when 0/absent rather than stating a hollow fact.
    visits_total = customer.visits_total
    if visits_total is not None and visits_total > 0:
        facts.append(f"{visits_total} visit(s) with you before this")
        evidence.append("customer.relationship.visits_total")
    if isinstance(service_due, str) and service_due:
        facts.append(f"your {service_due.replace('_', ' ')} recall is due")
    if isinstance(last_service_date, str) and last_service_date:
        facts.append(f"your last visit was on {last_service_date}")
    if isinstance(due_date, str) and due_date:
        facts.append(f"your recall is due by {due_date}")
    for label in slot_labels:
        facts.append(f"available slot: {label}")
    for offer in active_offers:
        title = offer.get("title")
        if title:
            facts.append(str(title))

    return Opportunity(
        name="recall_due",
        action_type="recall_reminder",
        score=score,
        cta="multi_choice_slot" if has_slots else "open_ended",
        facts=facts,
        evidence=evidence,
        reason=(
            "This customer's recall window has opened and they've consented to recall "
            "reminders — worth offering real available slots rather than a generic nudge."
        ),
    )


def _already_discussed_in_conversation_history(merchant: MerchantContext, molecule: str) -> bool:
    """Narrow, bounded check — not a general text-matching engine: does a specific molecule name
    already appear in a prior message *from Vera*, WITH genuine two-sided engagement evidence
    (at least one separate entry from the other side), rather than a one-sided Vera monologue?
    Found necessary from the real seed data itself: m_009_apollo_pharmacy_jaipur's
    conversation_history already shows a prior "voluntary recall on atorvastatin..." message AND
    a separate merchant reply "Yes send me the list please" (engagement: intent_action) —
    composing a fresh identical pitch on this exact canonical scenario would be a real,
    demonstrable repetition/Decision-Quality failure, not a hypothetical one.

    P1 fix (hostile-audit finding): this check reads merchant.conversation_history, which is
    judge-supplied context (challenge-brief.md/engagement-design.md document it as "last N turns
    w/ Vera, with engagement tags" -- historical context, never described as an authoritative
    record of what THIS bot instance has sent) -- previously a single injected "vera"-authored
    entry, with no corresponding reply, was sufficient on its own to suppress an otherwise fully
    justified compliance/safety alert (score 1.0, the clamped maximum for the real seed case, so
    no bounded scoring adjustment could ever fix this -- only a hard gate can, and only a
    correspondingly harder-to-forge gate closes the exploit). Requiring a genuine second,
    other-side entry is grounded directly in the real data's own actual shape, not an invented
    threshold -- it closes the specific single-field attack demonstrated in the audit. It is NOT
    a claim of cryptographic unforgeability: the judge legitimately controls this entire payload
    in one request, and no purely structural check on its content can fully rule out a more
    elaborate two-sided fabrication -- that is an architectural limit of trusting judge-supplied
    historical context at all, not something this fix can close further without inventing a
    verification mechanism the contract does not provide."""
    molecule_lower = molecule.lower()
    vera_mentioned_it = any(
        entry.get("from") == "vera" and molecule_lower in str(entry.get("body", "")).lower()
        for entry in merchant.conversation_history
    )
    if not vera_mentioned_it:
        return False
    other_side_responded = any(
        entry.get("from") not in (None, "vera") for entry in merchant.conversation_history
    )
    return other_side_responded


def _supply_alert_opportunity(
    merchant: MerchantContext,
    trigger: TriggerContext,
    customer: CustomerContext | None,  # unused: merchant-scoped, no customer_id on this trigger
    category: CategoryContext | None,  # unused: no category digest/vocab lookup needed for this decision
) -> Opportunity | None:
    if trigger.kind != "supply_alert":
        return None

    payload: dict[str, Any] = trigger.payload
    molecule = payload.get("molecule")
    affected_batches = payload.get("affected_batches")
    manufacturer = payload.get("manufacturer")

    if not isinstance(molecule, str) or not molecule:
        return None
    if _already_discussed_in_conversation_history(merchant, molecule):
        return None

    chronic_rx_count = merchant.chronic_rx_count
    # We only ever have the merchant's TOTAL chronic-Rx count, never a count filtered to which
    # specific customers were dispensed these exact batches — that would require per-customer
    # dispensing records this dataset doesn't provide. Citing a specific "N affected" figure
    # without that provenance would be exactly the fabrication case-studies.md itself warns
    # against, so the total count is used only as a relevance signal, not stated as "N affected".
    has_chronic_rx_customers = bool(chronic_rx_count)
    customer_impact_signal = 1.0 if has_chronic_rx_customers else 0.3

    trigger_relevance = 1.0
    engagement_potential = 0.6

    raw = trigger_relevance + customer_impact_signal + engagement_potential
    urgency_factor = 1.0 + _URGENCY_FACTOR_PER_LEVEL * (trigger.urgency - _URGENCY_BASELINE)
    score = _clamp((raw / 2.6) * urgency_factor)

    facts = [f"voluntary recall on {molecule}"]
    if isinstance(affected_batches, list) and affected_batches:
        facts.append(f"affected batches: {', '.join(str(b) for b in affected_batches)}")
    if isinstance(manufacturer, str) and manufacturer:
        facts.append(f"manufacturer {manufacturer}")
    if chronic_rx_count:  # a count of exactly 0 has nothing informative to state — omit, don't
        # claim "0 chronic-Rx customers on file", which reads as an odd non-fact rather than
        # useful context (same reasoning as omitting an offer fact when there are no active offers).
        facts.append(f"{chronic_rx_count} chronic-Rx customers on file")

    return Opportunity(
        name=f"supply_alert:{molecule}",
        action_type="compliance_alert",
        score=score,
        cta="open_ended",
        facts=facts,
        evidence=[
            "trigger.payload.molecule",
            "trigger.payload.affected_batches",
            "trigger.payload.manufacturer",
            "trigger.urgency",
            "merchant.customer_aggregate.chronic_rx_count",
            "merchant.conversation_history",
        ],
        reason=(
            f"A voluntary recall on {molecule} is a compliance-relevant safety alert this "
            f"merchant's customers should be informed about — not previously raised with them."
        ),
    )


# Positive mirror of _seasonal_dip_reframe_opportunity's magnitude gate: a spike smaller than
# this is nothing worth interrupting the merchant about. 0.10 mirrors _MEANINGFUL_DIP_THRESHOLD's
# magnitude symmetrically; the one real seed example (perf_spike:m_008, calls +15%) clears it
# with room, and there's no evidence to support a different cutoff.
_MEANINGFUL_SPIKE_THRESHOLD = 0.10


def _perf_spike_opportunity(
    merchant: MerchantContext,
    trigger: TriggerContext,
    customer: CustomerContext | None,  # merchant-scoped trigger, no customer_id in the real data
    category: CategoryContext | None,  # unused: no category digest lookup needed for this decision
) -> Opportunity | None:
    if trigger.kind != "perf_spike":
        return None

    payload: dict[str, Any] = trigger.payload
    metric = payload.get("metric")
    delta_pct_raw = payload.get("delta_pct")
    delta_pct: float | None = delta_pct_raw if isinstance(delta_pct_raw, (int, float)) else None
    likely_driver = payload.get("likely_driver")
    vs_baseline = payload.get("vs_baseline")

    # Hard gate, same reasoning as the dip's own: below-threshold movement isn't a real spike,
    # and a missing metric/delta leaves nothing grounded to report.
    if metric is None or delta_pct is None:
        return None
    if delta_pct < _MEANINGFUL_SPIKE_THRESHOLD:
        return None

    magnitude_signal = 1.0 if delta_pct >= _MEANINGFUL_SPIKE_THRESHOLD * 2 else 0.6

    active_offers = merchant.active_offers
    has_offer = bool(active_offers)
    # A spike is a "double down" moment: a concrete offer to push harder on is more actionable
    # than a bare "keep it up", mirroring the dip generator's own has_offer/actionability split.
    actionability = 1.0 if has_offer else 0.5

    trigger_relevance = 1.0  # gated above: only ever reached past the meaningful-spike threshold
    engagement_potential = 0.6  # same fixed baseline every other generator uses

    raw = trigger_relevance + magnitude_signal + actionability + engagement_potential
    urgency_factor = 1.0 + _URGENCY_FACTOR_PER_LEVEL * (trigger.urgency - _URGENCY_BASELINE)
    score = _clamp((raw / 3.6) * urgency_factor)

    facts = [f"{metric} up {delta_pct * 100:.0f}% this week"]
    if isinstance(vs_baseline, (int, float)):
        facts.append(f"vs a baseline of {vs_baseline}")
    if isinstance(likely_driver, str) and likely_driver:
        facts.append(f"likely driver: {likely_driver.replace('_', ' ')}")
    for offer in active_offers:
        title = offer.get("title")
        if title:
            facts.append(str(title))

    return Opportunity(
        name="perf_spike",
        action_type="perf_spike_capitalize",
        score=score,
        cta="open_ended",
        facts=facts,
        evidence=[
            "trigger.payload.metric",
            "trigger.payload.delta_pct",
            "trigger.payload.vs_baseline",
            "trigger.payload.likely_driver",
            "trigger.urgency",
            "merchant.offers",
        ],
        reason=(
            f"{metric} is up {delta_pct * 100:.0f}% — a real, grounded moment to help the "
            f"merchant capitalize on whatever's working rather than let it pass unnoticed."
        ),
    )


def _milestone_reached_opportunity(
    merchant: MerchantContext,
    trigger: TriggerContext,
    customer: CustomerContext | None,  # merchant-scoped trigger, no customer_id in the real data
    category: CategoryContext | None,
) -> Opportunity | None:
    if trigger.kind != "milestone_reached":
        return None

    payload: dict[str, Any] = trigger.payload
    metric = payload.get("metric")
    value_now_raw = payload.get("value_now")
    milestone_value_raw = payload.get("milestone_value")
    value_now: float | None = value_now_raw if isinstance(value_now_raw, (int, float)) else None
    milestone_value: float | None = (
        milestone_value_raw if isinstance(milestone_value_raw, (int, float)) else None
    )
    is_imminent = payload.get("is_imminent") is True

    # Without both real numbers there is no grounded milestone claim to make at all.
    if metric is None or value_now is None or milestone_value is None:
        return None
    already_crossed = value_now >= milestone_value
    # Neither "about to cross" nor "already crossed" alone — nothing worth a message yet.
    if not already_crossed and not is_imminent:
        return None

    trigger_relevance = 1.0
    milestone_signal = 1.0 if already_crossed else 0.7  # a crossed milestone is a firmer,
    # more celebratory fact than an approaching one, mirroring the dip generator's magnitude split.
    engagement_potential = 0.7  # a milestone is inherently a positive, low-friction, celebratory
    # nudge — scored slightly above the 0.6 baseline other generators use, on the same evidence
    # basis the dip/spike generators use fixed constants: a documented, explainable choice, not
    # an arbitrary one, reflecting that this action_type carries no ask beyond "worth noting".
    actionability = 0.6  # a real next step exists (share it publicly) even with no offer involved

    raw = trigger_relevance + milestone_signal + engagement_potential + actionability
    urgency_factor = 1.0 + _URGENCY_FACTOR_PER_LEVEL * (trigger.urgency - _URGENCY_BASELINE)
    score = _clamp((raw / 3.6) * urgency_factor)

    metric_readable = metric.replace("_", " ")
    if already_crossed:
        facts = [f"{metric_readable}: {value_now:.0f}, past the {milestone_value:.0f} milestone"]
    else:
        facts = [f"{metric_readable}: {value_now:.0f}, {milestone_value:.0f} milestone within reach"]

    evidence = [
        "trigger.payload.metric",
        "trigger.payload.value_now",
        "trigger.payload.milestone_value",
        "trigger.payload.is_imminent",
        "trigger.urgency",
    ]

    # Social-proof enrichment (challenge-brief.md SS10 lever #3, named as one of the two levers
    # production Vera under-uses today) -- gated narrowly, on real evidence only, never on every
    # milestone message:
    #   - metric must be "review_count" specifically: it's the one real trigger.payload metric
    #     with no "window" field (a lifetime/cumulative count) and the one peer_stats field with
    #     no "_30d" suffix (also cumulative) -- every other real metric (calls/views) is a 7-day
    #     trigger figure being compared against a 30-day peer average, an apples-to-oranges
    #     mismatch this deliberately does not attempt.
    #   - category.peer_avg_review_count must actually be present (never fabricated if missing).
    #   - only surfaced when value_now is at or above the peer average: this action_type is a
    #     celebration, and an unflattering below-peer comparison has no place inside one -- the
    #     underlying milestone still sends exactly as before either way, just without this fact.
    # Two real numbers stated plainly, no adjective ("well above"/"far ahead"), no percentile, no
    # rank -- nothing not directly in category.peer_stats.
    if category is not None and metric == "review_count":
        peer_avg = category.peer_avg_review_count
        if peer_avg is not None and value_now >= peer_avg:
            scope_label = category.peer_stats_scope
            if scope_label:
                facts.append(f"peer average for {scope_label.replace('_', ' ')} is {peer_avg:.0f} reviews")
            else:
                facts.append(f"peer average is {peer_avg:.0f} reviews")
            evidence.append("category.peer_stats.avg_review_count")

    return Opportunity(
        name="milestone_reached",
        action_type="milestone_celebration",
        score=score,
        cta="open_ended",
        facts=facts,
        evidence=evidence,
        reason=(
            f"{metric_readable} is at {value_now:.0f} against a {milestone_value:.0f} milestone — "
            f"a genuine, grounded reason to reach out with something positive rather than only "
            f"ever messaging about problems."
        ),
    )


# How close counts as "nearby" for this trigger kind, in the evidence-disciplined sense
# engagement-design.md itself uses ("competitor opens nearby") -- no specific radius is
# documented anywhere in the contract, so this formalizes the boundary this generator's OWN
# scoring already treated as meaningful (the second proximity tier below), rather than inventing
# a new number. The real seed's only example (1.3km, matching challenge-brief.md's own canonical
# "new dentist 1.3km away") sits comfortably inside it.
_NEARBY_COMPETITOR_RADIUS_KM = 5.0


def _competitor_opened_opportunity(
    merchant: MerchantContext,
    trigger: TriggerContext,
    customer: CustomerContext | None,  # merchant-scoped trigger, no customer_id in the real data
    category: CategoryContext | None,  # unused: no category digest lookup needed for this decision
) -> Opportunity | None:
    if trigger.kind != "competitor_opened":
        return None

    payload: dict[str, Any] = trigger.payload
    competitor_name = payload.get("competitor_name")
    distance_km_raw = payload.get("distance_km")
    distance_km: float | None = distance_km_raw if isinstance(distance_km_raw, (int, float)) else None
    their_offer = payload.get("their_offer")
    opened_date = payload.get("opened_date")

    # A competitor claim with no name and no distance is nothing grounded to report at all.
    if not competitor_name or distance_km is None:
        return None
    # Hard gate, not a soft floor -- found necessary via adversarial audit: a fixed, non-decaying
    # 0.3 floor for ANY out-of-tier distance meant trigger_relevance + actionability +
    # engagement_potential alone (2.2-2.6/3.6, itself above SEND_THRESHOLD) let a competitor
    # thousands of km away send identically to one 1.3km away. Same fix shape as
    # festival_upcoming's own timeliness gate: continuous decay alone can't work here either
    # (those three terms sum above threshold regardless of proximity), so only a hard gate closes
    # it. "Nearby" (engagement-design.md's own word for this trigger family) stops meaning
    # anything past this radius, so there's nothing grounded left to report beyond it.
    if distance_km > _NEARBY_COMPETITOR_RADIUS_KM:
        return None

    # Proximity is used only as a continuous scoring input inside the nearby radius (closer =
    # more relevant to inform the merchant about) -- the real seed's only example (1.3km) sits in
    # the top tier, and there's no finer-grained evidence to justify more tiers than these two.
    proximity_signal = 1.0 if distance_km <= 2.0 else 0.6

    has_offer_context = bool(their_offer)
    actionability = 1.0 if has_offer_context else 0.6  # a competitor's specific offer gives a
    # concrete comparison point; without one, still worth flagging the opening itself.

    trigger_relevance = 1.0
    engagement_potential = 0.6

    raw = trigger_relevance + proximity_signal + actionability + engagement_potential
    urgency_factor = 1.0 + _URGENCY_FACTOR_PER_LEVEL * (trigger.urgency - _URGENCY_BASELINE)
    score = _clamp((raw / 3.6) * urgency_factor)

    facts = [f"{competitor_name} opened {distance_km:g}km away"]
    if isinstance(opened_date, str) and opened_date:
        facts.append(f"opened {opened_date}")
    if isinstance(their_offer, str) and their_offer:
        facts.append(f"their offer: {their_offer}")

    return Opportunity(
        name=f"competitor_opened:{competitor_name}",
        action_type="competitor_awareness",
        score=score,
        cta="open_ended",
        facts=facts,
        evidence=[
            "trigger.payload.competitor_name",
            "trigger.payload.distance_km",
            "trigger.payload.their_offer",
            "trigger.payload.opened_date",
            "trigger.urgency",
        ],
        reason=(
            f"{competitor_name} opened {distance_km:g}km away — the merchant should hear this "
            f"from Vera first, informational and neutral, not alarmist."
        ),
    )


# TIMELINESS_WINDOW_DAYS (14, module-level) is reused here rather than a new invented constant —
# same "worth mentioning inside this window" reasoning festival_upcoming already applies to its
# own days_until field. The one real example (days_remaining=12) sits comfortably inside it.
def _renewal_due_opportunity(
    merchant: MerchantContext,
    trigger: TriggerContext,
    customer: CustomerContext | None,  # merchant-scoped trigger, no customer_id in the real data
    category: CategoryContext | None,  # unused: no category digest lookup needed for this decision
) -> Opportunity | None:
    if trigger.kind != "renewal_due":
        return None

    payload: dict[str, Any] = trigger.payload
    days_remaining_raw = payload.get("days_remaining")
    days_remaining: float | None = (
        days_remaining_raw if isinstance(days_remaining_raw, (int, float)) else None
    )
    plan = payload.get("plan")
    renewal_amount = payload.get("renewal_amount")

    if days_remaining is None or plan is None:
        return None
    # Too early to be worth interrupting the merchant about yet -- same window as festival_upcoming.
    if not (0 <= days_remaining <= TIMELINESS_WINDOW_DAYS):
        return None

    timeliness = _clamp(1 - (days_remaining / TIMELINESS_WINDOW_DAYS))
    trigger_relevance = 1.0  # gated above: only reached inside the timeliness window
    engagement_potential = 0.6

    raw = trigger_relevance + timeliness + engagement_potential
    urgency_factor = 1.0 + _URGENCY_FACTOR_PER_LEVEL * (trigger.urgency - _URGENCY_BASELINE)
    score = _clamp((raw / 2.6) * urgency_factor)

    facts = [f"{plan} plan renews in {days_remaining:.0f} day(s)"]
    if isinstance(renewal_amount, (int, float)):
        facts.append(f"renewal amount ₹{renewal_amount:g}")

    return Opportunity(
        name="renewal_due",
        action_type="renewal_reminder",
        score=score,
        cta="open_ended",
        facts=facts,
        evidence=[
            "trigger.payload.days_remaining",
            "trigger.payload.plan",
            "trigger.payload.renewal_amount",
            "trigger.urgency",
        ],
        reason=(
            f"{plan} plan renews in {days_remaining:.0f} day(s) — a timely, practical heads-up "
            f"about the merchant's own account, not a business-growth pitch."
        ),
    )


def _readable_question(ask_template: str) -> str:
    """Mechanical transform of the real trigger.payload.ask_template string only -- never a
    lookup table of known phrasings. The one real example ("what_service_in_demand_this_week")
    reads as a near-complete question once de-slugged; this must generalize to any future
    ask_template value the judge might push, not just the one seen in the seed data, so it does
    nothing smarter than de-slug + capitalize + ensure a trailing '?' -- the same discipline
    already used for trigger.payload.previous_focus elsewhere in this file."""
    words = ask_template.replace("_", " ").strip()
    if not words:
        return words
    text = words[0].upper() + words[1:]
    return text if text.endswith("?") else f"{text}?"


def _curious_ask_due_opportunity(
    merchant: MerchantContext,  # unused: no merchant-state precondition evidenced for this kind
    trigger: TriggerContext,
    customer: CustomerContext | None,  # merchant-scoped trigger, no customer_id in the real data
    category: CategoryContext | None,  # unused: no category digest/vocab lookup needed
) -> Opportunity | None:
    """challenge-brief.md SS10 names 'asking the merchant' (lever #7, e.g. "what's your
    most-asked treatment this week?") and 'social proof' (lever #3) as the two levers production
    Vera under-uses today. This generator is the only current opportunity kind whose whole point
    is a genuine, low-stakes QUESTION rather than a pitch -- Vera has enough evidence a curiosity-
    ask is due (the trigger itself, scheduled by the judge/data pipeline's own weekly cadence, not
    re-derived here) but deliberately does not claim to know the answer, recommend an action, or
    invent any urgency/statistic around it.
    """
    if trigger.kind != "curious_ask_due":
        return None

    payload: dict[str, Any] = trigger.payload
    ask_template = payload.get("ask_template")
    last_ask_at = payload.get("last_ask_at")

    # No template, nothing grounded to ask -- insufficient evidence, not a low score.
    if not isinstance(ask_template, str) or not ask_template.strip():
        return None

    trigger_relevance = 1.0  # the trigger's own existence is the judge/data pipeline's
    # classification that this ask is due now -- same evidence-disciplined trust already applied
    # to customer_lapsed_hard/recall_due, not re-derived from last_ask_at (no evidenced
    # "how-recent-is-too-recent" threshold exists anywhere in the contract).
    actionability = 1.0  # gated above: only reached with a real, non-empty template
    engagement_potential = 0.6  # same fixed baseline every generator in this file reuses

    raw = trigger_relevance + actionability + engagement_potential
    urgency_factor = 1.0 + _URGENCY_FACTOR_PER_LEVEL * (trigger.urgency - _URGENCY_BASELINE)
    score = _clamp((raw / 2.6) * urgency_factor)

    facts = [_readable_question(ask_template)]
    if isinstance(last_ask_at, str) and last_ask_at:
        facts.append(f"last asked this on {last_ask_at}")

    return Opportunity(
        name="curious_ask",
        action_type="curious_ask",
        score=score,
        cta="open_ended",
        facts=facts,
        evidence=[
            "trigger.kind",
            "trigger.payload.ask_template",
            "trigger.payload.last_ask_at",
            "trigger.urgency",
        ],
        reason=(
            "A curiosity-ask is due for this merchant -- worth a genuine, low-stakes question "
            "rather than a pitch; the merchant's answer is itself the value, not assumed."
        ),
    )


def _perf_dip_opportunity(
    merchant: MerchantContext,  # unused: no evidenced merchant-state precondition for this kind
    trigger: TriggerContext,
    customer: CustomerContext | None,  # merchant-scoped trigger, no customer_id in the real data
    category: CategoryContext | None,  # deliberately unused -- see the peer_stats note below
) -> Opportunity | None:
    """The unexpected-decline sibling of _seasonal_dip_reframe_opportunity: that generator hard-
    gates on payload.is_expected_seasonal being true and reassures ("this is normal"); perf_dip's
    real payload carries no such flag at all, so there is no expected/seasonal story to tell here
    -- only a plain, real decline, presented professionally and without a claimed cause.

    Reuses _MEANINGFUL_DIP_THRESHOLD/_STRONG_DIP_THRESHOLD (module-level, already defined for
    seasonal_perf_dip) rather than inventing a new magnitude cutoff -- both trigger kinds report
    the same delta_pct shape, and "how much decline is meaningful" is not evidenced to differ
    between them.

    No peer_stats comparison: perf_dip's real payload carries "window": "7d" (7-day), while
    category.peer_stats' avg_calls_30d/avg_views_30d are 30-day averages -- the same window
    mismatch _milestone_reached_opportunity's own peer-stats enrichment was scoped away from.
    Comparing a 7-day figure to a 30-day peer average would be exactly the kind of comparison the
    data doesn't support, so this deliberately never attempts it, however tempting the data
    "looks" adjacent.

    No active_offers reference either (unlike its seasonal sibling, which treats an offer as a
    reassuring "pivot"): juxtaposing "your calls dropped" with "here's an offer" risks reading as
    an implied cause-and-cure this generator has no evidence for. The message states the decline
    and nothing else grounded enough to add.
    """
    if trigger.kind != "perf_dip":
        return None

    payload: dict[str, Any] = trigger.payload
    metric = payload.get("metric")
    delta_pct_raw = payload.get("delta_pct")
    delta_pct: float | None = delta_pct_raw if isinstance(delta_pct_raw, (int, float)) else None
    vs_baseline = payload.get("vs_baseline")

    if metric is None or delta_pct is None:
        return None
    # Hard gate, not a low score -- same reasoning as every other magnitude-gated generator in
    # this file (a below-threshold movement isn't a "dip" worth a message at all).
    if delta_pct > _MEANINGFUL_DIP_THRESHOLD:
        return None

    magnitude_signal = 1.0 if delta_pct <= _STRONG_DIP_THRESHOLD else 0.4  # same tiers as the
    # seasonal sibling's own magnitude split, reused rather than invented.
    has_baseline = isinstance(vs_baseline, (int, float))
    actionability = 1.0 if has_baseline else 0.5  # a concrete reference number gives the merchant
    # something specific to react to; without one, still a real, if barer, fact to surface.
    trigger_relevance = 1.0  # gated above: only ever reached past the meaningful-dip threshold
    engagement_potential = 0.6  # same fixed baseline every generator in this file reuses

    raw = trigger_relevance + magnitude_signal + actionability + engagement_potential
    urgency_factor = 1.0 + _URGENCY_FACTOR_PER_LEVEL * (trigger.urgency - _URGENCY_BASELINE)
    score = _clamp((raw / 3.6) * urgency_factor)

    facts = [f"{metric} down {abs(delta_pct) * 100:.0f}% this week"]
    if has_baseline:
        facts.append(f"vs a baseline of {vs_baseline}")

    return Opportunity(
        name="perf_dip",
        action_type="perf_dip_flag",
        score=score,
        cta="open_ended",
        facts=facts,
        evidence=[
            "trigger.payload.metric",
            "trigger.payload.delta_pct",
            "trigger.payload.vs_baseline",
            "trigger.urgency",
        ],
        reason=(
            f"{metric} is down {abs(delta_pct) * 100:.0f}% — a real, meaningful decline worth "
            f"surfacing plainly and inviting a conversation, without assuming a cause or "
            f"promising a fix Vera has no evidence for."
        ),
    )


def _theme_sentiment(merchant: MerchantContext, theme: str) -> str | None:
    """The real cross-reference this generator relies on for sentiment: a review_theme_emerged
    trigger's own payload never carries a sentiment field (confirmed against the one real
    instance, trg_011_review_theme_late_delivery) -- only merchant.review_themes[] does, keyed by
    the same theme string. Verified against real data: that trigger's merchant
    (m_005_pizzajunction) has a review_themes entry {"theme": "delivery_late", "sentiment": "neg",
    "occurrences_30d": 4} matching both the theme name AND the occurrence count exactly -- the
    same real event, not a coincidental match. Returns None (not a guessed default) when no
    matching entry exists, or when the matched entry's own sentiment isn't the two documented
    values -- an unmatched theme gets fully neutral phrasing, never an assumed sentiment.
    """
    for entry in merchant.review_themes:
        if entry.get("theme") == theme:
            sentiment = entry.get("sentiment")
            return sentiment if sentiment in ("pos", "neg") else None
    return None


def _review_theme_emerged_opportunity(
    merchant: MerchantContext,
    trigger: TriggerContext,
    customer: CustomerContext | None,  # merchant-scoped trigger, no customer_id in the real data
    category: CategoryContext | None,  # unused: no category digest/vocab lookup needed
) -> Opportunity | None:
    """Qualitative customer-evidence signal, not a numeric one. Deliberately does not correlate
    with any other trigger/metric (e.g. a concurrent perf_dip for the same merchant) -- doing so
    would assert a causal link between review sentiment and a performance number that neither
    payload establishes; each generator here reasons only from its own trigger's evidence, by
    existing design (generate_opportunities() picks the single best-scoring opportunity, it never
    merges reasoning across them).
    """
    if trigger.kind != "review_theme_emerged":
        return None

    payload: dict[str, Any] = trigger.payload
    theme = payload.get("theme")
    occurrences_raw = payload.get("occurrences_30d")
    occurrences: float | None = occurrences_raw if isinstance(occurrences_raw, (int, float)) else None
    common_quote_raw = payload.get("common_quote")
    common_quote: str | None = common_quote_raw.strip() if isinstance(common_quote_raw, str) else None
    has_quote = bool(common_quote)
    trend = payload.get("trend")

    if not isinstance(theme, str) or not theme.strip():
        return None
    # A theme name alone, with neither a count nor a quote, is nothing concrete to report --
    # insufficient evidence, not a low score (same discipline as every other generator's hard
    # gate on missing required data).
    if occurrences is None and not has_quote:
        return None

    sentiment = _theme_sentiment(merchant, theme)

    evidence_richness = 1.0 if has_quote else 0.5  # a real reviewer's own words is more concrete
    # than a bare count -- the same "richer grounded data available" pattern every other
    # generator's actionability/magnitude term already follows.
    trend_signal = 1.0 if trend == "rising" else 0.6  # the one real evidenced trend value; any
    # other value (or absence) gets the same moderate baseline other generators use for "present
    # but not the strongest evidenced case" rather than a penalty -- this is real evidence either
    # way, just not proven to be accelerating.
    trigger_relevance = 1.0  # gated above: only reached with real, non-empty evidence
    engagement_potential = 0.6  # same fixed baseline every generator in this file reuses

    raw = trigger_relevance + evidence_richness + trend_signal + engagement_potential
    urgency_factor = 1.0 + _URGENCY_FACTOR_PER_LEVEL * (trigger.urgency - _URGENCY_BASELINE)
    score = _clamp((raw / 3.6) * urgency_factor)

    theme_readable = theme.replace("_", " ")
    if sentiment == "pos":
        facts = [f"customers have positively mentioned {theme_readable}"]
    else:
        # Both "neg" and unknown/unmatched sentiment use the same neutral phrasing --
        # "customers are mentioning X" per the requested framing, never a complaint/blame framing
        # and never an assumed-positive one either when sentiment genuinely isn't known.
        facts = [f"customers have mentioned {theme_readable}"]
    if occurrences is not None:
        facts.append(f"{occurrences:.0f} time(s) in the last 30 days")
    if common_quote:
        facts.append(f'one review said: "{common_quote}"')
    if trend == "rising":
        facts.append("mentions have been rising")

    if sentiment == "pos":
        reason = (
            f"Customers have positively mentioned {theme_readable} — genuine, grounded feedback "
            f"worth acknowledging without overstating it."
        )
    else:
        reason = (
            f"Customers have mentioned {theme_readable} — worth surfacing plainly and inviting a "
            f"conversation, without assuming a cause, a fix, or how widespread it is beyond what "
            f"was actually reported."
        )

    return Opportunity(
        name="review_theme_emerged",
        action_type="review_theme_flag",
        score=score,
        cta="open_ended",
        facts=facts,
        evidence=[
            "trigger.payload.theme",
            "trigger.payload.occurrences_30d",
            "trigger.payload.common_quote",
            "trigger.payload.trend",
            "trigger.urgency",
            "merchant.review_themes",
        ],
        reason=reason,
    )


_GENERATORS = (
    _festival_opportunity,
    _seasonal_dip_reframe_opportunity,
    _customer_lapsed_winback_opportunity,
    _recall_due_opportunity,
    _supply_alert_opportunity,
    _perf_spike_opportunity,
    _milestone_reached_opportunity,
    _competitor_opened_opportunity,
    _renewal_due_opportunity,
    _curious_ask_due_opportunity,
    _perf_dip_opportunity,
    _review_theme_emerged_opportunity,
)


def generate_opportunities(
    merchant: MerchantContext,
    trigger: TriggerContext,
    customer: CustomerContext | None,
    category: CategoryContext | None = None,
) -> list[Opportunity]:
    opportunities = [_fallback_opportunity()]
    corroboration = _signal_corroboration_bonus(merchant, trigger)
    for generator in _GENERATORS:
        opportunity = generator(merchant, trigger, customer, category)
        if opportunity is not None:
            if corroboration:
                # Applied only to an opportunity that already passed every one of its own hard
                # gates (category mismatch, consent, staleness, ...) inside the generator that
                # produced it — this can never rescue a None. Never touches action_type/cta/facts.
                opportunity.score = _clamp(opportunity.score + corroboration)
                opportunity.evidence = [*opportunity.evidence, "merchant.signals"]
            opportunities.append(opportunity)
    return opportunities
