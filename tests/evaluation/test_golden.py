"""Golden cases for the restaurant + festival_upcoming vertical slice.

Anchored on the official seed dataset (docs/challenge-package/dataset), not invented from
imagination. Where a case needs a "close" trigger the official examples don't happen to provide
(the seed's own festival trigger is 188 days out), it's a principled variation: same shape,
different timing/merchant, built via the same helper the contract tests use — not a new
scenario dreamed up independently of the real schema.

Each case asserts the full documented shape: dominant signal, send decision, action type, CTA,
suppression key, required facts present, and — via the firewall path inside
compose_and_validate — that nothing ungrounded reached the body.
"""

import copy
import json
from pathlib import Path

from vera.decision.compiler import decide
from vera.domain.context import CategoryContext, MerchantContext, TriggerContext
from vera.generation.brief import build_brief
from vera.generation.composer import TemplateComposer
from vera.pipeline import compose_and_validate

from .scoring import engagement_signals, evaluate, merchant_fit_signals

DATASET_DIR = Path(__file__).parent.parent.parent / "docs" / "challenge-package" / "dataset"
_COMPOSER = TemplateComposer()  # deterministic, no API key needed — golden tests must be reproducible


def _restaurant_category() -> dict:
    return json.loads((DATASET_DIR / "categories" / "restaurants.json").read_text())


def _dentists_category() -> dict:
    return json.loads((DATASET_DIR / "categories" / "dentists.json").read_text())


def _merchant_by_id(merchant_id: str) -> dict:
    merchants = json.loads((DATASET_DIR / "merchants_seed.json").read_text())["merchants"]
    return next(m for m in merchants if m["merchant_id"] == merchant_id)


def _festival_trigger_seed() -> dict:
    triggers = json.loads((DATASET_DIR / "triggers_seed.json").read_text())["triggers"]
    return next(t for t in triggers if t["kind"] == "festival_upcoming")


def _run(merchant_raw: dict, category_raw: dict, trigger_raw: dict):
    merchant = MerchantContext(merchant_raw)
    category = CategoryContext(category_raw)
    trigger = TriggerContext(trigger_raw)
    decision = decide(merchant, trigger, None)
    body, brief = None, None
    if decision.send:
        brief = build_brief(decision, merchant, category, None)
        body = compose_and_validate(brief, _COMPOSER).message
    return decision, body, brief


def test_golden_1_restaurant_with_active_offer_close_festival() -> None:
    """
    Input: SK Pizza Junction (restaurants), active offer "Buy 1 Pizza Get 1 Free", Diwali 3
           days away, category listed as relevant.
    Dominant signal: festival:Diwali (the only opportunity generator that fires; wins over the
        always-present no_strong_opportunity fallback because it scores above threshold).
    Expected: send=True, action_type=festival_campaign, cta=binary_yes_no (offer exists so a
        concrete accept/decline is meaningful), send_as=vera (merchant-scoped trigger).
    Required facts: the festival+timing fact and the real offer title.
    Forbidden: any price/percentage not literally present in those facts (enforced by the
        firewall inside compose_and_validate — a violation here would raise, not just assert-fail).
    """
    merchant = _merchant_by_id("m_005_pizzajunction_restaurant_delhi")
    trigger = copy.deepcopy(_festival_trigger_seed())
    trigger["id"] = "trg_golden_1"
    trigger["merchant_id"] = merchant["merchant_id"]
    trigger["payload"]["days_until"] = 3
    trigger["suppression_key"] = f"festival:diwali:2026:{merchant['merchant_id']}"

    decision, body, _brief = _run(merchant, _restaurant_category(), trigger)

    assert decision.send is True
    assert decision.dominant_signal == "festival:Diwali"
    assert decision.action_type == "festival_campaign"
    assert decision.cta == "binary_yes_no"
    assert decision.send_as == "vera"
    assert decision.suppression_key == f"festival:diwali:2026:{merchant['merchant_id']}"
    assert body is not None
    assert "Diwali" in body
    assert "Buy 1 Pizza Get 1 Free" in body


