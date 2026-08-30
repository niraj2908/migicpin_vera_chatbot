"""Tests for perf_spike (positive mirror of seasonal_perf_dip) -- gyms x perf_spike, anchored on
real seed data: m_008_zenyoga_gym_chennai / trg_024_perf_spike_zen.

Feasibility evidence (Phase 1 of this task): perf_spike is a real, documented internal trigger
kind (challenge-brief.md: "perf_spike (yesterday's views +28% vs avg)"), the real seed payload is
fully self-contained (metric, delta_pct, window, vs_baseline, likely_driver), no consent gate
needed (merchant-scoped, customer_id=null in the real trigger record).
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


def _gyms_category() -> dict:
    return json.loads((DATASET_DIR / "categories" / "gyms.json").read_text())


def _merchant() -> dict:
    merchants = json.loads((DATASET_DIR / "merchants_seed.json").read_text())["merchants"]
    return copy.deepcopy(next(m for m in merchants if m["merchant_id"] == "m_008_zenyoga_gym_chennai"))


def _trigger() -> dict:
    triggers = json.loads((DATASET_DIR / "triggers_seed.json").read_text())["triggers"]
    return copy.deepcopy(next(t for t in triggers if t["kind"] == "perf_spike"))


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


# --- GOLDEN: real, unmodified seed data ---
def test_golden_real_seed_data_sends_grounded_message() -> None:
    decision, body, _brief = _run(_merchant(), _trigger(), _gyms_category())
    assert decision.send is True
    assert decision.dominant_signal == "perf_spike"
    assert decision.action_type == "perf_spike_capitalize"
    assert decision.cta == "open_ended"
    assert decision.send_as == "vera"
    assert body is not None
    assert "15%" in body
    assert "First Month @ ₹499" in body


def test_golden_no_offer_still_sends_without_inventing_one() -> None:
    merchant = _merchant()
    merchant["offers"] = []
    decision, body, _brief = _run(merchant, _trigger(), _gyms_category())
    assert decision.send is True
    assert body is not None
    assert "₹" not in body  # no offer fact, no invented price


# --- COUNTERFACTUAL: magnitude below threshold ---
def test_counterfactual_small_spike_does_not_send() -> None:
    trigger = _trigger()
    trigger["payload"]["delta_pct"] = 0.03  # below _MEANINGFUL_SPIKE_THRESHOLD (0.10)
    decision, body, _brief = _run(_merchant(), trigger, _gyms_category())
    assert decision.send is False
    assert body is None


def test_counterfactual_negative_delta_does_not_send() -> None:
    """A negative delta on a perf_spike trigger is contradictory data -- must not send."""
    trigger = _trigger()
    trigger["payload"]["delta_pct"] = -0.20
    decision, _body, _brief = _run(_merchant(), trigger, _gyms_category())
    assert decision.send is False


# --- INSUFFICIENT EVIDENCE ---
def test_missing_metric_does_not_send() -> None:
    trigger = _trigger()
    del trigger["payload"]["metric"]
    decision, _body, _brief = _run(_merchant(), trigger, _gyms_category())
    assert decision.send is False


def test_missing_delta_pct_does_not_send() -> None:
    trigger = _trigger()
    del trigger["payload"]["delta_pct"]
    decision, _body, _brief = _run(_merchant(), trigger, _gyms_category())
    assert decision.send is False


# --- SUPPRESSION ---
def test_suppression_blocks_a_repeat_send() -> None:
    decision, body, _brief = _run(_merchant(), _trigger(), _gyms_category(), already_suppressed=True)
    assert decision.send is False
    assert decision.dominant_signal == "suppressed"
    assert body is None


# --- MERCHANT ISOLATION ---
def test_different_merchants_do_not_contaminate_facts() -> None:
    m1 = _merchant()
    m2 = _merchant()
    m2["merchant_id"] = "m_other_gym"
    m2["offers"] = [{"id": "o_x", "title": "Totally Different Offer @ ₹1", "status": "active", "started": "2026-01-01"}]
    _d1, body1, _ = _run(m1, _trigger(), _gyms_category())
    _d2, body2, _ = _run(m2, _trigger(), _gyms_category())
    assert "Totally Different Offer" not in (body1 or "")
    assert "First Month" not in (body2 or "")


# --- MALFORMED INPUT ---
def test_malformed_delta_pct_type_does_not_crash() -> None:
    trigger = _trigger()
    trigger["payload"]["delta_pct"] = "not_a_number"
    decision, _body, _brief = _run(_merchant(), trigger, _gyms_category())
    assert decision.send is False  # treated as missing, not crashed


def test_malformed_vs_baseline_type_is_omitted_not_fabricated() -> None:
    trigger = _trigger()
    trigger["payload"]["vs_baseline"] = {"unexpected": "shape"}
    decision, body, _brief = _run(_merchant(), trigger, _gyms_category())
    assert decision.send is True
    assert body is not None
    assert "unexpected" not in body


# --- GROUNDING / FABRICATION RESISTANCE ---
def test_no_invented_percentage_beyond_the_real_delta() -> None:
    _decision, body, _brief = _run(_merchant(), _trigger(), _gyms_category())
    assert body is not None
    import re

    claimed_percentages = set(re.findall(r"(\d+)%", body))
    assert claimed_percentages <= {"15"}  # only the real delta_pct value, nothing invented
