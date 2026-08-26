from fastapi.testclient import TestClient

from vera.api.app import app

client = TestClient(app)


def test_compose_returns_send_true_for_relevant_festival() -> None:
    payload = {
        "merchant": {
            "merchant_id": "m1",
            "name": "Spice Villa",
            "category": "restaurant",
            "offers": [{"name": "Diwali Thali", "discount_pct": 20}],
            "rating": 4.3,
        },
        "trigger": {
            "trigger_type": "festival",
            "event": "Diwali",
            "days_to_event": 2,
            "category_relevance": 0.9,
        },
    }
    response = client.post("/v1/compose", json=payload)
    assert response.status_code == 200
    body = response.json()
    assert body["send"] is True
    assert "Diwali" in body["message"] or "20" in body["message"]
    assert body["suppression_key"] == "m1:festival:Diwali"
    assert body["cta"]
    assert body["rationale"]


def test_compose_returns_send_false_for_irrelevant_trigger() -> None:
    payload = {
        "merchant": {"merchant_id": "m2", "name": "Plain Store", "category": "hardware"},
        "trigger": {
            "trigger_type": "festival",
            "event": "Diwali",
            "days_to_event": 20,
            "category_relevance": 0.1,
        },
    }
    response = client.post("/v1/compose", json=payload)
    assert response.status_code == 200
    body = response.json()
    assert body["send"] is False
    assert body["message"] == ""
