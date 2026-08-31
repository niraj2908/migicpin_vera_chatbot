"""Golden cases for the pharmacies + chronic_refill_due generator (option B, targeted trigger-
coverage expansion). Anchored on real seed data: m_009_apollo_pharmacy_jaipur /
c_013_grandfather_for_m009 / trg_019_chronic_refill_grandfather -- matches Case Study 10 closely
(49/50 score, examples/case-studies.md): real molecule list (metformin, atorvastatin,
telmisartan), real run-out date, real senior-citizen/delivery-address context, real consent scope
including "refill_reminders".

Deliberately never states a total/savings figure (unlike the illustrative case-study message,
which computes ₹1,420/₹1,240 saved) -- no per-medication price field exists anywhere in the real
contract data, so that number in the case study is not reproducible from real evidence and is not
attempted here. Also never reads customer.relationship.chronic_conditions (diagnosis-shaped data)
-- see test_conservative_health_claims.py for that specific guarantee.
"""

import copy
import json
from datetime import UTC, datetime
from pathlib import Path

from vera.decision.compiler import decide
from vera.domain.context import CategoryContext, CustomerContext, MerchantContext, TriggerContext
from vera.generation.brief import build_brief
from vera.generation.composer import TemplateComposer
from vera.generation.firewall import validate
from vera.pipeline import compose_and_validate

DATASET_DIR = Path(__file__).parent.parent.parent / "docs" / "challenge-package" / "dataset"
_COMPOSER = TemplateComposer()


def _pharmacies_category() -> dict:
    return json.loads((DATASET_DIR / "categories" / "pharmacies.json").read_text())


def _merchant() -> dict:
    merchants = json.loads((DATASET_DIR / "merchants_seed.json").read_text())["merchants"]
    return copy.deepcopy(next(m for m in merchants if m["merchant_id"] == "m_009_apollo_pharmacy_jaipur"))


def _trigger() -> dict:
    triggers = json.loads((DATASET_DIR / "triggers_seed.json").read_text())["triggers"]
    return copy.deepcopy(next(t for t in triggers if t["kind"] == "chronic_refill_due"))


def _customer() -> dict:
    customers = json.loads((DATASET_DIR / "customers_seed.json").read_text())["customers"]
    return copy.deepcopy(next(c for c in customers if c["customer_id"] == "c_013_grandfather_for_m009"))


def _run(
    merchant_raw: dict,
    trigger_raw: dict,
    customer_raw: dict | None,
    category_raw: dict,
    *,
    already_suppressed: bool = False,
    now=None,
):
    merchant = MerchantContext(merchant_raw)
    category = CategoryContext(category_raw)
    trigger = TriggerContext(trigger_raw)
    customer = CustomerContext(customer_raw) if customer_raw is not None else None
    decision = decide(merchant, trigger, customer, already_suppressed=already_suppressed, category=category, now=now)
    body, brief = None, None
    if decision.send:
        brief = build_brief(decision, merchant, category, customer)
        body = compose_and_validate(brief, _COMPOSER).message
    return decision, body, brief


def test_golden_1_official_seed_data_matches_case_study_10() -> None:
    decision, body, _brief = _run(_merchant(), _trigger(), _customer(), _pharmacies_category())

    assert decision.send is True
    assert decision.dominant_signal == "chronic_refill:c_013_grandfather_for_m009"
    assert decision.send_as == "merchant_on_behalf"
    assert decision.cta == "binary_confirm_cancel"
    assert body is not None
    assert body.startswith("Mr. Sharma")
    assert "Apollo Health Plus Pharmacy" in body
    assert "metformin" in body and "atorvastatin" in body and "telmisartan" in body
    assert "2026-04-28" in body
    ok, reasons = validate(body, _brief)
    assert ok, reasons


def test_golden_2_no_customer_context_pushed_does_not_send() -> None:
    decision, body, _brief = _run(_merchant(), _trigger(), None, _pharmacies_category())
    assert decision.send is False
    assert body is None


