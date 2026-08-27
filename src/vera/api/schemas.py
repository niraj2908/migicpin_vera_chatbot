from typing import Any

from pydantic import BaseModel, Field

MAX_ID_LENGTH = 200
MAX_MESSAGE_LENGTH = 20_000
MAX_TRIGGERS_PER_TICK = 200
MAX_CONTEXT_PAYLOAD_BYTES = 500_000  # matches the challenge contract's documented cap
MAX_ACTIONS_PER_TICK = 20  # matches the challenge contract's documented cap

VALID_SCOPES = {"category", "merchant", "customer", "trigger"}


class ContextPushRequest(BaseModel):
    scope: str = Field(max_length=50)
    context_id: str = Field(min_length=1, max_length=MAX_ID_LENGTH)
    version: int
    payload: dict[str, Any]
    delivered_at: str


class TickRequest(BaseModel):
    now: str
    available_triggers: list[str] = Field(default_factory=list, max_length=MAX_TRIGGERS_PER_TICK)


class ReplyRequest(BaseModel):
    conversation_id: str = Field(min_length=1, max_length=MAX_ID_LENGTH)
    merchant_id: str | None = Field(default=None, max_length=MAX_ID_LENGTH)
    customer_id: str | None = Field(default=None, max_length=MAX_ID_LENGTH)
    from_role: str = Field(min_length=1, max_length=50)
    message: str = Field(max_length=MAX_MESSAGE_LENGTH)
    received_at: str
    turn_number: int
