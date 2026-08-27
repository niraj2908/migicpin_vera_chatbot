import copy
import json

import pytest
from fastapi.testclient import TestClient

from vera.api.app import app

client = TestClient(app)
DELIVERED_AT = "2026-04-26T10:00:00Z"


def _push(scope: str, context_id: str, version: int, payload: dict):
    return client.post(
        "/v1/context",
        json={"scope": scope, "context_id": context_id, "version": version, "payload": payload, "delivered_at": DELIVERED_AT},
    )


def test_context_malformed_json_body_fails_safely() -> None:
    resp = client.post("/v1/context", content=b"{not valid json", headers={"Content-Type": "application/json"})
    assert resp.status_code in (400, 422)
    body = resp.json()
    assert "Traceback" not in json.dumps(body)


def test_context_missing_required_field_fails_safely() -> None:
    resp = client.post("/v1/context", json={"scope": "merchant", "version": 1, "payload": {}})
    assert resp.status_code == 400
    assert resp.json()["reason"] == "malformed_request"
    assert "Traceback" not in json.dumps(resp.json())


def test_context_wrong_type_for_version_fails_safely() -> None:
    resp = client.post(
        "/v1/context",
        json={"scope": "merchant", "context_id": "m1", "version": "not-an-int", "payload": {}, "delivered_at": DELIVERED_AT},
    )
    assert resp.status_code == 400


def test_reply_oversized_message_field_is_rejected() -> None:
    resp = client.post(
        "/v1/reply",
        json={
            "conversation_id": "conv_x",
            "merchant_id": "m1",
            "customer_id": None,
            "from_role": "merchant",
            "message": "x" * 25_000,
            "received_at": DELIVERED_AT,
            "turn_number": 1,
        },
    )
    assert resp.status_code == 422


def test_tick_with_too_many_available_triggers_is_rejected() -> None:
    resp = client.post("/v1/tick", json={"now": DELIVERED_AT, "available_triggers": [f"t{i}" for i in range(500)]})
    assert resp.status_code == 422


def test_offer_title_with_url_never_reaches_the_response(restaurants_category, restaurant_merchants, festival_trigger) -> None:
    merchant = copy.deepcopy(restaurant_merchants[0])
    merchant["offers"] = [{"id": "o_url", "title": "50% off, book at https://evil.example/promo", "status": "active"}]
    trigger = copy.deepcopy(festival_trigger)
    trigger["id"] = "trg_url_test"
    trigger["merchant_id"] = merchant["merchant_id"]
    trigger["payload"]["days_until"] = 3
    trigger["suppression_key"] = f"festival:diwali:2026:{merchant['merchant_id']}"

    _push("category", "restaurants", 1, restaurants_category)
    _push("merchant", merchant["merchant_id"], 1, merchant)
    _push("trigger", trigger["id"], 1, trigger)

    resp = client.post("/v1/tick", json={"now": DELIVERED_AT, "available_triggers": [trigger["id"]]})
    actions = resp.json()["actions"]
    assert len(actions) == 1
    assert "http" not in actions[0]["body"]
    assert "evil.example" not in actions[0]["body"]


def test_no_configured_secret_value_ever_appears_in_any_response(
    monkeypatch: pytest.MonkeyPatch, restaurants_category, restaurant_merchants, festival_trigger
) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-fake-secret-canary-000")
    monkeypatch.setenv("GEMINI_API_KEY", "test-fake-secret-canary-111")

    merchant = restaurant_merchants[0]
    trigger = copy.deepcopy(festival_trigger)
    trigger["id"] = "trg_secret_scan"
    trigger["merchant_id"] = merchant["merchant_id"]
    trigger["payload"]["days_until"] = 3
    trigger["suppression_key"] = f"festival:diwali:2026:{merchant['merchant_id']}"

    responses = [
        client.get("/v1/healthz"),
        client.get("/v1/metadata"),
        _push("category", "restaurants", 1, restaurants_category),
        _push("merchant", merchant["merchant_id"], 1, merchant),
        _push("trigger", trigger["id"], 1, trigger),
        client.post("/v1/tick", json={"now": DELIVERED_AT, "available_triggers": [trigger["id"]]}),
    ]

    for resp in responses:
        body_text = resp.text
        assert "test-fake-secret-canary-000" not in body_text
        assert "test-fake-secret-canary-111" not in body_text


def test_replayed_context_push_is_idempotent_not_duplicated(restaurants_category) -> None:
    """Contract: same-version repost is an idempotent no-op (200), not a 409 -- the 409 is
    reserved for a strictly stale/lower version. Idempotent still means no duplicate entry."""
    first = _push("category", "restaurants", 1, restaurants_category)
    replay = _push("category", "restaurants", 1, restaurants_category)
    assert first.status_code == 200
    assert replay.status_code == 200
    assert replay.json()["ack_id"] == first.json()["ack_id"]
    counts = client.get("/v1/healthz").json()["contexts_loaded"]
    assert counts["category"] == 1
