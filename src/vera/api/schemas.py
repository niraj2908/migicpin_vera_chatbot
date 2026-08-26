from pydantic import BaseModel

from vera.domain.models import CustomerState, MerchantState, Trigger


class ComposeRequest(BaseModel):
    merchant: MerchantState
    trigger: Trigger
    customer: CustomerState | None = None


class ComposeResponse(BaseModel):
    send: bool
    message: str
    cta: str
    identity: str
    suppression_key: str
    rationale: str
