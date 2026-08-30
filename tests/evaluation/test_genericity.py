"""Phase 7 genericity tests: counterfactual pairs that must produce materially different
messages. This is one of the most important defenses against the real judge's fresh-context
injection — a system that produces near-identical output for different merchants/offers/
categories is exactly what "genericity detection" penalizes.

`genericity_similarity` (see scoring.py) is an approximate word-overlap heuristic, not a claim
of semantic equivalence — treated here as a red flag worth a look, not a hard pass/fail gate on
its own; each test also asserts the concrete, unambiguous facts that must differ.
"""

import copy
import json
from pathlib import Path

from vera.decision.compiler import decide
from vera.domain.context import CategoryContext, MerchantContext, TriggerContext
from vera.generation.brief import build_brief
from vera.generation.composer import TemplateComposer
from vera.pipeline import compose_and_validate

from .scoring import genericity_similarity

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
    trigger["id"] = f"trg_genericity_{merchant_id}"
    trigger["merchant_id"] = merchant_id
    trigger["payload"]["days_until"] = days_until
    trigger["suppression_key"] = f"festival:diwali:2026:{merchant_id}"
    return trigger


def _compose(merchant_raw: dict, category_raw: dict, trigger_raw: dict) -> str:
    merchant = MerchantContext(merchant_raw)
    category = CategoryContext(category_raw)
    trigger = TriggerContext(trigger_raw)
    decision = decide(merchant, trigger, None)
    assert decision.send is True
    brief = build_brief(decision, merchant, category, None)
    return compose_and_validate(brief, _COMPOSER).message


def test_different_merchants_different_offers_produce_materially_different_messages() -> None:
    category = _restaurant_category()

    merchant_a = _merchant("m_005_pizzajunction_restaurant_delhi")  # "Buy 1 Pizza Get 1 Free"
    message_a = _compose(merchant_a, category, _festival_trigger(merchant_a["merchant_id"], days_until=3))

    merchant_b = _merchant("m_006_southindiancafe_restaurant_bangalore")  # "Weekday Lunch Thali @ ₹149"
    message_b = _compose(merchant_b, category, _festival_trigger(merchant_b["merchant_id"], days_until=4))

    assert merchant_a["identity"]["name"] not in message_b
    assert merchant_b["identity"]["name"] not in message_a
    assert merchant_a["offers"][0]["title"] not in message_b
    assert merchant_b["offers"][0]["title"] not in message_a
    similarity = genericity_similarity(message_a, message_b)
    assert similarity < 0.6, f"messages for two different merchants/offers are suspiciously similar ({similarity:.2f}): {message_a!r} vs {message_b!r}"


def test_offer_change_alone_updates_the_message_claim() -> None:
    category = _restaurant_category()
    merchant = _merchant("m_005_pizzajunction_restaurant_delhi")
    trigger = _festival_trigger(merchant["merchant_id"], days_until=3)

    merchant_20 = copy.deepcopy(merchant)
    merchant_20["offers"][0]["title"] = "20% off Diwali Thali"
    message_20 = _compose(merchant_20, category, trigger)

    merchant_10 = copy.deepcopy(merchant)
    merchant_10["offers"][0]["title"] = "10% off Diwali Thali"
    message_10 = _compose(merchant_10, category, trigger)

    assert "20%" in message_20 and "20%" not in message_10
    assert "10%" in message_10 and "10%" not in message_20


def test_festival_change_alone_updates_the_message() -> None:
    category = _restaurant_category()
    merchant = _merchant("m_005_pizzajunction_restaurant_delhi")

    trigger_diwali = _festival_trigger(merchant["merchant_id"], days_until=3)
    message_diwali = _compose(merchant, category, trigger_diwali)

    trigger_holi = copy.deepcopy(trigger_diwali)
    trigger_holi["payload"]["festival"] = "Holi"
    message_holi = _compose(merchant, category, trigger_holi)

    assert "Diwali" in message_diwali and "Diwali" not in message_holi
    assert "Holi" in message_holi and "Holi" not in message_diwali


def test_template_composer_first_message_wording_now_varies_by_category() -> None:
    """Supersedes the prior characterization test of the same limitation this test's own
    docstring predicted: TemplateComposer's fallback shape used to be category-blind (facts
    varied, sentence structure never did) -- production evidence (Gemini quota-exhausted, the
    fallback is what actually ships) made this the highest-ROI fix available. Each category now
    gets a real, evidence-grounded opener phrase reused verbatim from that category's own
    documented voice.tone_examples (categories/*.json) -- restaurants/salons both say "quick
    one —" and dentists says "worth a look —" because that's what their own real examples say,
    not because a distinct phrase was forced onto every category."""
    from vera.generation.brief import CompositionBrief

    facts = ["Diwali is 3 day(s) away", "20% off"]
    restaurant_brief = CompositionBrief(
        category_slug="restaurants", voice_tone="warm_busy_practical",
        vocab_allowed=["footfall", "covers"], vocab_taboo=[], merchant_name="Test Restaurant",
        owner_first_name="Suresh", languages=["en"], facts=facts, cta="binary_yes_no",
        send_as="vera", dominant_signal="festival:Diwali",
    )
    dentist_brief = CompositionBrief(
        category_slug="dentists", voice_tone="peer_clinical",
        vocab_allowed=["fluoride varnish", "caries"], vocab_taboo=[], merchant_name="Test Clinic",
        owner_first_name="Meera", languages=["en"], facts=facts, cta="binary_yes_no",
        send_as="vera", dominant_signal="festival:Diwali",
    )

    restaurant_message = _COMPOSER.compose(restaurant_brief)
    dentist_message = _COMPOSER.compose(dentist_brief)

    assert "quick one —" in restaurant_message
    assert "worth a look —" in dentist_message

    restaurant_shape = restaurant_message.replace("Suresh", "X").replace("Test Restaurant", "X")
    dentist_shape = dentist_message.replace("Meera", "X").replace("Test Clinic", "X")
    assert restaurant_shape != dentist_shape
