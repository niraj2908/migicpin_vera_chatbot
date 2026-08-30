"""P0 fix: challenge-brief.md SS11 explicitly names "Re-introducing yourself after the first
message" as a judge-penalized anti-pattern. Both composer paths previously named the sending
merchant unconditionally whenever a message was customer-facing -- including on every /v1/reply
turn, since CompositionBrief carried no first-message/turn signal and for_reply() only ever
changed reply_intent. Covers: TemplateComposer's behavior directly, for_reply()'s own contract,
the LLM payload wiring, and that the fix never affects a merchant-facing (non-customer) message
or drops any other required content.
"""

from dataclasses import replace

from vera.generation.brief import CompositionBrief, for_reply
from vera.generation.composer import TemplateComposer
from vera.generation.composer.shared import build_provider_payload
from vera.generation.firewall import validate


def _brief(facts: list[str], cta: str = "binary_yes_no", **overrides) -> CompositionBrief:
    base = CompositionBrief(
        category_slug="gyms",
        voice_tone="coaching_motivational",
        vocab_allowed=[],
        vocab_taboo=[],
        merchant_name="PowerHouse Fitness",
        owner_first_name="Karthik",
        languages=["en"],
        facts=facts,
        cta=cta,
        send_as="merchant_on_behalf",
        dominant_signal="customer_lapsed_hard",
        customer_name="Rashmi",
    )
    return replace(base, **overrides)


def test_first_message_names_the_sending_merchant() -> None:
    brief = _brief(["it has been 57 days since your last visit"], is_first_message=True)
    message = TemplateComposer().compose(brief)
    assert "this is PowerHouse Fitness" in message


def test_reply_does_not_reintroduce_the_merchant() -> None:
    brief = _brief(["it has been 57 days since your last visit"], is_first_message=False)
    message = TemplateComposer().compose(brief)
    assert "this is PowerHouse Fitness" not in message


def test_reply_message_still_grounded_and_firewall_valid_without_the_intro() -> None:
    """Dropping the intro clause must not affect grounding, CTA presence, or any other firewall
    check -- only the reintroduction sentence itself is gated."""
    brief = _brief(["it has been 57 days since your last visit"], is_first_message=False)
    message = TemplateComposer().compose(brief)
    ok, reasons = validate(message, brief)
    assert ok, reasons
    assert "57 days" in message


def test_merchant_facing_message_unaffected_either_way() -> None:
    """The intro clause only ever applied to customer-facing messages (customer_name present);
    a merchant-facing brief must render identically regardless of is_first_message."""
    facts = ["Diwali is 3 day(s) away"]
    first = _brief(facts, cta="open_ended", customer_name=None, send_as="vera", is_first_message=True)
    reply = _brief(facts, cta="open_ended", customer_name=None, send_as="vera", is_first_message=False)
    assert TemplateComposer().compose(first) == TemplateComposer().compose(reply)


def test_for_reply_always_sets_is_first_message_false() -> None:
    original = _brief(["fact"], is_first_message=True)
    assert original.is_first_message is True
    reply_brief = for_reply(original, "redirect_to_original_ask")
    assert reply_brief.is_first_message is False
    # everything else about the original grounded brief is preserved, only reply_intent
    # and is_first_message change.
    assert reply_brief.facts == original.facts
    assert reply_brief.merchant_name == original.merchant_name


def test_build_brief_defaults_to_first_message() -> None:
    """A brief built directly (not via for_reply) is always the /v1/tick send that opens a
    conversation -- try_reserve() guarantees conversation_id is new whenever build_brief runs."""
    brief = _brief(["fact"])
    assert brief.is_first_message is True


def test_llm_payload_carries_is_first_message() -> None:
    """The LLM path enforces this via prompt instruction, not the firewall -- the payload must
    actually carry the signal the system prompt (shared.py) tells the model to condition on."""
    first_payload = build_provider_payload(_brief(["fact"], is_first_message=True))
    reply_payload = build_provider_payload(_brief(["fact"], is_first_message=False))
    assert first_payload["is_first_message"] is True
    assert reply_payload["is_first_message"] is False
