"""Golden cases for the dentists + recall_due expansion — first `multi_choice_slot` CTA usage,
and the first category beyond restaurants/gyms.

Anchored on real seed data: m_001_drmeera_dentist_delhi / c_001_priya_for_m001, matching Case
Study 2 closely (49/50 score) — real 6-month cleaning recall, real active offer ("Dental
Cleaning @ ₹299"), real available slots, real consent scope including "recall_reminders".

No date arithmetic is performed by the opportunity generator — see opportunity.py's own comment
for why trusting the trigger kind's own classification (mirroring customer_lapsed_hard) was
chosen over computing "months since last visit" ourselves, which would need a `now` input no
generator has required before.

"category mismatch" and "staleness" counterfactuals don't apply here for the same structural
reasons documented in test_golden_gyms_winback.py (no category-relevance field on this trigger;
no magnitude field of ours to make stale — trusting the upstream classification).
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


def _dentists_category() -> dict:
    return json.loads((DATASET_DIR / "categories" / "dentists.json").read_text())


def _merchant() -> dict:
    merchants = json.loads((DATASET_DIR / "merchants_seed.json").read_text())["merchants"]
    return copy.deepcopy(next(m for m in merchants if m["merchant_id"] == "m_001_drmeera_dentist_delhi"))


def _trigger() -> dict:
    triggers = json.loads((DATASET_DIR / "triggers_seed.json").read_text())["triggers"]
    return copy.deepcopy(next(t for t in triggers if t["kind"] == "recall_due"))


def _customer() -> dict:
    customers = json.loads((DATASET_DIR / "customers_seed.json").read_text())["customers"]
    return copy.deepcopy(next(c for c in customers if c["customer_id"] == "c_001_priya_for_m001"))


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


def test_golden_1_official_seed_data_matches_case_study_2() -> None:
    decision, body, _brief = _run(_merchant(), _trigger(), _customer(), _dentists_category())

    assert decision.send is True
    assert decision.dominant_signal == "recall_due"
    assert decision.send_as == "merchant_on_behalf"
    assert decision.cta == "multi_choice_slot"
    assert body is not None
    assert body.startswith("Priya")  # greets the customer, not "Meera" or "Dr. Meera's Dental Clinic"
    assert "Dr. Meera's Dental Clinic" in body  # merchant identifies itself
    assert "Wed 5 Nov, 6pm" in body
    assert "Thu 6 Nov, 5pm" in body
    assert "₹299" in body


def test_golden_2_no_customer_context_pushed_does_not_send() -> None:
    decision, body, _brief = _run(_merchant(), _trigger(), None, _dentists_category())
    assert decision.send is False
    assert body is None


def test_golden_3_missing_consent_scope_does_not_send() -> None:
    customer = _customer()
    customer["consent"]["scope"] = ["appointment_reminders"]  # recall_reminders removed
    decision, body, _brief = _run(_merchant(), _trigger(), customer, _dentists_category())
    assert decision.send is False
    assert body is None


def test_golden_4_no_available_slots_falls_back_to_open_ended() -> None:
    trigger = _trigger()
    trigger["payload"]["available_slots"] = []
    decision, body, _brief = _run(_merchant(), trigger, _customer(), _dentists_category())
    assert decision.send is True
    assert decision.cta == "open_ended"
    assert body is not None
    assert "Wed 5 Nov" not in body


def test_golden_5_suppression_blocks_a_repeat_send() -> None:
    decision, body, _brief = _run(_merchant(), _trigger(), _customer(), _dentists_category(), already_suppressed=True)
    assert decision.send is False
    assert decision.dominant_signal == "suppressed"
    assert body is None


def test_golden_6_no_active_offer_omits_offer_but_still_sends() -> None:
    merchant = _merchant()
    merchant["offers"] = []
    decision, body, _brief = _run(merchant, _trigger(), _customer(), _dentists_category())
    assert decision.send is True
    assert body is not None
    assert "₹299" not in body
    assert "Wed 5 Nov, 6pm" in body  # slots are independent of offer presence


def test_golden_7_changed_slots_update_the_facts_not_the_old_ones() -> None:
    trigger = _trigger()
    trigger["payload"]["available_slots"] = [{"iso": "2026-12-01T10:00:00+05:30", "label": "Tue 1 Dec, 10am"}]
    decision, body, _brief = _run(_merchant(), trigger, _customer(), _dentists_category())
    assert decision.send is True
    assert body is not None
    assert "Tue 1 Dec, 10am" in body
    assert "Wed 5 Nov, 6pm" not in body


def test_golden_8_category_taboo_vocabulary_is_enforced_for_dentists() -> None:
    """Category Fit: dentists' real taboo list ("guaranteed", "100% safe", "completely cure")
    differs from restaurants'/gyms' — confirms our existing firewall plumbing actually applies
    the right category's taboo list, not a hardcoded one."""
    from vera.generation.firewall import validate

    _decision, _body, brief = _run(_merchant(), _trigger(), _customer(), _dentists_category())
    assert brief is not None
    assert "guaranteed" in [t.lower() for t in brief.vocab_taboo]

    hallucinated = "Priya, this is Dr. Meera's Dental Clinic. Guaranteed 100% safe results! Reply 1 or 2."
    ok, reasons = validate(hallucinated, brief)
    assert not ok
    assert any("taboo" in r for r in reasons)
