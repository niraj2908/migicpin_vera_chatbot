import os
import time
from datetime import UTC, datetime
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from pydantic import ValidationError

from vera.api.schemas import (
    MAX_ACTIONS_PER_TICK,
    MAX_CONTEXT_PAYLOAD_BYTES,
    VALID_SCOPES,
    ContextPushRequest,
    ReplyRequest,
    TickRequest,
)
from vera.decision.compiler import decide
from vera.decision.reply_policy import decide_reply
from vera.domain.context import CategoryContext, CustomerContext, MerchantContext, TriggerContext
from vera.generation.brief import build_brief, for_reply
from vera.generation.composer import TemplateComposer, cta_fallback_text, get_default_composer
from vera.observability.logging import log_event
from vera.pipeline import compose_and_validate
from vera.security.validation import exceeds_byte_limit
from vera.state.store import Store, Turn

APP_START = time.monotonic()
TICK_LLM_BUDGET_SECONDS = 8.0  # of the documented 10s /v1/tick budget; leaves headroom for
# validation, state access, and serialization once an LLM call is in flight.

app = FastAPI(title="Vera Engine")
store = Store()


# FastAPI's own default for an unhandled exception is a PLAIN-TEXT "Internal Server Error" body
# (verified directly: no stack trace leaked, since debug is never enabled here — but also not
# JSON). The official judge harness's own reference bot client always does
# `json.loads(resp.read())` on every response; a plain-text 500 would fail that parse instead of
# being cleanly recognized as a failed action to skip. This restores a JSON-shaped error contract
# for the one path no route-level code controls, without changing any route's own logic or
# widening what's caught inside compose_and_validate() (which already fails closed internally).
@app.exception_handler(Exception)
async def _unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    log_event("unhandled_exception", path=request.url.path, exception_type=type(exc).__name__)
    return JSONResponse(status_code=500, content={"error": "internal_error"})


def _now_iso() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _parse_datetime(value: str) -> datetime:
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return datetime.now(UTC)


def _parse_tick_now(value: str) -> datetime | None:
    # Deliberately distinct from _parse_datetime above: that helper's wall-clock fallback is
    # correct for received_at (a display/logging timestamp), but the decision layer's staleness
    # check must never substitute real time for a malformed TickRequest.now -- the evaluator must
    # be able to control evaluation time deterministically. None here means "cannot determine",
    # which decide()'s own now=None default already treats as "skip the staleness check", not as
    # "assume not expired" or "assume expired" -- no policy is invented for the malformed case.
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


# Explicit GET+HEAD, not relying on any framework version's implicit HEAD-from-GET behavior
# (checked directly: reproducible even locally with the currently pinned FastAPI/Starlette
# versions that a HEAD request to a GET-only route returns 405). Free-tier uptime monitors
# (e.g. UptimeRobot's free plan) send HEAD, not GET, and cannot be configured otherwise --
# a 405 there is reported as the service being down, which is the actual root cause of a real
# production incident, not a hypothetical one.
@app.api_route("/v1/healthz", methods=["GET", "HEAD"])
def healthz() -> dict[str, Any]:
    counts = store.context.counts()
    contexts_loaded = {
        scope: counts.get(scope, 0) for scope in ("category", "merchant", "customer", "trigger")
    }
    return {
        "status": "ok",
        "uptime_seconds": int(time.monotonic() - APP_START),
        "contexts_loaded": contexts_loaded,
    }


@app.get("/v1/metadata")
def metadata() -> dict[str, Any]:
    team_members = [m.strip() for m in os.environ.get("VERA_TEAM_MEMBERS", "").split(",") if m.strip()]
    return {
        "team_name": os.environ.get("VERA_TEAM_NAME", "REPLACE_BEFORE_SUBMISSION"),
        "team_members": team_members,
        "model": os.environ.get("VERA_MODEL_NAME", "claude-sonnet-5"),
        "approach": (
            "Deterministic decision compiler selects at most one opportunity per merchant; "
            "an LLM composer (with a grounded deterministic template fallback) only phrases "
            "the already-decided message; an output firewall checks every generated numeric "
            "and price claim against the approved facts before a response can be returned."
        ),
        "contact_email": os.environ.get("VERA_CONTACT_EMAIL", "REPLACE_BEFORE_SUBMISSION"),
        "version": "0.2.0",
        "submitted_at": os.environ.get("VERA_SUBMITTED_AT", _now_iso()),
    }


