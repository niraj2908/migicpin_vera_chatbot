"""Golden cases for the dentists + research_digest generator (option B, targeted trigger-coverage
expansion). Anchored on real seed data: m_001_drmeera_dentist_delhi / trg_001_research_digest_dentists
/ dentists.json's "d_2026W17_jida_fluoride" digest item — matches Case Study 1 closely (50/50 score,
docs/challenge-package/examples/case-studies.md).

The trigger payload only ever carries {category, top_item_id}; the real research content (title,
source, trial_n, patient_segment, actionable) lives entirely in CategoryContext.digest_item() —
see opportunity.py's _resolve_digest_item() docstring for why a category/kind mismatch is a hard
gate, not a low score.

No consent gate applies here: research_digest is merchant-scoped (trigger.customer_id is always
null in the real data), same as festival_upcoming/perf_dip/milestone_reached.
"""

import copy
import json
from datetime import UTC
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
    return copy.deepcopy(next(t for t in triggers if t["kind"] == "research_digest"))


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


def test_golden_1_official_seed_data_matches_case_study_1() -> None:
    decision, body, _brief = _run(_merchant(), _trigger(), _dentists_category())

    assert decision.send is True
    assert decision.dominant_signal == "research_digest:d_2026W17_jida_fluoride"
    assert decision.send_as == "vera"
    assert decision.cta == "binary_yes_no"
    assert body is not None
    assert body.startswith("Meera")  # merchant owner, not "Dr. Meera's Dental Clinic"
    assert "3-month fluoride varnish recall outperforms 6-month for high-risk adult caries" in body
    assert "JIDA Oct 2026, p.14" in body
    assert "2100" in body  # trial_n, real
    ok, reasons = validate(body, _brief)
    assert ok, reasons


def test_golden_2_unresolvable_top_item_id_does_not_send() -> None:
    trigger = _trigger()
    trigger["payload"]["top_item_id"] = "d_does_not_exist_in_digest"
    decision, body, _brief = _run(_merchant(), trigger, _dentists_category())
    assert decision.send is False
    assert body is None


def test_golden_3_no_category_context_pushed_does_not_send() -> None:
    """category is required to resolve the digest item at all -- unlike merchant/festival data,
    there is no fallback source for research content."""
    merchant = MerchantContext(_merchant())
    trigger = TriggerContext(_trigger())
    decision = decide(merchant, trigger, None, category=None)
    assert decision.send is False


def test_golden_4_category_mismatch_between_trigger_and_merchant_does_not_send() -> None:
    """The trigger's own declared payload.category must match the merchant it was pushed for --
    a dentists digest item reaching a merchant of a different category is not grounded content
    for that merchant."""
    trigger = _trigger()
    trigger["payload"]["category"] = "gyms"
    decision, body, _brief = _run(_merchant(), trigger, _dentists_category())
    assert decision.send is False
    assert body is None


def test_golden_5_digest_item_of_the_wrong_kind_does_not_send() -> None:
    """A research_digest trigger pointing at a "compliance"-kind item (or any non-"research" kind)
    is a data-shape mismatch -- hard gate, not a score penalty."""
    trigger = _trigger()
    trigger["payload"]["top_item_id"] = "d_2026W17_dci_radiograph"  # the real compliance item
    decision, body, _brief = _run(_merchant(), trigger, _dentists_category())
    assert decision.send is False
    assert body is None


def test_golden_6_missing_title_on_the_digest_item_does_not_send() -> None:
    category = _dentists_category()
    for item in category["digest"]:
        if item["id"] == "d_2026W17_jida_fluoride":
            del item["title"]
    decision, body, _brief = _run(_merchant(), _trigger(), category)
    assert decision.send is False
    assert body is None


def test_golden_7_no_trial_data_still_sends_without_fabricating_it() -> None:
    category = _dentists_category()
    for item in category["digest"]:
        if item["id"] == "d_2026W17_jida_fluoride":
            del item["trial_n"]
            del item["patient_segment"]
    decision, body, _brief = _run(_merchant(), _trigger(), category)
    assert decision.send is True
    assert body is not None
    assert "2100" not in body
    assert "patient trial" not in body


def test_golden_8_no_actionable_field_still_sends_without_a_suggested_action_fact() -> None:
    category = _dentists_category()
    for item in category["digest"]:
        if item["id"] == "d_2026W17_jida_fluoride":
            del item["actionable"]
    decision, body, _brief = _run(_merchant(), _trigger(), category)
    assert decision.send is True
    assert body is not None
    assert "suggested action" not in body


def test_golden_9_suppression_blocks_a_repeat_send() -> None:
    decision, body, _brief = _run(_merchant(), _trigger(), _dentists_category(), already_suppressed=True)
    assert decision.send is False
    assert decision.dominant_signal == "suppressed"
    assert body is None


def test_golden_10_expired_trigger_does_not_send() -> None:
    from datetime import datetime

    trigger = _trigger()
    assert trigger["expires_at"] == "2026-05-03T00:00:00Z"
    now = datetime(2026, 5, 4, tzinfo=UTC)  # after expiry
    decision, body, _brief = _run(_merchant(), trigger, _dentists_category(), now=now)
    assert decision.send is False
    assert decision.dominant_signal == "stale_trigger"
    assert body is None


def test_golden_11_fresh_trigger_before_expiry_still_sends() -> None:
    from datetime import datetime

    trigger = _trigger()
    now = datetime(2026, 4, 26, tzinfo=UTC)  # before expiry
    decision, body, _brief = _run(_merchant(), trigger, _dentists_category(), now=now)
    assert decision.send is True
    assert body is not None