def test_golden_2_restaurant_without_offer_still_sends_open_ended() -> None:
    """
    Same merchant/trigger shape, offer removed. Decision Quality here means: still worth a
    message (the festival relevance and timing alone clear the send threshold), but the CTA
    downgrades from a concrete accept/decline to open-ended since there's nothing specific to
    say yes to yet — the deterministic layer, not the LLM, makes that call.
    """
    merchant = copy.deepcopy(_merchant_by_id("m_005_pizzajunction_restaurant_delhi"))
    merchant["offers"] = []
    trigger = copy.deepcopy(_festival_trigger_seed())
    trigger["id"] = "trg_golden_2"
    trigger["merchant_id"] = merchant["merchant_id"]
    trigger["payload"]["days_until"] = 3
    trigger["suppression_key"] = f"festival:diwali:2026:{merchant['merchant_id']}"

    decision, body, _brief = _run(merchant, _restaurant_category(), trigger)

    assert decision.send is True
    assert decision.cta == "open_ended"
    assert body is not None


def test_golden_3_category_not_listed_as_relevant_does_not_send() -> None:
    """
    Same festival trigger (category_relevance = restaurants/salons/pharmacies), but the merchant
    is a dentist. Category Fit protection: no opportunity fires, dominant signal falls back to
    no_strong_opportunity, nothing is sent.
    """
    merchant = _merchant_by_id("m_001_drmeera_dentist_delhi")
    trigger = copy.deepcopy(_festival_trigger_seed())
    trigger["id"] = "trg_golden_3"
    trigger["merchant_id"] = merchant["merchant_id"]
    trigger["payload"]["days_until"] = 3

    decision, body, _brief = _run(merchant, _dentists_category(), trigger)

    assert decision.send is False
    assert decision.dominant_signal == "no_strong_opportunity"
    assert body is None


def test_golden_4_official_seed_trigger_unmodified_is_too_far_out_to_send() -> None:
    """
    The seed dataset's own festival trigger, completely unmodified (188 days out, no timing
    variation applied). A restaurant without an active offer at that distance should not be
    messaged yet — this is the real official data, not a constructed case.
    """
    merchant = copy.deepcopy(_merchant_by_id("m_005_pizzajunction_restaurant_delhi"))
    merchant["offers"] = []
    trigger = _festival_trigger_seed()  # unmodified: merchant_id is m_003 (a salon) in the seed,
    trigger = copy.deepcopy(trigger)
    trigger["merchant_id"] = merchant["merchant_id"]  # only the merchant pointer changes

    decision, _body, _brief = _run(merchant, _restaurant_category(), trigger)

    assert decision.send is False
    assert decision.dominant_signal == "festival:Diwali"  # the opportunity exists, just too weak


def test_golden_5_suppression_blocks_a_repeat_send() -> None:
    """A trigger already acted on for this merchant must not fire again."""
    merchant = _merchant_by_id("m_005_pizzajunction_restaurant_delhi")
    trigger = copy.deepcopy(_festival_trigger_seed())
    trigger["id"] = "trg_golden_5"
    trigger["merchant_id"] = merchant["merchant_id"]
    trigger["payload"]["days_until"] = 3

    merchant_ctx = MerchantContext(merchant)
    trigger_ctx = TriggerContext(trigger)
    decision = decide(merchant_ctx, trigger_ctx, None, already_suppressed=True)

    assert decision.send is False
    assert decision.dominant_signal == "suppressed"


def test_golden_6_missing_owner_name_greets_by_merchant_name_not_invented() -> None:
    """Merchant Fit: no owner_first_name in context. The composer must greet by merchant_name
    and must never invent a plausible-sounding owner name to fill the gap."""
    merchant = copy.deepcopy(_merchant_by_id("m_005_pizzajunction_restaurant_delhi"))
    merchant["identity"] = {k: v for k, v in merchant["identity"].items() if k != "owner_first_name"}
    trigger = copy.deepcopy(_festival_trigger_seed())
    trigger["id"] = "trg_golden_6"
    trigger["merchant_id"] = merchant["merchant_id"]
    trigger["payload"]["days_until"] = 3

    decision, body, brief = _run(merchant, _restaurant_category(), trigger)

    assert decision.send is True
    assert body is not None
    assert brief is not None
    assert brief.owner_first_name is None
    fit = merchant_fit_signals(body, brief)
    assert fit["greets_by_correct_subject"], body
    assert "Suresh" not in body  # the real owner_first_name from the unmodified fixture — must not leak in


