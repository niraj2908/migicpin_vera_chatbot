"""Phase 8: TemplateComposer evaluated independently as the thing that ships whenever a real
provider is unavailable, times out, or gets rejected by the firewall. A provider outage must
degrade gracefully — never produce something generic, ungrounded, or firewall-unsafe.
"""

import copy
import json
from pathlib import Path

from vera.decision.compiler import decide
from vera.domain.context import CategoryContext, MerchantContext, TriggerContext
from vera.generation.brief import build_brief
from vera.generation.composer import TemplateComposer

from .scoring import evaluate

DATASET_DIR = Path(__file__).parent.parent.parent / "docs" / "challenge-package" / "dataset"
_COMPOSER = TemplateComposer()


def _restaurant_category() -> dict:
    return json.loads((DATASET_DIR / "categories" / "restaurants.json").read_text())


def _merchant(merchant_id: str) -> dict:
    merchants = json.loads((DATASET_DIR / "merchants_seed.json").read_text())["merchants"]
    return copy.deepcopy(next(m for m in merchants if m["merchant_id"] == merchant_id))


def _festival_trigger(merchant_id: str, days_until: int) -> dict:
    triggers = json.loads((DATASET_DIR / "triggers_seed.json").read_text())["triggers"]
    trigger = copy.deepcopy(next(t for t in triggers if t["kind"] == "festival_upcoming"))
    trigger["id"] = f"trg_fallback_{merchant_id}_{days_until}"
    trigger["merchant_id"] = merchant_id
    trigger["payload"]["days_until"] = days_until
    trigger["suppression_key"] = f"festival:diwali:2026:{merchant_id}:{days_until}"
    return trigger


_SCENARIOS = [
    ("m_005_pizzajunction_restaurant_delhi", 3, False),
    ("m_005_pizzajunction_restaurant_delhi", 3, True),  # offers stripped below
    ("m_006_southindiancafe_restaurant_bangalore", 4, False),
    ("m_005_pizzajunction_restaurant_delhi", 1, False),
]


def _build_case(merchant_id: str, days_until: int, strip_offers: bool):
    merchant_raw = _merchant(merchant_id)
    if strip_offers:
        merchant_raw["offers"] = []
    trigger_raw = _festival_trigger(merchant_id, days_until)
    merchant = MerchantContext(merchant_raw)
    category = CategoryContext(_restaurant_category())
    trigger = TriggerContext(trigger_raw)
    decision = decide(merchant, trigger, None)
    assert decision.send is True
    brief = build_brief(decision, merchant, category, None)
    message = _COMPOSER.compose(brief)
    return message, brief


def test_fallback_is_grounded_and_firewall_safe_across_scenarios() -> None:
    for merchant_id, days_until, strip_offers in _SCENARIOS:
        message, brief = _build_case(merchant_id, days_until, strip_offers)
        report = evaluate(message, brief)
        assert report.passes_grounding, (merchant_id, report.grounding_violations, message)
        assert report.passes_category_fit, (merchant_id, report.category_fit_violations, message)


def test_fallback_is_specific_not_generic_across_scenarios() -> None:
    for merchant_id, days_until, strip_offers in _SCENARIOS:
        message, brief = _build_case(merchant_id, days_until, strip_offers)
        signals = evaluate(message, brief).specificity_signals
        assert signals["uses_at_least_one_fact"], (merchant_id, message)
        assert signals["has_number"], (merchant_id, message)  # every case here has a days-away number at minimum


def test_fallback_cta_matches_the_decision_across_scenarios() -> None:
    for merchant_id, days_until, strip_offers in _SCENARIOS:
        message, brief = _build_case(merchant_id, days_until, strip_offers)
        engagement = evaluate(message, brief).engagement_signals
        assert engagement["single_clear_cta"], (merchant_id, message)
        assert engagement["no_generic_filler"], (merchant_id, engagement["generic_filler_phrases"], message)
        # binary_yes_no / open_ended both expect exactly what the fallback's fixed CTA table
        # produces for that cta value — never a mismatched or invented ask. Language-aware: this
        # real merchant (m_005) lists "hi" among its languages, so its fallback CTA is correctly
        # the Hindi variant (haan/nahi + bata dijiye), reusing the same firewall-tested Hindi CTA
        # vocabulary added this session — never the English phrase for a Hindi-preferring
        # merchant, and never a mismatched/invented one either way.
        from vera.generation.composer import _prefers_hindi

        hindi = _prefers_hindi(brief)
        if brief.cta == "binary_yes_no":
            assert ("YES" in message) if not hindi else ("haan" in message.lower() and "nahi" in message.lower())
        elif brief.cta == "open_ended":
            if hindi:
                assert "bata dijiye" in message.lower()
            else:
                assert "?" in message or "share more" in message.lower()


def test_fallback_never_mentions_an_offer_it_does_not_have() -> None:
    message, brief = _build_case("m_005_pizzajunction_restaurant_delhi", 3, strip_offers=True)
    assert "pizza" not in message.lower() or "free" not in message.lower()
    assert brief.cta == "open_ended"
