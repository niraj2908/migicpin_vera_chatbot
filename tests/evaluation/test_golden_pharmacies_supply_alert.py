"""Golden cases for pharmacies + supply_alert — the first generator to consult
conversation_history as a decision input, and the first to deliberately withhold a number
(a filtered "affected customer count") that Case Study 9 uses but our real dataset cannot
support without fabricating it.

Anchored on real seed data: m_009_apollo_pharmacy_jaipur, matching Case Study 9 closely (50/50)
— real atorvastatin recall, real batch numbers, real manufacturer, real chronic_rx_count (240).

Critical real-data finding, verified before writing any code: the merchant's own unmodified
conversation_history already contains a prior Vera message about this exact recall, which the
merchant replied "Yes send me the list please" to (engagement: intent_action). Composing a
fresh identical pitch on the real, unmodified scenario would be a real repetition/Decision
Quality failure — golden_1 below proves the generator correctly recognizes this and does not
re-send; golden_2 proves it correctly does send when the same alert has genuinely not been
raised before.

"category mismatch" and "staleness" counterfactuals don't apply for the same structural reasons
documented in the other expansion test files: no category-relevance field on this trigger; no
magnitude field of ours to make stale (trusting the trigger kind's own existence, same pattern
as customer_lapsed_hard). "suppression" and "consent" don't apply either: this trigger is
merchant-scoped (no customer_id, no consent gate) — its equivalent hard gate is the
conversation-history check instead.
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


def _pharmacies_category() -> dict:
    return json.loads((DATASET_DIR / "categories" / "pharmacies.json").read_text())


def _merchant() -> dict:
    merchants = json.loads((DATASET_DIR / "merchants_seed.json").read_text())["merchants"]
    return copy.deepcopy(next(m for m in merchants if m["merchant_id"] == "m_009_apollo_pharmacy_jaipur"))


def _trigger() -> dict:
    triggers = json.loads((DATASET_DIR / "triggers_seed.json").read_text())["triggers"]
    return copy.deepcopy(next(t for t in triggers if t["kind"] == "supply_alert"))


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


def test_golden_1_official_seed_data_is_not_resent_because_already_discussed() -> None:
    """The real, completely unmodified seed data: conversation_history already shows this exact
    recall was raised and the merchant said yes. Must not send it again."""
    decision, body, _brief = _run(_merchant(), _trigger(), _pharmacies_category())
    assert decision.send is False
    assert decision.dominant_signal == "no_strong_opportunity"
    assert body is None


def test_golden_2_fresh_alert_never_discussed_sends_with_grounded_facts() -> None:
    """Same trigger/merchant, minus the prior conversation_history — a principled variation
    proving the generator fires correctly when the alert genuinely hasn't been raised yet."""
    merchant = _merchant()
    merchant["conversation_history"] = []
    decision, body, _brief = _run(merchant, _trigger(), _pharmacies_category())

    assert decision.send is True
    assert decision.dominant_signal == "supply_alert:atorvastatin"
    assert decision.action_type == "compliance_alert"
    assert decision.cta == "open_ended"
    assert decision.send_as == "vera"
    assert body is not None
    assert "AT2024-1102" in body
    assert "AT2024-1108" in body
    assert "MfrZ" in body
    assert "240" in body


def test_golden_3_never_invents_a_filtered_affected_customer_count() -> None:
    """The real dataset only gives a TOTAL chronic_rx_count, never a count filtered to which
    customers were dispensed these specific batches — Case Study 9's "22 of your customers"
    figure has no provenance in our data and must never be fabricated."""
    merchant = _merchant()
    merchant["conversation_history"] = []
    decision, body, _brief = _run(merchant, _trigger(), _pharmacies_category())
    assert body is not None
    assert "22" not in body
    assert all("22" not in fact for fact in decision.facts_allowed)


def test_golden_4_no_chronic_rx_customers_still_sends_but_weaker_signal() -> None:
    merchant = _merchant()
    merchant["conversation_history"] = []
    merchant["customer_aggregate"]["chronic_rx_count"] = 0
    with_customers = _run(_merchant() | {"conversation_history": []}, _trigger(), _pharmacies_category())[0]
    without_customers, body, _brief = _run(merchant, _trigger(), _pharmacies_category())

    assert without_customers.send is True  # a recall is still worth an FYI even with 0 known chronic-Rx customers
    assert without_customers.confidence <= with_customers.confidence
    assert body is not None
    assert "0 chronic-Rx customers" not in body  # falsy count omitted as a fact entirely, not stated as zero


def test_golden_5_changed_molecule_and_batches_update_the_facts() -> None:
    merchant = _merchant()
    merchant["conversation_history"] = []
    trigger = _trigger()
    trigger["payload"]["molecule"] = "metformin"
    trigger["payload"]["affected_batches"] = ["MF2025-0099"]
    decision, body, _brief = _run(merchant, trigger, _pharmacies_category())

    assert decision.send is True
    assert body is not None
    assert "metformin" in body
    assert "MF2025-0099" in body
    assert "atorvastatin" not in body
    assert "AT2024-1102" not in body


def test_golden_6_category_taboo_vocabulary_is_pharmacies_specific() -> None:
    from vera.generation.firewall import validate

    merchant = _merchant()
    merchant["conversation_history"] = []
    _decision, _body, brief = _run(merchant, _trigger(), _pharmacies_category())
    assert brief is not None
    assert "miracle cure" in [t.lower() for t in brief.vocab_taboo]

    hallucinated = "Ramesh, miracle cure available, 100% safe! Reply for details."
    ok, reasons = validate(hallucinated, brief)
    assert not ok
    assert any("taboo" in r for r in reasons)
