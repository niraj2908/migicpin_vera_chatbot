from vera.generation.brief import CompositionBrief
from vera.generation.firewall import validate


def _brief(facts: list[str], max_chars: int = 280) -> CompositionBrief:
    return CompositionBrief(
        category="restaurant",
        merchant_name="Spice Villa",
        facts=facts,
        cta="Book now",
        identity="Spice Villa",
        urgency="high",
        max_chars=max_chars,
    )


def test_accepts_grounded_message() -> None:
    brief = _brief(["20% off Diwali Thali"])
    ok, reasons = validate("Spice Villa: 20% off Diwali Thali. Book now.", brief)
    assert ok, reasons


def test_rejects_unsupported_percentage_claim() -> None:
    brief = _brief(["Diwali is 2 days away"])
    ok, reasons = validate("Spice Villa: get 50% off today! Book now.", brief)
    assert not ok
    assert any("50" in r for r in reasons)


def test_rejects_empty_message() -> None:
    brief = _brief(["Diwali is 2 days away"])
    ok, reasons = validate("   ", brief)
    assert not ok
    assert "empty message" in reasons


def test_rejects_overlong_message() -> None:
    brief = _brief(["Diwali is 2 days away"], max_chars=20)
    ok, reasons = validate("x" * 100, brief)
    assert not ok
    assert any("exceeds max_chars" in r for r in reasons)
