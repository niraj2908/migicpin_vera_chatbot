from vera.decision.compiler import decide
from vera.domain.context import CustomerContext, MerchantContext, TriggerContext


def _merchant(category_slug: str = "restaurants", offers: list[dict] | None = None) -> MerchantContext:
    return MerchantContext(
        {
            "merchant_id": "m_005_pizzajunction_restaurant_delhi",
            "category_slug": category_slug,
            "identity": {"name": "SK Pizza Junction", "owner_first_name": "Suresh", "languages": ["en", "hi"]},
            "offers": offers if offers is not None else [{"id": "o1", "title": "Buy 1 Pizza Get 1 Free", "status": "active"}],
        }
    )


def _festival_trigger(days_until: int = 3, customer_id: str | None = None) -> TriggerContext:
    return TriggerContext(
        {
            "id": "trg_006_festival_diwali",
            "scope": "customer" if customer_id else "merchant",
            "kind": "festival_upcoming",
            "merchant_id": "m_005_pizzajunction_restaurant_delhi",
            "customer_id": customer_id,
            "payload": {
                "festival": "Diwali",
                "date": "2026-10-31",
                "days_until": days_until,
                "category_relevance": ["restaurants", "salons", "pharmacies"],
            },
            "urgency": 1,
            "suppression_key": "festival:diwali:2026:m_005",
        }
    )


def test_decision_is_deterministic() -> None:
    merchant, trigger = _merchant(), _festival_trigger()
    first = decide(merchant, trigger)
    second = decide(merchant, trigger)
    assert first == second


def test_sends_when_festival_close_and_offer_relevant() -> None:
    decision = decide(_merchant(), _festival_trigger(days_until=3))
    assert decision.send is True
    assert decision.action_type == "festival_campaign"
    assert decision.cta == "binary_yes_no"
    assert any("Buy 1 Pizza Get 1 Free" in fact for fact in decision.facts_allowed)


def test_no_send_when_category_not_relevant_to_trigger() -> None:
    merchant = _merchant(category_slug="gyms")
    decision = decide(merchant, _festival_trigger(days_until=3))
    assert decision.send is False


def test_no_send_for_weak_far_off_trigger_without_offer() -> None:
    merchant = _merchant(offers=[])
    decision = decide(merchant, _festival_trigger(days_until=300))
    assert decision.send is False


def test_suppressed_when_already_used() -> None:
    decision = decide(_merchant(), _festival_trigger(days_until=3), already_suppressed=True)
    assert decision.send is False
    assert decision.dominant_signal == "suppressed"


def test_suppression_key_is_stable_across_timing_changes() -> None:
    a = decide(_merchant(), _festival_trigger(days_until=3))
    b = decide(_merchant(), _festival_trigger(days_until=1))
    assert a.suppression_key == b.suppression_key == "festival:diwali:2026:m_005"


def test_send_as_is_vera_for_merchant_scoped_trigger() -> None:
    decision = decide(_merchant(), _festival_trigger(days_until=3))
    assert decision.send_as == "vera"


def test_send_as_is_merchant_on_behalf_for_customer_scoped_trigger() -> None:
    customer = CustomerContext({"customer_id": "c_001", "state": "active"})
    decision = decide(_merchant(), _festival_trigger(days_until=3, customer_id="c_001"), customer)
    assert decision.send_as == "merchant_on_behalf"


def test_no_send_declines_facts_and_cta() -> None:
    decision = decide(_merchant(category_slug="gyms"), _festival_trigger(days_until=3))
    assert decision.facts_allowed == []
    assert decision.cta == "none"
