from typing import Literal

from pydantic import BaseModel, Field

TriggerType = Literal[
    "festival",
    "season",
    "research",
    "merchant_update",
    "customer_event",
    "offer_event",
    "review_event",
    "market_event",
]


class Offer(BaseModel):
    name: str
    discount_pct: float | None = None
    original_price: float | None = None
    final_price: float | None = None
    expires_in_days: int | None = None


class MerchantState(BaseModel):
    merchant_id: str
    name: str
    category: str
    subcategory: str | None = None
    city: str | None = None
    offers: list[Offer] = Field(default_factory=list)
    catalog: list[str] = Field(default_factory=list)
    rating: float | None = None
    campaign_fatigue: float = 0.0


class CustomerState(BaseModel):
    customer_id: str | None = None
    relationship: Literal["new", "returning", "lapsed"] | None = None
    consent: bool = True
    fatigue: float = 0.0


class Trigger(BaseModel):
    trigger_type: TriggerType
    event: str
    days_to_event: int | None = None
    category_relevance: float = 0.5
