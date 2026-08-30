"""Phase 3 regression tests for the TemplateComposer improvements: Hindi-aware CTA phrasing
(reusing already-firewall-tested vocabulary, never inventing new claims) and natural fact-list
joining. Must never introduce a fact not present in the input, never produce multiple/prohibited
CTAs, and must remain deterministic and firewall-valid.
"""

from dataclasses import replace

from vera.generation.brief import CompositionBrief
from vera.generation.composer import TemplateComposer, _prefers_hindi, cta_fallback_text
from vera.generation.firewall import validate


def _brief(facts: list[str], cta: str = "binary_yes_no", **overrides) -> CompositionBrief:
    base = CompositionBrief(
        category_slug="restaurants",
        voice_tone="warm_busy_practical",
        vocab_allowed=[],
        vocab_taboo=[],
        merchant_name="SK Pizza Junction",
        owner_first_name="Suresh",
        languages=["en"],
        facts=facts,
        cta=cta,
        send_as="vera",
        dominant_signal="festival:Diwali",
    )
    return replace(base, **overrides)


def test_hindi_detection_from_merchant_languages() -> None:
    assert _prefers_hindi(_brief([], languages=["en", "hi"])) is True
    assert _prefers_hindi(_brief([], languages=["en"])) is False
    assert _prefers_hindi(_brief([], languages=["hi-en mix"])) is True


def test_hindi_detection_prioritizes_customer_preference_over_merchant_languages() -> None:
    """A customer-facing message should match the specific customer, not just the merchant's
    general language list."""
    brief = _brief([], languages=["en"], customer_name="Priya", customer_language_pref="hi-en mix")
    assert _prefers_hindi(brief) is True
    brief2 = _brief([], languages=["en", "hi"], customer_name="Priya", customer_language_pref="en")
    assert _prefers_hindi(brief2) is False


def test_hindi_cta_uses_only_already_firewall_tested_vocabulary() -> None:
    """Every Hindi CTA phrase must independently pass the firewall's own CTA check -- guaranteed
    by construction, verified here directly rather than only inferred."""
    for cta in ("open_ended", "binary_yes_no", "binary_confirm_cancel", "multi_choice_slot"):
        brief = _brief(["a real fact"], cta=cta, languages=["en", "hi"])
        text = cta_fallback_text(brief)
        if text:
            ok, reasons = validate(f"Suresh, a real fact. {text}", brief)
            assert ok, (cta, text, reasons)


def test_hindi_binary_yes_no_message_end_to_end() -> None:
    brief = _brief(["Diwali is 3 day(s) away"], cta="binary_yes_no", languages=["en", "hi"])
    message = TemplateComposer().compose(brief)
    assert "haan" in message.lower() and "nahi" in message.lower()
    ok, reasons = validate(message, brief)
    assert ok, reasons


def test_english_merchant_still_gets_english_cta_unchanged() -> None:
    brief = _brief(["Diwali is 3 day(s) away"], cta="binary_yes_no", languages=["en"])
    message = TemplateComposer().compose(brief)
    assert "YES" in message
    assert "haan" not in message.lower()


def test_natural_fact_joining_still_contains_every_fact_verbatim() -> None:
    """Structural improvement (and-before-last-fact) must never drop or alter fact content."""
    facts = ["fact one here", "fact two here", "fact three here"]
    brief = _brief(facts, cta="open_ended")
    message = TemplateComposer().compose(brief)
    for fact in facts:
        assert fact in message
    assert "; and " in message


def test_single_fact_is_unaffected_by_the_joining_change() -> None:
    brief = _brief(["the only fact"], cta="open_ended")
    message = TemplateComposer().compose(brief)
    assert "the only fact" in message
    assert "; and" not in message


def test_no_hallucinated_facts_introduced_by_variation() -> None:
    """The only facts that may ever appear are the exact ones supplied -- variation touches only
    connectors/CTA phrasing, never invents new claims."""
    import re

    facts = ["revenue up 12% this week", "3 active offers"]
    brief = _brief(facts, cta="open_ended", languages=["en", "hi"])
    message = TemplateComposer().compose(brief)
    claimed_percentages = set(re.findall(r"(\d+)%", message))
    assert claimed_percentages <= {"12"}


def test_no_multiple_or_prohibited_ctas_introduced_by_hindi_variant() -> None:
    for cta in ("open_ended", "binary_yes_no", "binary_confirm_cancel", "multi_choice_slot", "none"):
        brief = _brief(["a fact"], cta=cta, languages=["en", "hi"])
        message = TemplateComposer().compose(brief)
        ok, reasons = validate(message, brief)
        assert ok, (cta, message, reasons)


def test_output_remains_deterministic() -> None:
    brief = _brief(["fact a", "fact b"], cta="open_ended", languages=["en", "hi"])
    m1 = TemplateComposer().compose(brief)
    m2 = TemplateComposer().compose(brief)
    assert m1 == m2


def test_structurally_valid_for_every_cta_type_english_and_hindi() -> None:
    for langs in (["en"], ["en", "hi"]):
        for cta in ("open_ended", "binary_yes_no", "binary_confirm_cancel", "multi_choice_slot", "none"):
            brief = _brief(["a grounded fact"], cta=cta, languages=langs)
            message = TemplateComposer().compose(brief)
            assert message.strip() != ""
            assert message.startswith("Suresh")
            ok, reasons = validate(message, brief)
            assert ok, (langs, cta, message, reasons)
