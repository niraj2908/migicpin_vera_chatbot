"""Golden cases for the gyms + seasonal_perf_dip expansion.

Anchored on the real seed data (PowerHouse Fitness / m_007), which is an exact numeric match to
the official Case Study 7 (views -30% w/w, 245 active members) — confirmed by direct comparison
before writing any code, not assumed.

Two counterfactuals from the requested set don't apply to this trigger kind, and are documented
here rather than forced:
- "category mismatch": festival_upcoming has an explicit trigger.payload.category_relevance
  allowlist to test against; seasonal_perf_dip has no such field — it's a purely merchant-scoped
  internal trigger whose only eligibility gate is "this trigger belongs to this merchant_id",
  already enforced structurally by the tick handler's trigger->merchant lookup, not something the
  opportunity generator itself needs to re-check.
- "customer context": this trigger's scope is "merchant" (trigger.customer_id is always null in
  the real data) — there's no customer-facing variant of it to test.
"""

import copy
import json
from pathlib import Path

from vera.decision.compiler import decide
from vera.domain.context import CategoryContext, MerchantContext, TriggerContext
from vera.generation.brief import build_brief
from vera.generation.composer import TemplateComposer
from vera.pipeline import compose_and_validate

DATASET_DIR = Path(__file__).parent.parent.parent / "docs" / "challenge-package" / "dataset"
_COMPOSER = TemplateComposer()


def _gyms_category() -> dict:
    return json.loads((DATASET_DIR / "categories" / "gyms.json").read_text())


def _merchant() -> dict:
    merchants = json.loads((DATASET_DIR / "merchants_seed.json").read_text())["merchants"]
    return copy.deepcopy(next(m for m in merchants if m["merchant_id"] == "m_007_powerhouse_gym_bangalore"))


def _trigger() -> dict:
    triggers = json.loads((DATASET_DIR / "triggers_seed.json").read_text())["triggers"]
    return copy.deepcopy(next(t for t in triggers if t["kind"] == "seasonal_perf_dip"))


def _run(merchant_raw: dict, category_raw: dict, trigger_raw: dict, *, already_suppressed: bool = False):
    merchant = MerchantContext(merchant_raw)
    category = CategoryContext(category_raw)
    trigger = TriggerContext(trigger_raw)
    decision = decide(merchant, trigger, None, already_suppressed=already_suppressed, category=category)
    body, brief = None, None
    if decision.send:
        brief = build_brief(decision, merchant, category, None)
        body = compose_and_validate(brief, _COMPOSER).message
    return decision, body, brief


def test_golden_1_official_seed_data_matches_case_study_7() -> None:
    """The real, completely unmodified seed data — not a constructed case. Verifies our
    decision reproduces the "reassure, don't panic, redirect to retention" pattern Case Study 7
    scored 48/50, using only grounded facts (dip %, category digest citation, real member count,
    real active offer)."""
    decision, body, _brief = _run(_merchant(), _gyms_category(), _trigger())

    assert decision.send is True
    assert decision.dominant_signal == "seasonal_dip_reframe"
    assert decision.action_type == "seasonal_dip_reframe"
    assert decision.cta == "open_ended"
    assert decision.send_as == "vera"
    assert body is not None
    assert "30%" in body  # the real dip magnitude
    assert "245" in body  # the real member count, matching Case Study 7 exactly


def test_golden_2_weak_dip_below_threshold_does_not_send() -> None:
    """A negligible fluctuation (-5%) isn't a "dip" worth Vera commenting on — Decision Quality
    means restraint here, not manufacturing a message from noise."""
    trigger = _trigger()
    trigger["payload"]["delta_pct"] = -0.05
    decision, body, _brief = _run(_merchant(), _gyms_category(), trigger)

    assert decision.send is False
    assert body is None


def test_golden_3_not_flagged_as_seasonal_does_not_fire() -> None:
    """The entire premise of this trigger kind is "expected seasonal pattern" — without that
    flag, there's no grounded reframe story, so the opportunity must not fire at all (not fall
    back to inventing one)."""
    trigger = _trigger()
    trigger["payload"]["is_expected_seasonal"] = False
    decision, body, _brief = _run(_merchant(), _gyms_category(), trigger)

    assert decision.send is False
    assert decision.dominant_signal == "no_strong_opportunity"
    assert body is None


def test_golden_4_missing_offer_still_sends_with_open_ended_cta() -> None:
    """No active offer to pivot to — the message can still be sent (reassurance + "want me to
    draft a retention idea?" doesn't require a concrete offer), but must not invent one."""
    merchant = _merchant()
    merchant["offers"] = []
    decision, body, _brief = _run(merchant, _gyms_category(), _trigger())

    assert decision.send is True
    assert decision.cta == "open_ended"
    assert body is not None
    assert "3 FREE Trial Classes" not in body


def test_golden_5_suppression_blocks_a_repeat_send() -> None:
    decision, body, _brief = _run(_merchant(), _gyms_category(), _trigger(), already_suppressed=True)
    assert decision.send is False
    assert decision.dominant_signal == "suppressed"
    assert body is None


def test_golden_6_changed_member_count_updates_the_fact() -> None:
    """Merchant Fit: a different merchant's real member count must appear, not the seed's."""
    merchant = _merchant()
    merchant["customer_aggregate"]["total_active_members"] = 412
    decision, body, _brief = _run(merchant, _gyms_category(), _trigger())

    assert decision.send is True
    assert any("412" in fact for fact in decision.facts_allowed)
    assert body is not None
    assert "412" in body
    assert "245" not in body


def test_golden_7_changed_offer_updates_the_fact_not_the_old_one() -> None:
    merchant = _merchant()
    merchant["offers"][0]["title"] = "First Month @ ₹499"
    decision, body, _brief = _run(merchant, _gyms_category(), _trigger())

    assert decision.send is True
    assert body is not None
    assert "First Month @ ₹499" in body
    assert "3 FREE Trial Classes" not in body


def test_golden_8_changed_trigger_magnitude_changes_confidence_not_just_composition() -> None:
    """A stronger dip is a stronger (if still bounded) signal — Decision Quality, not just
    wording, should reflect it."""
    trigger_strong = _trigger()
    trigger_strong["payload"]["delta_pct"] = -0.40

    trigger_moderate = _trigger()
    trigger_moderate["payload"]["delta_pct"] = -0.12  # meaningful but below the "strong" threshold

    strong_decision, _b1, _br1 = _run(_merchant(), _gyms_category(), trigger_strong)
    moderate_decision, _b2, _br2 = _run(_merchant(), _gyms_category(), trigger_moderate)

    assert strong_decision.send is True
    assert moderate_decision.send is True
    assert strong_decision.confidence >= moderate_decision.confidence
