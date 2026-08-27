"""Decision-quality experiment: does trigger.urgency belong in the opportunity score, and if so,
how much?

Investigation summary (full detail in the session report): the official package gives no
documented semantics for merchant.performance/merchant.signals modulating a festival_upcoming
opportunity's score — every case study that uses performance data does so because the TRIGGER
ITSELF is a performance trigger (perf_dip/perf_spike/seasonal_perf_dip), confirmed structurally
in triggers_seed.json to have entirely separate payload shapes from festival_upcoming. Wiring
merchant.performance into festival scoring would be an invented rule with no evidentiary basis.

What IS documented: engagement-design.md's TriggerContext section states trigger.urgency (1-5)
"ranks against other queued triggers." That field was read into TriggerContext but never used
in opportunity.py — a real, evidence-grounded Decision Quality gap. Fixed with a bounded
multiplier (urgency=1, the value every festival_upcoming trigger in the base dataset happens to
carry, is an exact no-op; urgency=5 is capped at +20%).

Where the task's requested "competing signal" pairs use "performance" as the second axis
(strong offer + weak performance, stale trigger + strong performance, etc.), these tests
substitute trigger.urgency for "performance" and say so explicitly — urgency is the actual
evidence-grounded analog available in this vertical slice; performance itself is not wired in
and these tests do not pretend otherwise.
"""

import copy
import json
from pathlib import Path

from vera.decision.compiler import decide
from vera.decision.opportunity import generate_opportunities
from vera.domain.context import MerchantContext, TriggerContext

DATASET_DIR = Path(__file__).parent.parent.parent / "docs" / "challenge-package" / "dataset"


def _merchant_by_id(merchant_id: str) -> dict:
    merchants = json.loads((DATASET_DIR / "merchants_seed.json").read_text())["merchants"]
    return copy.deepcopy(next(m for m in merchants if m["merchant_id"] == merchant_id))


def _trigger(merchant_id: str, days_until: int, urgency: int) -> dict:
    triggers = json.loads((DATASET_DIR / "triggers_seed.json").read_text())["triggers"]
    trigger = copy.deepcopy(next(t for t in triggers if t["kind"] == "festival_upcoming"))
    trigger["id"] = "trg_dq"
    trigger["merchant_id"] = merchant_id
    trigger["payload"]["days_until"] = days_until
    trigger["urgency"] = urgency
    trigger["suppression_key"] = f"festival:diwali:2026:{merchant_id}"
    return trigger


def _score(merchant_raw: dict, trigger_raw: dict) -> float:
    opportunities = generate_opportunities(MerchantContext(merchant_raw), TriggerContext(trigger_raw), None)
    return max(o.score for o in opportunities)


PIZZA_ID = "m_005_pizzajunction_restaurant_delhi"


# 1. Baseline: urgency=1 (the real seed value) is an exact no-op. Regression-pinned against the
# close+offer score measured before this change (0.877), confirming urgency=1 alters nothing.
def test_urgency_one_is_a_true_no_op() -> None:
    merchant = _merchant_by_id(PIZZA_ID)
    score_default = _score(merchant, _trigger(PIZZA_ID, days_until=3, urgency=1))
    assert abs(score_default - 0.877) < 0.001


# 2. Elevated urgency increases score for an otherwise-identical, already-sending opportunity —
# but only modestly (bounded to +20% at the urgency ceiling), and the decision fields it doesn't
# own (cta, action_type, send_as) are unaffected.
def test_elevated_urgency_increases_score_boundedly_without_changing_other_decision_fields() -> None:
    merchant = _merchant_by_id(PIZZA_ID)
    trigger_low = _trigger(PIZZA_ID, days_until=3, urgency=1)
    trigger_high = _trigger(PIZZA_ID, days_until=3, urgency=5)

    decision_low = decide(MerchantContext(merchant), TriggerContext(trigger_low), None)
    decision_high = decide(MerchantContext(merchant), TriggerContext(trigger_high), None)

    assert decision_high.confidence > decision_low.confidence
    assert decision_high.confidence <= decision_low.confidence * 1.2 + 1e-9
    assert decision_low.cta == decision_high.cta == "binary_yes_no"
    assert decision_low.action_type == decision_high.action_type == "festival_campaign"
    assert decision_low.send_as == decision_high.send_as == "vera"


# 3. "stale trigger + strong performance" (substituting urgency): max urgency cannot rescue a
# fundamentally stale, offerless opportunity into a send.
def test_max_urgency_cannot_rescue_a_stale_offerless_trigger() -> None:
    merchant = _merchant_by_id(PIZZA_ID)
    merchant["offers"] = []
    trigger = _trigger(PIZZA_ID, days_until=300, urgency=5)
    decision = decide(MerchantContext(merchant), TriggerContext(trigger), None)
    assert decision.send is False


# 4. "weak offer + strong demand" (substituting urgency for "demand"): max urgency cannot rescue
# an offerless trigger sitting just past the timeliness window either.
def test_max_urgency_cannot_rescue_a_weak_offerless_trigger_past_the_window() -> None:
    merchant = _merchant_by_id(PIZZA_ID)
    merchant["offers"] = []
    trigger = _trigger(PIZZA_ID, days_until=15, urgency=5)
    decision = decide(MerchantContext(merchant), TriggerContext(trigger), None)
    assert decision.send is False


# 5. "category mismatch + strong performance": urgency is never even consulted, because the
# opportunity generator returns None before any scoring happens for an irrelevant category.
def test_category_mismatch_ignores_urgency_entirely() -> None:
    merchant = _merchant_by_id("m_001_drmeera_dentist_delhi")  # dentists; trigger's category_relevance excludes it
    trigger = _trigger(merchant["merchant_id"], days_until=1, urgency=5)
    decision = decide(MerchantContext(merchant), TriggerContext(trigger), None)
    assert decision.send is False
    assert decision.dominant_signal == "no_strong_opportunity"


# 6. "suppression + strong performance": already_suppressed short-circuits before opportunity
# scoring runs at all, so urgency (or anything else in the trigger) cannot override it.
def test_suppression_ignores_urgency_entirely() -> None:
    merchant = _merchant_by_id(PIZZA_ID)
    trigger = _trigger(PIZZA_ID, days_until=1, urgency=5)
    decision = decide(MerchantContext(merchant), TriggerContext(trigger), None, already_suppressed=True)
    assert decision.send is False
    assert decision.dominant_signal == "suppressed"


# 7. "strong offer + weak performance" (substituting urgency): a close, offer-backed opportunity
# sends regardless of urgency being at its floor — the offer/timing evidence is sufficient on
# its own, exactly as before this change (confirms urgency is additive, not required).
def test_strong_offer_sends_even_at_minimum_urgency() -> None:
    merchant = _merchant_by_id(PIZZA_ID)
    trigger = _trigger(PIZZA_ID, days_until=3, urgency=1)
    decision = decide(MerchantContext(merchant), TriggerContext(trigger), None)
    assert decision.send is True
    assert decision.cta == "binary_yes_no"