@app.post("/v1/context")
async def push_context(request: Request) -> JSONResponse:
    raw_body = await request.body()
    if exceeds_byte_limit(raw_body, MAX_CONTEXT_PAYLOAD_BYTES):
        return JSONResponse(
            status_code=400,
            content={
                "accepted": False,
                "reason": "payload_too_large",
                "details": f"payload exceeds {MAX_CONTEXT_PAYLOAD_BYTES} bytes",
            },
        )

    try:
        body = ContextPushRequest.model_validate_json(raw_body)
    except ValidationError as exc:
        return JSONResponse(
            status_code=400,
            content={"accepted": False, "reason": "malformed_request", "details": str(exc)[:500]},
        )

    if body.scope not in VALID_SCOPES:
        return JSONResponse(
            status_code=400,
            content={
                "accepted": False,
                "reason": "invalid_scope",
                "details": f"unknown scope: {body.scope!r}",
            },
        )

    result = store.context.push(
        body.scope, body.context_id, body.version, body.payload, datetime.now(UTC)
    )

    log_event(
        "context_push",
        scope=body.scope,
        context_id=body.context_id,
        version=body.version,
        accepted=result.accepted,
        reason=result.reason,
    )

    if not result.accepted:
        return JSONResponse(
            status_code=409,
            content={
                "accepted": False,
                "reason": result.reason,
                "current_version": result.current_version,
            },
        )

    return JSONResponse(
        status_code=200,
        content={"accepted": True, "ack_id": result.ack_id, "stored_at": result.stored_at},
    )


# Deliberately plain `def`, not `async def`: this handler calls compose_and_validate(), which
# can make a blocking synchronous provider SDK call (no await) taking several seconds. An
# `async def` route with a blocking call inside it stalls the whole event loop for that
# duration — confirmed empirically, a concurrent /v1/healthz during that window would also hang,
# risking the judge's 3-consecutive-failures disqualification. A plain `def` route runs in
# FastAPI's own thread pool instead, so slow LLM calls no longer block other requests.
@app.post("/v1/tick")
def tick(body: TickRequest) -> dict[str, Any]:
    start = time.monotonic()
    actions: list[dict[str, Any]] = []
    tick_now = _parse_tick_now(body.now)

    for trigger_id in body.available_triggers:
        if len(actions) >= MAX_ACTIONS_PER_TICK:
            break

        trigger_raw = store.context.get("trigger", trigger_id)
        if trigger_raw is None:
            continue
        trigger = TriggerContext(trigger_raw)

        merchant_raw = store.context.get("merchant", trigger.merchant_id)
        if merchant_raw is None:
            continue
        merchant = MerchantContext(merchant_raw)

        category_raw = store.context.get("category", merchant.category_slug)
        if category_raw is None:
            continue
        category = CategoryContext(category_raw)

        customer: CustomerContext | None = None
        if trigger.customer_id:
            customer_raw = store.context.get("customer", trigger.customer_id)
            if customer_raw is not None:
                customer = CustomerContext(customer_raw)

        already_suppressed = store.suppression.is_used(
            merchant.merchant_id, trigger.suppression_key
        ) or store.suppression.is_merchant_suppressed(merchant.merchant_id)
        decision = decide(
            merchant, trigger, customer, already_suppressed=already_suppressed, category=category, now=tick_now
        )

        log_event(
            "tick_decision",
            trigger_id=trigger.id,
            merchant_id=merchant.merchant_id,
            send=decision.send,
            dominant_signal=decision.dominant_signal,
            confidence=round(decision.confidence, 3),
        )

        if not decision.send:
            continue

        conversation_id = f"conv_{merchant.merchant_id}_{trigger.id}"
        # Atomic claim BEFORE any composition work — see ConversationStore.try_reserve's
        # docstring for the concurrent-duplicate-send bug this closes (empirically reproduced).
        if not store.conversations.try_reserve(
            conversation_id, merchant.merchant_id, customer.customer_id if customer else None, trigger.id
        ):
            continue  # another concurrent tick already claimed this (merchant, trigger)

        brief = build_brief(decision, merchant, category, customer)

        elapsed = time.monotonic() - start
        composer = get_default_composer() if elapsed < TICK_LLM_BUDGET_SECONDS else TemplateComposer()
        result = compose_and_validate(brief, composer)
        body_text = result.message

        log_event(
            "tick_compose",
            conversation_id=conversation_id,
            used_fallback=result.used_fallback,
            fallback_reason=result.fallback_reason,
            cta_corrected=result.cta_corrected,
        )

        if not body_text.strip():
            # Total failure: even the deterministic fallback couldn't produce a firewall-clean
            # message. Never ship an empty/malformed body — release the reservation so a later
            # tick may retry this (merchant, trigger), and skip this one.
            store.conversations.release(conversation_id)
            continue

        store.conversations.attach_brief(conversation_id, brief)
        store.suppression.mark_used(merchant.merchant_id, trigger.suppression_key)

        # Same recipient logic as TemplateComposer's greeting: a customer-facing send must open
        # with the customer's name, not the merchant owner's — found via the same real
        # end-to-end verification that caught the equivalent bug in TemplateComposer.
        template_subject = brief.customer_name or merchant.owner_first_name or merchant.name
        template_params = [template_subject, *brief.facts[:2]]
        cta_phrase = cta_fallback_text(brief)
        if cta_phrase:
            template_params.append(cta_phrase)

        actions.append(
            {
                "conversation_id": conversation_id,
                "merchant_id": merchant.merchant_id,
                "customer_id": customer.customer_id if customer else None,
                "send_as": decision.send_as,
                "trigger_id": trigger.id,
                "template_name": f"vera_{decision.action_type}_v1",
                "template_params": template_params,
                "body": body_text,
                "cta": decision.cta,
                "suppression_key": decision.suppression_key,
                "rationale": decision.reason,
            }
        )

        log_event(
            "tick_action",
            conversation_id=conversation_id,
            merchant_id=merchant.merchant_id,
            trigger_id=trigger.id,
            cta=decision.cta,
        )

    return {"actions": actions}


