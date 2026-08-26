from vera.decision.opportunity import generate_opportunities
from vera.domain.models import MerchantState, Offer, Trigger


def test_fallback_opportunity_always_present() -> None:
    merchant = MerchantState(merchant_id="m1", name="Any Shop", category="retail")
    trigger = Trigger(trigger_type="review_event", event="new_review")
    opportunities = generate_opportunities(merchant, trigger, None)
    assert any(o.name == "no_strong_opportunity" for o in opportunities)


def test_festival_opportunity_scores_higher_with_relevant_offer() -> None:
    trigger = Trigger(
        trigger_type="festival", event="Diwali", days_to_event=2, category_relevance=0.9
    )
    with_offer = MerchantState(
        merchant_id="m1",
        name="Spice Villa",
        category="restaurant",
        offers=[Offer(name="Diwali Thali", discount_pct=20)],
        rating=4.5,
    )
    without_offer = MerchantState(
        merchant_id="m2", name="Bare Kitchen", category="restaurant", rating=4.5
    )

    scored_with = {o.name: o.score for o in generate_opportunities(with_offer, trigger, None)}
    scored_without = {o.name: o.score for o in generate_opportunities(without_offer, trigger, None)}

    assert scored_with["festival:Diwali"] > scored_without["festival:Diwali"]
