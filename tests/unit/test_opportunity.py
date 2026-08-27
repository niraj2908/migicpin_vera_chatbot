from vera.decision.opportunity import generate_opportunities
from vera.domain.context import MerchantContext, TriggerContext


def _merchant(category_slug: str = "restaurants", offers: list[dict] | None = None) -> MerchantContext:
    return MerchantContext(
        {
            "merchant_id": "m_test",
            "category_slug": category_slug,
            "identity": {"name": "Test Merchant", "owner_first_name": "Suresh", "languages": ["en", "hi"]},
            "offers": offers if offers is not None else [],
        }
    )


def _festival_trigger(days_until: int = 3, category_relevance: list[str] | None = None) -> TriggerContext:
    return TriggerContext(
        {
            "id": "trg_test_festival",
            "scope": "merchant",
            "kind": "festival_upcoming",
            "merchant_id": "m_test",
            "customer_id": None,
            "payload": {
                "festival": "Diwali",
                "date": "2026-10-31",
                "days_until": days_until,
                "category_relevance": category_relevance or ["restaurants"],
            },
            "urgency": 2,
            "suppression_key": "festival:diwali:2026:m_test",
        }
    )


def test_fallback_opportunity_always_present() -> None:
    merchant = _merchant()
    trigger = TriggerContext({"id": "t1", "kind": "review_theme_emerged", "merchant_id": "m_test", "payload": {}})
    opportunities = generate_opportunities(merchant, trigger, None)
    assert any(o.name == "no_strong_opportunity" for o in opportunities)


def test_festival_opportunity_absent_when_category_not_relevant() -> None:
    merchant = _merchant(category_slug="gyms")
    trigger = _festival_trigger(category_relevance=["restaurants", "salons"])
    opportunities = generate_opportunities(merchant, trigger, None)
    assert [o.name for o in opportunities] == ["no_strong_opportunity"]


def test_festival_opportunity_scores_higher_with_active_offer() -> None:
    trigger = _festival_trigger(days_until=3)
    with_offer = _merchant(offers=[{"id": "o1", "title": "Buy 1 Pizza Get 1 Free", "status": "active"}])
    without_offer = _merchant(offers=[])

    scored_with = {o.name: o.score for o in generate_opportunities(with_offer, trigger, None)}
    scored_without = {o.name: o.score for o in generate_opportunities(without_offer, trigger, None)}

    assert scored_with["festival:Diwali"] > scored_without["festival:Diwali"]


def test_festival_opportunity_facts_are_grounded_in_context() -> None:
    trigger = _festival_trigger(days_until=3)
    merchant = _merchant(offers=[{"id": "o1", "title": "Buy 1 Pizza Get 1 Free", "status": "active"}])
    opportunities = generate_opportunities(merchant, trigger, None)
    festival_opp = next(o for o in opportunities if o.name == "festival:Diwali")

    assert any("Diwali" in fact for fact in festival_opp.facts)
    assert any("Buy 1 Pizza Get 1 Free" in fact for fact in festival_opp.facts)


def test_expired_offer_is_not_used_as_a_fact() -> None:
    trigger = _festival_trigger(days_until=3)
    merchant = _merchant(offers=[{"id": "o1", "title": "Old Deep Cleaning Offer", "status": "expired"}])
    opportunities = generate_opportunities(merchant, trigger, None)
    festival_opp = next(o for o in opportunities if o.name == "festival:Diwali")
    assert not any("Old Deep Cleaning Offer" in fact for fact in festival_opp.facts)