def test_golden_7_expired_offer_excluded_category_fit_holds() -> None:
    """Merchant Fit + grounding: an expired offer must not become a fact, and the category's
    taboo vocabulary must stay absent from the composed message."""
    merchant = copy.deepcopy(_merchant_by_id("m_005_pizzajunction_restaurant_delhi"))
    merchant["offers"][0]["status"] = "expired"
    trigger = copy.deepcopy(_festival_trigger_seed())
    trigger["id"] = "trg_golden_7"
    trigger["merchant_id"] = merchant["merchant_id"]
    trigger["payload"]["days_until"] = 3

    decision, body, brief = _run(merchant, _restaurant_category(), trigger)

    assert decision.send is True  # timing alone still clears threshold
    assert decision.cta == "open_ended"
    assert body is not None
    assert "Buy 1 Pizza Get 1 Free" not in body
    report = evaluate(body, brief)
    assert report.passes_grounding, report.grounding_violations
    assert report.passes_category_fit, report.category_fit_violations


def test_golden_8_urgent_festival_single_clear_cta_no_invented_urgency() -> None:
    """Engagement Compulsion: festival 1 day away is real urgency already present in the facts —
    the composed message must have exactly one clear CTA and no generic filler phrases stacked
    on top of the real urgency."""
    merchant = _merchant_by_id("m_005_pizzajunction_restaurant_delhi")
    trigger = copy.deepcopy(_festival_trigger_seed())
    trigger["id"] = "trg_golden_8"
    trigger["merchant_id"] = merchant["merchant_id"]
    trigger["payload"]["days_until"] = 1

    decision, body, brief = _run(merchant, _restaurant_category(), trigger)

    assert decision.send is True
    assert body is not None
    engagement = engagement_signals(body, brief)
    assert engagement["single_clear_cta"], engagement
    assert engagement["no_generic_filler"], engagement


def test_golden_9_weak_timing_but_offer_present_still_clears_threshold() -> None:
    """Decision Quality: a festival just past the timeliness window (15 days out — outside the
    14-day window the scorer treats as 'close') is a weak signal on its own, but a concrete
    active offer is enough to still clear the send threshold, just with lower confidence than a
    close festival with the same offer (golden_1's ~0.88 for reference)."""
    merchant = _merchant_by_id("m_005_pizzajunction_restaurant_delhi")
    trigger = copy.deepcopy(_festival_trigger_seed())
    trigger["id"] = "trg_golden_9"
    trigger["merchant_id"] = merchant["merchant_id"]
    trigger["payload"]["days_until"] = 15

    decision, body, _brief = _run(merchant, _restaurant_category(), trigger)

    assert decision.send is True
    assert decision.confidence < 0.65
    assert body is not None


def test_golden_10_unrelated_merchant_signals_do_not_perturb_the_decision() -> None:
    """Decision robustness: merchant.signals carries seemingly contradictory entries
    ("trial_ending_soon" alongside "engaged_in_last_48h"). The decision must be driven only by
    the documented inputs (category relevance, offers, trigger timing) — this also surfaces,
    honestly, that `signals` isn't consumed by scoring at all today, which is a legitimate future
    Decision Quality question (see the final report), not something fixed in this evaluation pass.
    """
    merchant = copy.deepcopy(_merchant_by_id("m_005_pizzajunction_restaurant_delhi"))
    merchant["signals"] = ["trial_ending_soon", "engaged_in_last_48h", "ctr_below_peer_median"]
    trigger = copy.deepcopy(_festival_trigger_seed())
    trigger["id"] = "trg_golden_10"
    trigger["merchant_id"] = merchant["merchant_id"]
    trigger["payload"]["days_until"] = 3

    decision, body, _brief = _run(merchant, _restaurant_category(), trigger)

    assert decision.send is True
    assert decision.dominant_signal == "festival:Diwali"
    assert decision.cta == "binary_yes_no"
    assert body is not None
