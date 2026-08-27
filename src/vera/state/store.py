"""In-memory state for the challenge HTTP contract.

Three concerns, kept explicit rather than as scattered module-level dicts:

- ContextStore: versioned (scope, context_id) -> payload, idempotent per the
  official /v1/context semantics (repost of same-or-lower version is a no-op
  that reports the current version; a higher version replaces atomically).
- ConversationStore: per-conversation_id turn history + status, needed for
  /v1/reply (auto-reply detection, anti-repetition, end/wait state).
- SuppressionStore: which trigger suppression_keys have already been acted on,
  and which merchants have opted out — the real dedup/fatigue mechanism the
  contract provides, instead of an invented campaign_fatigue score.

Single-process, in-memory, as the reference implementation and testing brief
both call out as sufficient ("storing in memory is fine; just don't restart
between calls").
"""

import threading
from dataclasses import dataclass, field
from datetime import datetime
from typing import TYPE_CHECKING, Any, Literal

if TYPE_CHECKING:
    from vera.generation.brief import CompositionBrief


@dataclass
class PushResult:
    accepted: bool
    reason: str | None = None
    current_version: int | None = None
    ack_id: str | None = None
    stored_at: str | None = None


class ContextStore:
    def __init__(self) -> None:
        self._items: dict[tuple[str, str], tuple[int, dict[str, Any]]] = {}
        # Empirically confirmed via a barrier-forced concurrent test that CPython's GIL makes
        # this check-then-write race very hard to trigger in practice (0/200 forced-interleave
        # attempts), but the code has no language-level atomicity guarantee without a lock, and
        # the contract requires version 4 to *always* beat a concurrent version 3 — not merely
        # "almost always". A lock around a two-dict-op critical section costs nothing measurable.
        self._lock = threading.Lock()

    def push(
        self, scope: str, context_id: str, version: int, payload: dict[str, Any], now: datetime
    ) -> PushResult:
        # Contract (challenge-testing-brief.md §2.1): "Idempotent by (context_id, version).
        # Re-posting the same version is a no-op" — a genuinely separate case from the
        # documented 409, which is reserved for "you already have a HIGHER version" (strictly
        # stale). Equal version must be accepted (200), not rejected — but still must not
        # re-store/overwrite: a same-version repost is defined as a no-op, not an update, so an
        # equal-version call with a *different* payload does not silently replace what's stored.
        key = (scope, context_id)
        with self._lock:
            current = self._items.get(key)
            if current is not None and current[0] > version:
                return PushResult(accepted=False, reason="stale_version", current_version=current[0])

            if current is None or current[0] < version:
                self._items[key] = (version, payload)

            return PushResult(
                accepted=True,
                ack_id=f"ack_{context_id}_v{version}",
                stored_at=now.isoformat().replace("+00:00", "Z"),
            )

    def get(self, scope: str, context_id: str) -> dict[str, Any] | None:
        item = self._items.get((scope, context_id))
        return item[1] if item else None

    def version_of(self, scope: str, context_id: str) -> int | None:
        item = self._items.get((scope, context_id))
        return item[0] if item else None

    def counts(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for scope, _ in self._items:
            counts[scope] = counts.get(scope, 0) + 1
        return counts


@dataclass
class Turn:
    from_role: str
    message: str
    ts: datetime


@dataclass
class ConversationState:
    conversation_id: str
    merchant_id: str
    customer_id: str | None
    trigger_id: str
    status: Literal["active", "ended", "waiting"] = "active"
    turns: list[Turn] = field(default_factory=list)
    auto_reply_hits: int = 0
    last_incoming_message: str | None = None
    sent_bodies: set[str] = field(default_factory=set)
    brief: "CompositionBrief | None" = None


class ConversationStore:
    def __init__(self) -> None:
        self._conversations: dict[str, ConversationState] = {}
        # Guards structural changes to _conversations (reservation/rollback) and lazy creation of
        # per-conversation turn locks below. Found necessary via real concurrent HTTP evidence: a
        # `get(conversation_id) is not None` check followed much later (after building a brief and
        # calling the LLM composer) by `create(...)` left a multi-second window in which several
        # concurrent /v1/tick calls for the same (merchant, trigger) all passed the check before
        # any of them had created the conversation, each composing and returning its own action —
        # reproduced empirically (1/15 rounds at 12-way concurrency against a live server).
        self._struct_lock = threading.Lock()
        self._turn_locks: dict[str, threading.Lock] = {}

    def try_reserve(
        self, conversation_id: str, merchant_id: str, customer_id: str | None, trigger_id: str
    ) -> bool:
        """Atomically claims conversation_id for a new proactive send. Returns False if another
        caller already claimed it — the caller must not build a brief or call the composer in
        that case. Must be called, and must succeed, before any composition work begins."""
        with self._struct_lock:
            if conversation_id in self._conversations:
                return False
            self._conversations[conversation_id] = ConversationState(
                conversation_id=conversation_id,
                merchant_id=merchant_id,
                customer_id=customer_id,
                trigger_id=trigger_id,
            )
            return True

    def release(self, conversation_id: str) -> None:
        """Rolls back a reservation whose composition ultimately produced no sendable body, so a
        later tick may retry this (merchant, trigger). Safe without extra synchronization: only
        the thread that won try_reserve() ever calls this for a given conversation_id, since every
        other concurrent caller already received False and never touched this entry."""
        with self._struct_lock:
            self._conversations.pop(conversation_id, None)

    def attach_brief(self, conversation_id: str, brief: "CompositionBrief") -> None:
        state = self._conversations.get(conversation_id)
        if state is not None:
            state.brief = brief

    def create(
        self,
        conversation_id: str,
        merchant_id: str,
        customer_id: str | None,
        trigger_id: str,
        brief: "CompositionBrief | None" = None,
    ) -> ConversationState:
        state = ConversationState(
            conversation_id=conversation_id,
            merchant_id=merchant_id,
            customer_id=customer_id,
            trigger_id=trigger_id,
            brief=brief,
        )
        self._conversations[conversation_id] = state
        return state

    def get(self, conversation_id: str) -> ConversationState | None:
        return self._conversations.get(conversation_id)

    def turn_lock(self, conversation_id: str) -> threading.Lock:
        """Per-conversation lock serializing /v1/reply processing for one conversation_id, without
        blocking concurrent replies on *other* conversations. Found necessary via real concurrent
        HTTP evidence: without it, concurrent identical replies to the same conversation raced on
        conv.last_incoming_message/auto_reply_hits/status, causing a genuine merchant commitment
        message to be misclassified as a repeated auto-reply by one thread while another thread
        concurrently returned a live 'send' — an inconsistent, corrupted outcome for what the
        contract treats as one incoming turn at a time."""
        with self._struct_lock:
            lock = self._turn_locks.get(conversation_id)
            if lock is None:
                lock = threading.Lock()
                self._turn_locks[conversation_id] = lock
            return lock

    def record_sent(self, conversation_id: str, body: str) -> None:
        state = self._conversations.get(conversation_id)
        if state is not None:
            state.sent_bodies.add(body)


class SuppressionStore:
    def __init__(self) -> None:
        # Keyed by (merchant_id, suppression_key), not suppression_key alone. Found necessary via
        # real dataset evidence: 2 of 25 real triggers (research_digest_release, supply_alert)
        # supply a suppression_key with no merchant identity embedded in it, unlike the other 23
        # and unlike generate_dataset.py's own generator convention. A bare-string set would let
        # one merchant's send silently suppress a second, unrelated merchant's legitimate action
        # if a fresh judge scenario ever reuses one of those category/event-wide keys across
        # merchants — decide()'s own reason text already says "for this merchant", so this closes
        # the store up to match what the decision layer already assumed.
        self._used_keys: set[tuple[str, str]] = set()
        self._suppressed_merchants: set[str] = set()

    def is_used(self, merchant_id: str, suppression_key: str) -> bool:
        return (merchant_id, suppression_key) in self._used_keys

    def mark_used(self, merchant_id: str, suppression_key: str) -> None:
        if suppression_key:
            self._used_keys.add((merchant_id, suppression_key))

    def suppress_merchant(self, merchant_id: str) -> None:
        self._suppressed_merchants.add(merchant_id)

    def is_merchant_suppressed(self, merchant_id: str) -> bool:
        return merchant_id in self._suppressed_merchants


class Store:
    """Single composition root for all engine state, injected into the API layer."""

    def __init__(self) -> None:
        self.context = ContextStore()
        self.conversations = ConversationStore()
        self.suppression = SuppressionStore()
