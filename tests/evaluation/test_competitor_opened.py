"""Tests for competitor_opened -- dentists x competitor_opened, anchored on real seed data:
m_001_drmeera_dentist_delhi / trg_023_competitor_opened_dentist.

Feasibility evidence: real, documented external trigger kind (challenge-brief.md's external
trigger list includes "competitor_opened (new dentist 1.3km away on GBP)" -- this exact real
seed trigger), fully self-contained real payload, merchant-scoped, no consent gate. Prompt style
per engagement-design.md: "voyeur-curiosity framing" -- informational and neutral, not alarmist,
never disparaging the named competitor.
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


def _dentists_category() -> dict:
    return json.loads((DATASET_DIR / "categories" / "dentists.json").read_text())


def _merchant() -> dict:
    merchants = json.loads((DATASET_DIR / "merchants_seed.json").read_text())["merchants"]
    return copy.deepcopy(next(m for m in merchants if m["merchant_id"] == "m_001_drmeera_dentist_delhi"))


def _trigger() -> dict:
    triggers = json.loads((DATASET_DIR / "triggers_seed.json").read_text())["triggers"]
    return copy.deepcopy(next(t for t in triggers if t["kind"] == "competitor_opened"))


def _run(merchant_raw: dict, trigger_raw: dict, category_raw: dict, *, already_suppressed: bool = False):
    merchant = MerchantContext(merchant_raw)
    category = CategoryContext(category_raw)
    trigger = TriggerContext(trigger_raw)
    decision = decide(merchant, trigger, None, already_suppressed=already_suppressed, category=category)
    body, brief = None, None
    if decision.send:
        brief = build_brief(decision, merchant, category, None)
        body = compose_and_validate(brief, _COMPOSER).message
    return decision, body, brief


def test_golden_real_seed_data_sends_grounded_message() -> None:
    decision, body, _brief = _run(_merchant(), _trigger(), _dentists_category())
    assert decision.send is True
    assert decision.dominant_signal == "competitor_opened:Smile Studio"
    assert decision.action_type == "competitor_awareness"
    assert decision.send_as == "vera"
    assert body is not None
    assert "Smile Studio" in body
    assert "1.3km" in body


def test_golden_no_disparaging_language_in_facts_or_reason() -> None:
    """Voyeur-curiosity framing: informational, never alarmist or disparaging."""
    decision, body, _brief = _run(_merchant(), _trigger(), _dentists_category())
    assert body is not None
    disparaging = ["threat", "beware", "danger", "worried", "scared", "losing"]
    assert not any(w in body.lower() for w in disparaging)
    assert not any(w in decision.reason.lower() for w in disparaging)


def test_golden_missing_their_offer_still_sends_without_inventing_one() -> None:
    trigger = _trigger()
    del trigger["payload"]["their_offer"]
    decision, body, _brief = _run(_merchant(), trigger, _dentists_category())
    assert decision.send is True
    assert body is not None
    assert "₹199" not in body


def test_counterfactual_missing_competitor_name_does_not_send() -> None:
    trigger = _trigger()
    del trigger["payload"]["competitor_name"]
    decision, body, _brief = _run(_merchant(), trigger, _dentists_category())
    assert decision.send is False
    assert body is None


def test_counterfactual_missing_distance_does_not_send() -> None:
    trigger = _trigger()
    del trigger["payload"]["distance_km"]
    decision, _body, _brief = _run(_merchant(), trigger, _dentists_category())
    assert decision.send is False


def test_a_competitor_within_the_nearby_radius_sends_with_lower_confidence_than_very_close() -> None:
    """Within the 5km nearby radius, distance is still a graded scoring input -- 3km is a real,
    if slightly weaker, signal than the real seed's 1.3km example."""
    near = _run(_merchant(), _trigger(), _dentists_category())[0]
    trigger_moderate = _trigger()
    trigger_moderate["payload"]["distance_km"] = 3.0
    moderate = _run(_merchant(), trigger_moderate, _dentists_category())[0]
    assert moderate.send is True
    assert moderate.confidence <= near.confidence


def test_a_competitor_past_the_nearby_radius_does_not_send() -> None:
    """P1 fix (hostile-audit finding), superseding this test's prior behavior: distance was
    previously a pure scoring input with no upper bound, which is exactly what let a competitor
    thousands of km away send identically to one 1.3km away (trigger_relevance + actionability +
    engagement_potential alone already clear SEND_THRESHOLD regardless of proximity). Real
    evidence for treating this trigger family as inherently about *nearby* competitors:
    challenge-brief.md's own canonical example ("new dentist 1.3km away") and
    engagement-design.md's own description ("competitor opens nearby"). 8km is outside the
    evidence-grounded 5km radius this generator's own prior tier structure already used."""
    trigger_far = _trigger()
    trigger_far["payload"]["distance_km"] = 8.0
    far = _run(_merchant(), trigger_far, _dentists_category())[0]
    assert far.send is False
    assert far.dominant_signal == "no_strong_opportunity"


def test_suppression_blocks_a_repeat_send() -> None:
    decision, body, _brief = _run(_merchant(), _trigger(), _dentists_category(), already_suppressed=True)
    assert decision.send is False
    assert decision.dominant_signal == "suppressed"
    assert body is None


def test_different_merchants_do_not_contaminate_facts() -> None:
    m1 = _merchant()
    m2 = _merchant()
    m2["merchant_id"] = "m_other_dentist"
    t1 = _trigger()
    t2 = _trigger()
    t2["payload"]["competitor_name"] = "A Totally Different Clinic"
    _d1, body1, _ = _run(m1, t1, _dentists_category())
    _d2, body2, _ = _run(m2, t2, _dentists_category())
    assert "A Totally Different Clinic" not in (body1 or "")
    assert "Smile Studio" not in (body2 or "")


def test_malformed_distance_type_does_not_crash() -> None:
    trigger = _trigger()
    trigger["payload"]["distance_km"] = "close-ish"
    decision, _body, _brief = _run(_merchant(), trigger, _dentists_category())
    assert decision.send is False  # treated as missing, not crashed


def test_no_invented_competitor_or_prices_beyond_the_real_values() -> None:
    _decision, body, _brief = _run(_merchant(), _trigger(), _dentists_category())
    assert body is not None
    assert "₹199" in body  # the real, supported price
    import re

    prices = set(re.findall(r"₹\s*(\d[\d,]*)", body))
    assert prices <= {"199"}
