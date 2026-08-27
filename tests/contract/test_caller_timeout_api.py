"""API-level regression tests for the caller-side composer timeout: healthz isolation, concurrent
tick safety, and reservation/release/retry state semantics, all exercised through real HTTP
(TestClient, which dispatches these plain `def` routes via FastAPI's real thread pool) against a
composer that genuinely blocks past the (monkeypatched, shortened) caller deadline. No real
network, no Gemini quota.
"""

import concurrent.futures as cf
import copy
import json
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import vera.api.app as app_module
import vera.pipeline as pipeline_module
from vera.api.app import app

DATASET_DIR = Path(__file__).parent.parent.parent / "docs" / "challenge-package" / "dataset"
client = TestClient(app)
NOW = "2026-04-26T10:00:00Z"


class _SleepThenSucceedComposer:
    def __init__(self, seconds: float) -> None:
        self.seconds = seconds

    def compose(self, brief):  # type: ignore[no-untyped-def]
        time.sleep(self.seconds)
        return f"{brief.merchant_name}, {brief.facts[0] if brief.facts else 'update'}. Reply to confirm."


@pytest.fixture(autouse=True)
def _short_caller_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(pipeline_module, "_CALLER_TIMEOUT_SECONDS", 0.5)


def _push(scope: str, cid: str, version: int, payload: dict):
    return client.post(
        "/v1/context",
        json={"scope": scope, "context_id": cid, "version": version, "payload": payload, "delivered_at": NOW},
    )


def _fresh_pharmacy(suffix: str):
    cat = json.loads((DATASET_DIR / "categories" / "pharmacies.json").read_text())
    merchants = json.loads((DATASET_DIR / "merchants_seed.json").read_text())["merchants"]
    m = copy.deepcopy(next(x for x in merchants if x["merchant_id"] == "m_009_apollo_pharmacy_jaipur"))
    triggers = json.loads((DATASET_DIR / "triggers_seed.json").read_text())["triggers"]
    t = copy.deepcopy(next(x for x in triggers if x["kind"] == "supply_alert"))
    mid, tid = f"{m['merchant_id']}_{suffix}", f"{t['id']}_{suffix}"
    m["merchant_id"] = mid
    m["conversation_history"] = []
    t["id"] = tid
    t["merchant_id"] = mid
    t["suppression_key"] = f"{t['suppression_key']}:{suffix}"
    _push("category", "pharmacies", 1, cat)
    _push("merchant", mid, 1, m)
    _push("trigger", tid, 1, t)
    return mid, tid


def test_6_healthz_remains_responsive_while_composer_exceeds_caller_deadline(monkeypatch: pytest.MonkeyPatch) -> None:
    """Composer sleeps 3s -- well past the 0.5s patched deadline, so /v1/tick itself must return
    (with a fallback) close to 0.5s, and /v1/healthz fired concurrently must stay fast throughout,
    including during the period after tick() has already returned but the abandoned background
    compose call is still running in _COMPOSE_EXECUTOR."""
    monkeypatch.setattr(app_module, "get_default_composer", lambda: _SleepThenSucceedComposer(3.0))
    _mid, tid = _fresh_pharmacy("t6")

    with cf.ThreadPoolExecutor(max_workers=6) as ex:
        tick_start = time.monotonic()
        tick_future = ex.submit(client.post, "/v1/tick", json={"now": NOW, "available_triggers": [tid]})
        healthz_latencies = []
        for _ in range(6):
            start = time.monotonic()
            r = ex.submit(client.get, "/v1/healthz").result()
            healthz_latencies.append(time.monotonic() - start)
            assert r.status_code == 200
            time.sleep(0.15)
        tick_resp = tick_future.result()
        tick_elapsed = time.monotonic() - tick_start

    assert tick_elapsed < 2.0, f"tick should return near the 0.5s caller deadline, not wait for the 3s composer, took {tick_elapsed:.2f}s"
    assert tick_resp.status_code == 200
    assert max(healthz_latencies) < 0.5, f"healthz must stay fast even while a composer call has been abandoned in the background: {healthz_latencies}"


def test_7_concurrent_ticks_remain_safe_with_the_new_executor_path(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(app_module, "get_default_composer", lambda: _SleepThenSucceedComposer(0.05))
    dup_rounds = 0
    rounds = 10
    for i in range(rounds):
        _mid, tid = _fresh_pharmacy(f"t7_{i}")
        with cf.ThreadPoolExecutor(max_workers=10) as ex:
            results = list(ex.map(
                lambda _, tid=tid: client.post("/v1/tick", json={"now": NOW, "available_triggers": [tid]}).json().get("actions", []),
                range(10),
            ))
        total = sum(len(r) for r in results)
        if total > 1:
            dup_rounds += 1
    assert dup_rounds == 0, f"{dup_rounds}/{rounds} rounds produced more than one action under concurrent load through the new executor path"


def test_8_no_duplicate_or_stuck_state_when_composer_times_out(monkeypatch: pytest.MonkeyPatch) -> None:
    """A composer that always exceeds the (patched) deadline still falls through to the working
    deterministic template, so the first tick correctly sends once via that fallback. The point
    of this test is what happens next: state must not be left duplicated or stuck -- a second
    identical tick for the same (merchant, trigger) must be cleanly suppressed, not re-sent and
    not left in limbo."""
    monkeypatch.setattr(app_module, "get_default_composer", lambda: _SleepThenSucceedComposer(3.0))
    _mid, tid = _fresh_pharmacy("t8")

    first = client.post("/v1/tick", json={"now": NOW, "available_triggers": [tid]}).json()["actions"]
    assert len(first) == 1, "caller timeout on the primary composer must still fall through to a working deterministic fallback"

    second = client.post("/v1/tick", json={"now": NOW, "available_triggers": [tid]}).json()["actions"]
    assert second == [], "already acted on for this (merchant, trigger) -- must not duplicate, and must not be stuck unresolved either"


def test_9_successful_retry_after_a_previous_caller_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    """The realistic judge-facing scenario: an earlier tick's composition genuinely failed the
    firewall after a caller timeout produced no usable message (forcing a release), and a LATER
    tick for the same (merchant, trigger) must be allowed to try again and succeed."""
    _mid, tid = _fresh_pharmacy("t9")

    # Force total failure (not just a slow-but-eventually-valid fallback) by making the
    # deterministic TemplateComposer fallback itself unable to produce anything -- simplest
    # honest way: monkeypatch pipeline._FALLBACK's compose to also fail firewall validation,
    # scoped to only the first call, so release() is exercised precisely once.
    import vera.pipeline as pipeline_mod

    call_state = {"count": 0}
    real_fallback_compose = pipeline_mod._FALLBACK.compose

    def flaky_fallback_compose(brief):  # type: ignore[no-untyped-def]
        call_state["count"] += 1
        if call_state["count"] == 1:
            return "http://evil.example.com totally-fabricated-unsafe-output"  # fails firewall (URL)
        return real_fallback_compose(brief)

    monkeypatch.setattr(pipeline_mod._FALLBACK, "compose", flaky_fallback_compose)
    monkeypatch.setattr(app_module, "get_default_composer", lambda: _SleepThenSucceedComposer(3.0))

    first = client.post("/v1/tick", json={"now": NOW, "available_triggers": [tid]}).json()["actions"]
    assert first == [], "total failure (caller timeout + even the fallback rejected) must send nothing"

    second = client.post("/v1/tick", json={"now": NOW, "available_triggers": [tid]}).json()["actions"]
    assert len(second) == 1, "a later tick for the same (merchant, trigger) must be allowed to retry and succeed once the fallback works again"
