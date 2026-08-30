"""Tests for renewal_due -- dentists x renewal_due, anchored on real seed data:
m_002_bharat_dentist_mumbai / trg_005_renewal_due_bharat.

Feasibility evidence: real, documented internal trigger kind, fully self-contained real payload
(days_remaining, plan, renewal_amount), merchant-scoped (the merchant's own subscription, not a
customer-facing pitch), no consent gate. Reuses TIMELINESS_WINDOW_DAYS (14) rather than a new
invented constant, the same window festival_upcoming already applies to its own days_until field.
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
    return copy.deepcopy(next(m for m in merchants if m["merchant_id"] == "m_002_bharat_dentist_mumbai"))


def _trigger() -> dict:
    triggers = json.loads((DATASET_DIR / "triggers_seed.json").read_text())["triggers"]
    return copy.deepcopy(next(t for t in triggers if t["kind"] == "renewal_due"))


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
    assert decision.dominant_signal == "renewal_due"
    assert decision.action_type == "renewal_reminder"
    assert decision.send_as == "vera"
    assert body is not None
    assert "Pro" in body
    assert "12 day" in body
    assert "4999" in body


def test_counterfactual_too_early_does_not_send() -> None:
    trigger = _trigger()
    trigger["payload"]["days_remaining"] = 45  # outside the 14-day timeliness window
    decision, body, _brief = _run(_merchant(), trigger, _dentists_category())
    assert decision.send is False
    assert body is None


def test_counterfactual_negative_days_remaining_does_not_send() -> None:
    """Already past due -- contradictory/stale data for this trigger kind, not a fresh reminder."""
    trigger = _trigger()
    trigger["payload"]["days_remaining"] = -3
    decision, _body, _brief = _run(_merchant(), trigger, _dentists_category())
    assert decision.send is False


def test_missing_days_remaining_does_not_send() -> None:
    trigger = _trigger()
    del trigger["payload"]["days_remaining"]
    decision, _body, _brief = _run(_merchant(), trigger, _dentists_category())
    assert decision.send is False


def test_missing_plan_does_not_send() -> None:
    trigger = _trigger()
    del trigger["payload"]["plan"]
    decision, _body, _brief = _run(_merchant(), trigger, _dentists_category())
    assert decision.send is False


def test_missing_renewal_amount_still_sends_without_inventing_a_price() -> None:
    trigger = _trigger()
    del trigger["payload"]["renewal_amount"]
    decision, body, _brief = _run(_merchant(), trigger, _dentists_category())
    assert decision.send is True
    assert body is not None
    assert "4999" not in body


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
    t2["payload"]["renewal_amount"] = 111
    _d1, body1, _ = _run(m1, t1, _dentists_category())
    _d2, body2, _ = _run(m2, t2, _dentists_category())
    assert "111" not in (body1 or "")
    assert "4999" not in (body2 or "")


def test_malformed_days_remaining_type_does_not_crash() -> None:
    trigger = _trigger()
    trigger["payload"]["days_remaining"] = "soon"
    decision, _body, _brief = _run(_merchant(), trigger, _dentists_category())
    assert decision.send is False  # treated as missing, not crashed


def test_no_invented_amounts_beyond_the_real_values() -> None:
    _decision, body, _brief = _run(_merchant(), _trigger(), _dentists_category())
    assert body is not None
    import re

    amounts = set(re.findall(r"₹\s*(\d[\d,]*)", body))
    assert amounts <= {"4999"}
