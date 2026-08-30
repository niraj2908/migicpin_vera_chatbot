"""P1 regression tests (hostile-audit finding #1): festival_upcoming previously allowed a
concrete active offer alone to push an arbitrarily distant festival over SEND_THRESHOLD, because
out-of-window days_until fell back to a fixed, non-decaying "weak but nonzero" score contribution
with no upper bound. Reproduced with the exact real seed trigger (188 days out,
m_003_studio11_salon_hyderabad, which has real active offers) that the audit used.

Fix: _festival_opportunity() now hard-gates on days_until being inside TIMELINESS_WINDOW_DAYS --
the same constant and the same pattern renewal_due already uses for its own days_remaining field
(opportunity.py's own comment: "worth mentioning inside this window"). No new threshold invented.
"""

import copy
import json
from pathlib import Path

from vera.decision.compiler import decide
from vera.decision.opportunity import TIMELINESS_WINDOW_DAYS, generate_opportunities
from vera.domain.context import CategoryContext, MerchantContext, TriggerContext
from vera.generation.brief import build_brief
from vera.generation.composer import TemplateComposer
from vera.pipeline import compose_and_validate

DATASET_DIR = Path(__file__).parent.parent.parent / "docs" / "challenge-package" / "dataset"
_COMPOSER = TemplateComposer()


def _salons_category() -> dict:
    return json.loads((DATASET_DIR / "categories" / "salons.json").read_text())


def _real_festival_merchant() -> dict:
    """The exact real merchant the audit's failing seed trigger belongs to -- has real active
    offers, which is precisely what let the old code rescue a 188-day-out send."""
    merchants = json.loads((DATASET_DIR / "merchants_seed.json").read_text())["merchants"]
    return copy.deepcopy(next(m for m in merchants if m["merchant_id"] == "m_003_studio11_salon_hyderabad"))


def _real_festival_trigger() -> dict:
    triggers = json.loads((DATASET_DIR / "triggers_seed.json").read_text())["triggers"]
    return copy.deepcopy(next(t for t in triggers if t["kind"] == "festival_upcoming"))


def _run(merchant_raw: dict, trigger_raw: dict, category_raw: dict):
    merchant = MerchantContext(merchant_raw)
    category = CategoryContext(category_raw)
    trigger = TriggerContext(trigger_raw)
    decision = decide(merchant, trigger, None, category=category)
    body, brief = None, None
    if decision.send:
        brief = build_brief(decision, merchant, category, None)
        body = compose_and_validate(brief, _COMPOSER).message
    return decision, body, brief


# --- the exact real failing case the audit found ------------------------------------------------


def test_the_exact_real_seed_case_no_longer_sends() -> None:
    """188 days out, real merchant, real offers ("Haircut @ 99", "Hair Spa @ 499") -- the precise
    scenario the hostile audit reproduced. Must now refuse to send, offer or no offer."""
    merchant = _real_festival_merchant()
    assert merchant.get("offers")  # ground the premise: this merchant genuinely has active offers
    trigger = _real_festival_trigger()
    assert trigger["payload"]["days_until"] == 188  # ground the premise: real, unmodified value

    decision, body, _brief = _run(merchant, trigger, _salons_category())

    assert decision.send is False
    assert decision.dominant_signal == "no_strong_opportunity"
    assert body is None


def test_the_exact_real_seed_case_generator_returns_none_directly() -> None:
    """Same case, checked at the generator level: no Opportunity object is produced at all for
    the out-of-window trigger, not merely one that scores below threshold."""
    merchant = MerchantContext(_real_festival_merchant())
    trigger = TriggerContext(_real_festival_trigger())
    opportunities = generate_opportunities(merchant, trigger, None)
    assert all(o.name != "festival:Diwali" for o in opportunities)


# --- clearly upcoming case still sends -----------------------------------------------------------


def test_clearly_upcoming_festival_with_offer_still_sends() -> None:
    merchant = _real_festival_merchant()
    trigger = _real_festival_trigger()
    trigger["payload"]["days_until"] = 3

    decision, body, _brief = _run(merchant, trigger, _salons_category())

    assert decision.send is True
    assert decision.action_type == "festival_campaign"
    assert body is not None
    assert "3 day(s) away" in body


def test_clearly_upcoming_festival_without_offer_still_sends_weaker() -> None:
    merchant = _real_festival_merchant()
    merchant["offers"] = []
    trigger = _real_festival_trigger()
    trigger["payload"]["days_until"] = 3

    decision, body, _brief = _run(merchant, trigger, _salons_category())

    assert decision.send is True
    assert body is not None


# --- deterministic boundary --------------------------------------------------------------------


def test_exactly_at_the_window_boundary_sends() -> None:
    """days_until == TIMELINESS_WINDOW_DAYS (14) is INSIDE the closed interval [0, 14] -- must
    still send, same boundary convention renewal_due already uses (`0 <= days_remaining <= 14`)."""
    merchant = _real_festival_merchant()
    trigger = _real_festival_trigger()
    trigger["payload"]["days_until"] = TIMELINESS_WINDOW_DAYS
    assert TIMELINESS_WINDOW_DAYS == 14  # pin the real constant value this test relies on

    decision, body, _brief = _run(merchant, trigger, _salons_category())

    assert decision.send is True
    assert body is not None