@app.post("/v1/teardown")
def teardown() -> dict[str, Any]:
    """Not part of the 5 scored endpoints (challenge-testing-brief.md SS2) but documented in
    SS11's privacy requirement: 'Bots must not persist context data after the test ends. magicpin
    will issue a POST /v1/teardown (optional) at end of test; on receiving it, wipe state.' No
    request body is documented, so this accepts none and always succeeds -- there's no
    partial-failure mode for wiping in-memory state.
    """
    store.teardown()
    log_event("teardown")
    return {"status": "ok"}


@app.post("/v1/reply")
def reply(body: ReplyRequest) -> dict[str, Any]:
    conv = store.conversations.get(body.conversation_id)
    if conv is None:
        log_event("reply_rejected", conversation_id=body.conversation_id, reason="unknown conversation_id")
        return {"action": "end", "rationale": "unknown conversation_id"}

    # Serializes all processing of this one conversation_id's turns (read state, classify,
    # mutate, compose) without blocking replies on any other conversation — see
    # ConversationStore.turn_lock's docstring for the concrete misclassification bug this closes.
    with store.conversations.turn_lock(body.conversation_id):
        if conv.status != "active":
            reason = f"conversation already {conv.status}"
            log_event("reply_rejected", conversation_id=body.conversation_id, reason=reason)
            return {"action": "end", "rationale": reason}

        conv.turns.append(Turn(from_role=body.from_role, message=body.message, ts=_parse_datetime(body.received_at)))

        reply_decision = decide_reply(body.message, conv.last_incoming_message, conv.auto_reply_hits)
        conv.last_incoming_message = body.message

        log_event(
            "reply_decision",
            conversation_id=body.conversation_id,
            kind=reply_decision.kind,
            action=reply_decision.action,
        )

        if reply_decision.action == "wait":
            conv.auto_reply_hits += 1
            conv.status = "waiting"
            return {
                "action": "wait",
                "wait_seconds": reply_decision.wait_seconds,
                "rationale": reply_decision.reason,
            }

        if reply_decision.action == "end":
            conv.status = "ended"
            if reply_decision.kind == "hostile_optout":
                store.suppression.suppress_merchant(conv.merchant_id)
            return {"action": "end", "rationale": reply_decision.reason}

        # action == "send"
        if conv.brief is None or reply_decision.reply_intent is None:
            conv.status = "ended"
            return {"action": "end", "rationale": "no composition context available for this conversation"}

        reply_brief = for_reply(conv.brief, reply_decision.reply_intent)
        result = compose_and_validate(reply_brief, get_default_composer())

        log_event(
            "reply_compose",
            conversation_id=body.conversation_id,
            used_fallback=result.used_fallback,
            fallback_reason=result.fallback_reason,
            cta_corrected=result.cta_corrected,
        )

        if not result.message.strip():
            # Total failure: never ship an empty/malformed body. Ending is the safe choice —
            # better than a send the contract would flag as malformed.
            conv.status = "ended"
            return {"action": "end", "rationale": "unable to produce a grounded, firewall-clean reply"}

        if result.message in conv.sent_bodies:
            # Contract (challenge-testing-brief.md §10): a verbatim repeat of a body already sent
            # in this conversation is flagged "anti-repetition" and penalized (-2), independent of
            # any other quality signal. No retry/re-prompt loop exists in this pipeline (by
            # design — see compose_and_validate's docstring), so there is no next candidate to try
            # instead; ending is the same safe choice already used for an empty/unsendable body,
            # applied to a verbatim-duplicate one.
            conv.status = "ended"
            log_event("reply_rejected", conversation_id=body.conversation_id, reason="verbatim repeat of a body already sent in this conversation")
            return {"action": "end", "rationale": "already said this in this conversation; nothing new to add"}

        conv.sent_bodies.add(result.message)
        return {"action": "send", "body": result.message, "cta": reply_brief.cta, "rationale": reply_decision.reason}
