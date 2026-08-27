from dataclasses import dataclass
from typing import Literal

ReplyAction = Literal["send", "wait", "end"]

AUTO_REPLY_WAIT_SECONDS = 14400  # 4 hours; matches the challenge package's own example.

_AUTO_REPLY_MARKERS = (
    "thank you for contacting",
    "our team will respond",
    "will get back to you shortly",
    "automated assistant",
    "auto-reply",
    "currently unavailable",
)

_HOSTILE_MARKERS = (
    "stop messaging",
    "not interested",
    "stop sending",
    "unsubscribe",
    "leave me alone",
    "this is useless",
    "this is spam",
    "don't contact",
    "do not contact",
)

_INTENT_COMMIT_MARKERS = (
    "let's do it",
    "lets do it",
    "let's go",
    "lets go",
    "go ahead",
    "ok let's",
    "okay let's",
    "ok lets",
    "yes let's",
    "sounds good",
    "confirm",
    "i want to join",
    "sign me up",
    "proceed",
)

ReplyKind = Literal["auto_reply", "hostile_optout", "intent_commit", "other"]


@dataclass(frozen=True)
class ReplyDecision:
    action: ReplyAction
    kind: ReplyKind
    reason: str
    wait_seconds: int | None = None
    reply_intent: str | None = None


def classify_kind(message: str, previous_message: str | None) -> ReplyKind:
    text = message.strip().lower()

    is_repeat = previous_message is not None and text == previous_message.strip().lower()
    if is_repeat or any(marker in text for marker in _AUTO_REPLY_MARKERS):
        return "auto_reply"

    if any(marker in text for marker in _HOSTILE_MARKERS):
        return "hostile_optout"

    if any(marker in text for marker in _INTENT_COMMIT_MARKERS):
        return "intent_commit"

    return "other"


def decide_reply(message: str, previous_message: str | None, auto_reply_hits_so_far: int) -> ReplyDecision:
    """Deterministic: owns the action (send/wait/end), never the LLM.

    Auto-reply handling tries once (wait) then exits on the next detection — this is the
    challenge brief's own stated pain point ("burns 2-3 turns each time" on auto-reply); we
    spend at most one.
    """
    kind = classify_kind(message, previous_message)

    if kind == "hostile_optout":
        return ReplyDecision(
            action="end",
            kind=kind,
            reason="Merchant signaled not interested or hostile; closing the conversation gracefully.",
        )

    if kind == "auto_reply":
        if auto_reply_hits_so_far == 0:
            return ReplyDecision(
                action="wait",
                kind=kind,
                reason="Detected a merchant auto-reply; backing off once before retrying.",
                wait_seconds=AUTO_REPLY_WAIT_SECONDS,
            )
        return ReplyDecision(
            action="end",
            kind=kind,
            reason="Auto-reply detected again with no real engagement signal; closing.",
        )

    if kind == "intent_commit":
        return ReplyDecision(
            action="send",
            kind=kind,
            reason="Merchant explicitly committed; switching from pitch to action immediately.",
            reply_intent="accept_and_advance",
        )

    return ReplyDecision(
        action="send",
        kind=kind,
        reason="Acknowledging the merchant's message and returning to the original ask.",
        reply_intent="redirect_to_original_ask",
    )