def test_one_day_past_the_window_boundary_does_not_send() -> None:
    """days_until == TIMELINESS_WINDOW_DAYS + 1 (15) is the exact case the previous
    "offer rescues it" behavior covered -- now must not send, offer or no offer."""
    merchant = _real_festival_merchant()
    trigger = _real_festival_trigger()
    trigger["payload"]["days_until"] = TIMELINESS_WINDOW_DAYS + 1

    decision, body, _brief = _run(merchant, trigger, _salons_category())

    assert decision.send is False
    assert body is None


def test_zero_days_until_the_festival_is_today_sends() -> None:
    merchant = _real_festival_merchant()
    trigger = _real_festival_trigger()
    trigger["payload"]["days_until"] = 0

    decision, body, _brief = _run(merchant, trigger, _salons_category())

    assert decision.send is True
    assert body is not None


def test_negative_days_until_a_festival_already_passed_does_not_send() -> None:
    """A negative days_until (the festival already happened) is contradictory/stale data for
    this trigger kind, not a fresh signal -- same discipline renewal_due already applies to a
    negative days_remaining."""
    merchant = _real_festival_merchant()
    trigger = _real_festival_trigger()
    trigger["payload"]["days_until"] = -3

    decision, body, _brief = _run(merchant, trigger, _salons_category())

    assert decision.send is False
    assert body is None


def test_missing_days_until_does_not_send() -> None:
    """No timing evidence at all -- same hard-gate discipline every other generator in this file
    already applies to missing required fields."""
    merchant = _real_festival_merchant()
    trigger = _real_festival_trigger()
    del trigger["payload"]["days_until"]

    decision, body, _brief = _run(merchant, trigger, _salons_category())

    assert decision.send is False
    assert body is None


def test_malformed_days_until_type_does_not_crash() -> None:
    merchant = _real_festival_merchant()
    trigger = _real_festival_trigger()
    trigger["payload"]["days_until"] = "next month"

    decision, body, _brief = _run(merchant, trigger, _salons_category())

    assert decision.send is False  # treated as missing, not crashed
    assert body is None


# --- no regression in unrelated trigger types ----------------------------------------------------


def test_perf_dip_unaffected_by_the_festival_timing_fix() -> None:
    dentists_category = json.loads((DATASET_DIR / "categories" / "dentists.json").read_text())
    merchant = next(
        m for m in json.loads((DATASET_DIR / "merchants_seed.json").read_text())["merchants"]
        if m["merchant_id"] == "m_002_bharat_dentist_mumbai"
    )
    trigger = next(
        t for t in json.loads((DATASET_DIR / "triggers_seed.json").read_text())["triggers"]
        if t["kind"] == "perf_dip"
    )
    decision, body, _brief = _run(copy.deepcopy(merchant), copy.deepcopy(trigger), dentists_category)
    assert decision.send is True
    assert body is not None
    assert "calls down 50%" in body


def test_milestone_reached_and_curious_ask_due_unaffected_by_the_festival_timing_fix() -> None:
    restaurants_category = json.loads((DATASET_DIR / "categories" / "restaurants.json").read_text())
    salons_category = _salons_category()

    milestone_merchant = next(
        m for m in json.loads((DATASET_DIR / "merchants_seed.json").read_text())["merchants"]
        if m["merchant_id"] == "m_006_southindiancafe_restaurant_bangalore"
    )
    milestone_trigger = next(
        t for t in json.loads((DATASET_DIR / "triggers_seed.json").read_text())["triggers"]
        if t["kind"] == "milestone_reached"
    )
    d1, body1, _ = _run(copy.deepcopy(milestone_merchant), copy.deepcopy(milestone_trigger), restaurants_category)
    assert d1.send is True
    assert body1 is not None
    assert "peer average" in body1.lower()

    curious_merchant = _real_festival_merchant()  # same real merchant as curious_ask_due's own seed
    curious_trigger = next(
        t for t in json.loads((DATASET_DIR / "triggers_seed.json").read_text())["triggers"]
        if t["kind"] == "curious_ask_due"
    )
    d2, body2, _ = _run(curious_merchant, copy.deepcopy(curious_trigger), salons_category)
    assert d2.send is True
    assert body2 is not None
    assert "What service in demand this week?" in body2


def test_review_theme_emerged_unaffected_by_the_festival_timing_fix() -> None:
    restaurants_category = json.loads((DATASET_DIR / "categories" / "restaurants.json").read_text())
    merchant = next(
        m for m in json.loads((DATASET_DIR / "merchants_seed.json").read_text())["merchants"]
        if m["merchant_id"] == "m_005_pizzajunction_restaurant_delhi"
    )
    trigger = next(
        t for t in json.loads((DATASET_DIR / "triggers_seed.json").read_text())["triggers"]
        if t["kind"] == "review_theme_emerged"
    )
    decision, body, _brief = _run(copy.deepcopy(merchant), copy.deepcopy(trigger), restaurants_category)
    assert decision.send is True
    assert body is not None
    assert "customers have mentioned delivery late" in body
