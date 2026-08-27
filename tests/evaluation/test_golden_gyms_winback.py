"""Golden cases for the gyms + customer_lapsed_hard expansion — the first customer-scoped
opportunity generator (send_as=merchant_on_behalf, consent-gated).

Anchored on real seed data: m_007_powerhouse_gym_bangalore (already used for seasonal_perf_dip)
and c_010_rashmi_for_m007, which is an exact match to Case Study 8 (57 days lapsed, weight-loss
focus, real consent scope including "winback_offers") — the only case study with a perfect
50/50 score, confirmed by direct comparison before writing any code.

Two requested counterfactuals don't apply here, documented rather than forced:
- "category mismatch": this trigger carries no category-relevance concept (unlike
  festival_upcoming) — it's pushed for a specific merchant_id, whose category is whatever it is.
- "staleness": customer_lapsed_hard is itself the judge/data pipeline's classification that this
  lapse is significant (as opposed to a milder "_soft" sibling kind) — there's no separate
  magnitude field of ours to make stale; trusting that upstream classification rather than
  reinventing a threshold is the evidence-disciplined choice (see opportunity.py's own comment).
"""

import copy
import json
from pathlib import Path

from vera.decision.compiler import decide
from vera.domain.context import CategoryContext, CustomerContext, MerchantContext, TriggerContext
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
    return copy.deepcopy(next(t for t in triggers if t["kind"] == "customer_lapsed_hard"))


def _customer() -> dict:
    customers = json.loads((DATASET_DIR / "customers_seed.json").read_text())["customers"]
    return copy.deepcopy(next(c for c in customers if c["customer_id"] == "c_010_rashmi_for_m007"))


def _run(merchant_raw: dict, trigger_raw: dict, customer_raw: dict | None, category_raw: dict, *, already_suppressed: bool = False):
    merchant = MerchantContext(merchant_raw)
    category = CategoryContext(category_raw)
    trigger = TriggerContext(trigger_raw)
    customer = CustomerContext(customer_raw) if customer_raw is not None else None
    decision = decide(merchant, trigger, customer, already_suppressed=already_suppressed, category=category)
    body, brief = None, None
    if decision.send:
        brief = build_brief(decision, merchant, category, customer)
        body = compose_and_validate(brief, _COMPOSER).message
    return decision, body, brief


def test_golden_1_official_seed_data_matches_case_study_8() -> None:
    decision, body, _brief = _run(_merchant(), _trigger(), _customer(), _gyms_category())

    assert decision.send is True
    assert decision.dominant_signal == "customer_winback"
    assert decision.send_as == "merchant_on_behalf"  # first real exercise of this code path
    assert decision.cta == "binary_yes_no"
    assert body is not None
    assert body.startswith("Rashmi")  # greets the CUSTOMER, not the merchant owner "Karthik"
    assert "57" in body
    assert "weight loss" in body


def test_golden_2_no_customer_context_pushed_does_not_send() -> None:
    """Customer-scoped and we have no customer data at all — can't verify consent, so we must
    not act, not guess."""
    decision, body, _brief = _run(_merchant(), _trigger(), None, _gyms_category())
    assert decision.send is False
    assert body is None


def test_golden_3_missing_consent_scope_does_not_send() -> None:
    """The real, hard gate this expansion introduces: explicit consent for winback-type outreach
    is required, not assumed."""
    customer = _customer()
    customer["consent"]["scope"] = ["renewal_reminders"]  # winback_offers removed
    decision, body, _brief = _run(_merchant(), _trigger(), customer, _gyms_category())
    assert decision.send is False
    assert body is None


def test_golden_4_no_active_offer_still_sends_open_ended() -> None:
    merchant = _merchant()
    merchant["offers"] = []
    decision, body, _brief = _run(merchant, _trigger(), _customer(), _gyms_category())
    assert decision.send is True
    assert decision.cta == "open_ended"
    assert body is not None
    assert "3 FREE Trial Classes" not in body


def test_golden_5_suppression_blocks_a_repeat_send() -> None:
    decision, body, _brief = _run(_merchant(), _trigger(), _customer(), _gyms_category(), already_suppressed=True)
    assert decision.send is False
    assert decision.dominant_signal == "suppressed"
    assert body is None


def test_golden_6_changed_offer_updates_the_fact_not_the_old_one() -> None:
    merchant = _merchant()
    merchant["offers"][0]["title"] = "First Month @ ₹499"
    decision, body, _brief = _run(merchant, _trigger(), _customer(), _gyms_category())
    assert decision.send is True
    assert body is not None
    assert "First Month @ ₹499" in body
    assert "3 FREE Trial Classes" not in body


def test_golden_7_changed_customer_previous_focus_updates_the_fact() -> None:
    customer = _customer()
    customer["preferences"]["training_focus"] = "strength"
    trigger = _trigger()
    trigger["payload"]["previous_focus"] = "strength"
    decision, body, _brief = _run(_merchant(), trigger, customer, _gyms_category())
    assert decision.send is True
    assert body is not None
    assert "strength" in body
    assert "weight loss" not in body


def test_golden_8_no_guilt_trip_tone_rule_is_present_in_the_shared_prompt() -> None:
    """Characterization test: the composition rule this expansion introduced (Case Study 8's
    explicit "no judgment" framing) is present in the shared system prompt, not gym-specific
    wording — applies to any lapsed-customer message in any category."""
    from vera.generation.composer.shared import SYSTEM_PROMPT

    assert "guilt-trip" in SYSTEM_PROMPT.lower()
