"""Golden cases for the dentists + regulation_change generator (option B, targeted trigger-
coverage expansion). Anchored on real seed data: m_001_drmeera_dentist_delhi /
trg_002_compliance_dci_radiograph / dentists.json's "d_2026W17_dci_radiograph" digest item
(kind="compliance"). No dedicated case study exists for this exact trigger kind in
examples/case-studies.md (Case Study 9 covers supply_alert, a different compliance-flavored
trigger) -- challenge-brief.md line 134 and engagement-design.md both name regulation_change as a
first-class trigger kind ("DCI radiograph dose limit revised"), and case-studies.md's own
cross-case pattern #1 cites "DCI circular" as the canonical source-citation example, so tone is
calibrated from those, not a full worked message.

Shares CategoryContext.digest_item()/kind-gate plumbing with research_digest -- see
opportunity.py's _resolve_digest_item() docstring. No consent gate: merchant-scoped, trigger.
customer_id is null in the real data.
"""

import copy
import json
from datetime import UTC, datetime
from pathlib import Path

from vera.decision.compiler import decide
from vera.domain.context import CategoryContext, MerchantContext, TriggerContext
from vera.generation.brief import build_brief
from vera.generation.composer import TemplateComposer
from vera.generation.firewall import validate
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
    return copy.deepcopy(next(t for t in triggers if t["kind"] == "regulation_change"))


def _run(merchant_raw: dict, trigger_raw: dict, category_raw: dict, *, already_suppressed: bool = False, now=None):
    merchant = MerchantContext(merchant_raw)
    category = CategoryContext(category_raw)
    trigger = TriggerContext(trigger_raw)
    decision = decide(merchant, trigger, None, already_suppressed=already_suppressed, category=category, now=now)
    body, brief = None, None
    if decision.send:
        brief = build_brief(decision, merchant, category, None)
        body = compose_and_validate(brief, _COMPOSER).message
    return decision, body, brief


def test_golden_1_official_seed_data_sends_a_grounded_compliance_alert() -> None:
    decision, body, _brief = _run(_merchant(), _trigger(), _dentists_category())

    assert decision.send is True
    assert decision.dominant_signal == "regulation_change:d_2026W17_dci_radiograph"
    assert decision.send_as == "vera"
    assert decision.cta == "open_ended"
    assert body is not None
    assert body.startswith("Meera")
    assert "DCI revised radiograph dose limits effective 2026-12-15" in body
    assert "2026-12-15" in body
    assert "Dental Council of India circular 2026-11-04" in body
    ok, reasons = validate(body, _brief)
    assert ok, reasons


def test_golden_2_missing_deadline_does_not_send() -> None:
    """Never falls back to trigger.expires_at as a substitute deadline claim -- see
    opportunity.py's own comment on why. Insufficient evidence, no send."""
    trigger = _trigger()
    del trigger["payload"]["deadline_iso"]
    decision, body, _brief = _run(_merchant(), trigger, _dentists_category())
    assert decision.send is False
    assert body is None


def test_golden_3_malformed_deadline_type_does_not_send() -> None:
    trigger = _trigger()
    trigger["payload"]["deadline_iso"] = 20261215
    decision, body, _brief = _run(_merchant(), trigger, _dentists_category())
    assert decision.send is False
    assert body is None


def test_golden_4_unresolvable_top_item_id_does_not_send() -> None:
    trigger = _trigger()
    trigger["payload"]["top_item_id"] = "d_does_not_exist"
    decision, body, _brief = _run(_merchant(), trigger, _dentists_category())
    assert decision.send is False
    assert body is None


def test_golden_5_digest_item_of_the_wrong_kind_does_not_send() -> None:
    """A regulation_change trigger pointing at the real "research"-kind item is a data-shape
    mismatch -- hard gate."""
    trigger = _trigger()
    trigger["payload"]["top_item_id"] = "d_2026W17_jida_fluoride"
    decision, body, _brief = _run(_merchant(), trigger, _dentists_category())
    assert decision.send is False
    assert body is None


def test_golden_6_missing_title_does_not_send() -> None:
    category = _dentists_category()
    for item in category["digest"]:
        if item["id"] == "d_2026W17_dci_radiograph":
            del item["title"]
    decision, body, _brief = _run(_merchant(), _trigger(), category)
    assert decision.send is False
    assert body is None


def test_golden_7_no_actionable_field_still_sends_without_inventing_one() -> None:
    category = _dentists_category()
    for item in category["digest"]:
        if item["id"] == "d_2026W17_dci_radiograph":
            del item["actionable"]
    decision, body, _brief = _run(_merchant(), _trigger(), category)
    assert decision.send is True
    assert body is not None
    assert "suggested action" not in body


def test_golden_8_missing_source_still_sends_without_inventing_a_citation() -> None:
    category = _dentists_category()
    for item in category["digest"]:
        if item["id"] == "d_2026W17_dci_radiograph":
            del item["source"]
    decision, body, _brief = _run(_merchant(), _trigger(), category)
    assert decision.send is True
    assert body is not None
    assert "source:" not in body


def test_golden_9_suppression_blocks_a_repeat_send() -> None:
    decision, body, _brief = _run(_merchant(), _trigger(), _dentists_category(), already_suppressed=True)
    assert decision.send is False
    assert decision.dominant_signal == "suppressed"
    assert body is None


def test_golden_10_stale_regulation_trigger_does_not_send() -> None:
    trigger = _trigger()
    assert trigger["expires_at"] == "2026-12-15T00:00:00Z"
    now = datetime(2026, 12, 16, tzinfo=UTC)  # after the deadline/expiry
    decision, body, _brief = _run(_merchant(), trigger, _dentists_category(), now=now)
    assert decision.send is False
    assert decision.dominant_signal == "stale_trigger"
    assert body is None


def test_golden_11_before_the_deadline_still_sends() -> None:
    trigger = _trigger()
    now = datetime(2026, 11, 1, tzinfo=UTC)
    decision, body, _brief = _run(_merchant(), trigger, _dentists_category(), now=now)
    assert decision.send is True
    assert body is not None