def test_golden_3_missing_consent_scope_does_not_send() -> None:
    customer = _customer()
    customer["consent"]["scope"] = ["delivery_notifications"]  # refill_reminders removed
    decision, body, _brief = _run(_merchant(), _trigger(), customer, _pharmacies_category())
    assert decision.send is False
    assert body is None


def test_golden_4_empty_molecule_list_does_not_send() -> None:
    trigger = _trigger()
    trigger["payload"]["molecule_list"] = []
    decision, body, _brief = _run(_merchant(), trigger, _customer(), _pharmacies_category())
    assert decision.send is False
    assert body is None


def test_golden_5_missing_molecule_list_does_not_send() -> None:
    trigger = _trigger()
    del trigger["payload"]["molecule_list"]
    decision, body, _brief = _run(_merchant(), trigger, _customer(), _pharmacies_category())
    assert decision.send is False
    assert body is None


def test_golden_6_malformed_molecule_list_type_does_not_crash() -> None:
    trigger = _trigger()
    trigger["payload"]["molecule_list"] = "metformin"  # not a list
    decision, _body, _brief = _run(_merchant(), trigger, _customer(), _pharmacies_category())
    assert decision.send is False


def test_golden_7_no_delivery_address_saved_falls_back_to_open_ended() -> None:
    trigger = _trigger()
    trigger["payload"]["delivery_address_saved"] = False
    decision, body, _brief = _run(_merchant(), trigger, _customer(), _pharmacies_category())
    assert decision.send is True
    assert decision.cta == "open_ended"
    assert body is not None
    assert "home delivery address already on file" not in body


def test_golden_8_no_stock_runs_out_date_still_sends_without_fabricating_it() -> None:
    trigger = _trigger()
    del trigger["payload"]["stock_runs_out_iso"]
    decision, body, _brief = _run(_merchant(), trigger, _customer(), _pharmacies_category())
    assert decision.send is True
    assert body is not None
    assert "2026-04-28" not in body
    assert "metformin" in body  # core molecule fact still present


def test_golden_9_no_active_offer_omits_offer_but_still_sends() -> None:
    merchant = _merchant()
    merchant["offers"] = []
    decision, body, _brief = _run(merchant, _trigger(), _customer(), _pharmacies_category())
    assert decision.send is True
    assert body is not None
    assert "Free Home Delivery" not in body
    assert "Senior Citizen" not in body
    assert "metformin" in body


def test_golden_10_suppression_blocks_a_repeat_send() -> None:
    decision, body, _brief = _run(_merchant(), _trigger(), _customer(), _pharmacies_category(), already_suppressed=True)
    assert decision.send is False
    assert decision.dominant_signal == "suppressed"
    assert body is None


def test_golden_11_expired_refill_trigger_does_not_send() -> None:
    trigger = _trigger()
    assert trigger["expires_at"] == "2026-04-28T00:00:00+05:30"
    now = datetime(2026, 4, 29, tzinfo=UTC)  # after run-out/expiry
    decision, body, _brief = _run(_merchant(), trigger, _customer(), _pharmacies_category(), now=now)
    assert decision.send is False
    assert decision.dominant_signal == "stale_trigger"
    assert body is None


def test_golden_12_before_the_run_out_date_still_sends() -> None:
    trigger = _trigger()
    now = datetime(2026, 4, 20, tzinfo=UTC)
    decision, body, _brief = _run(_merchant(), trigger, _customer(), _pharmacies_category(), now=now)
    assert decision.send is True
    assert body is not None


def test_golden_13_no_total_or_savings_figure_is_ever_stated() -> None:
    """The illustrative case-study message computes a ₹1,420 total / ₹240 saved -- no per-
    medication price field exists in the real contract data to ground that, so this generator
    must never produce it."""
    _decision, body, _brief = _run(_merchant(), _trigger(), _customer(), _pharmacies_category())
    assert body is not None
    assert "1,420" not in body and "1420" not in body
    assert "240 saved" not in body.lower()
