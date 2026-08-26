from vera.decision.compiler import decide
from vera.domain.models import MerchantState, Offer, Trigger


def _restaurant() -> MerchantState:
    return MerchantState(
        merchant_id="m1",
        name="Spice Villa",
        category="restaurant",
        offers=[Offer(name="Diwali Thali", discount_pct=20)],
        rating=4.3,
    )


def _festival_trigger(days_to_event: int = 2) -> Trigger:
    return Trigger(
        trigger_type="festival",
        event="Diwali",
        days_to_event=days_to_event,
        category_relevance=0.9,
    )


def test_decision_is_deterministic() -> None:
    merchant, trigger = _restaurant(), _festival_trigger()
    first = decide(merchant, trigger)
    second = decide(merchant, trigger)
    assert first == second


def test_sends_when_festival_close_and_offer_relevant() -> None:
    decision = decide(_restaurant(), _festival_trigger(days_to_event=2))
    assert decision.send is True
    assert decision.action_type == "festival_campaign"
    assert any("20" in fact for fact in decision.facts_allowed)


def test_suppressed_when_campaign_fatigue_high() -> None:
    merchant = _restaurant()
    merchant.campaign_fatigue = 0.9
    decision = decide(merchant, _festival_trigger())
    assert decision.send is False
    assert "fatigue" in decision.reason.lower()


def test_no_send_for_weak_low_relevance_trigger() -> None:
    merchant = MerchantState(merchant_id="m2", name="Plain Store", category="hardware")
    trigger = Trigger(
        trigger_type="festival", event="Diwali", days_to_event=20, category_relevance=0.1
    )
    decision = decide(merchant, trigger)
    assert decision.send is False


def test_suppression_key_is_stable_per_merchant_trigger() -> None:
    merchant, trigger = _restaurant(), _festival_trigger()
    a = decide(merchant, trigger)
    b = decide(merchant, _festival_trigger(days_to_event=1))
    assert a.suppression_key == b.suppression_key == "m1:festival:Diwali"
