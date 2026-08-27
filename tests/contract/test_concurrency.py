"""Regression test for a real bug found via empirical reproduction against a live server, not
assumed: /v1/tick and /v1/reply call compose_and_validate(), which can make a blocking
synchronous provider SDK call (several seconds, no await). Declaring those routes `async def`
stalled the whole event loop for that duration — verified with a real uvicorn process: a
concurrent /v1/healthz took 2.7s to answer a request that does zero work, and after changing the
routes to plain `def`, the same request answered in 0.01s. Since the judge polls /v1/healthz
every 60s and disqualifies after 3 consecutive failures, a slow real LLM call could have caused
a false disqualification.

Note on test methodology: FastAPI's TestClient does not faithfully reproduce this class of bug
(each call appears to get its own event-loop portal, so blocking never manifests through it even
with the bug present — confirmed by deliberately reintroducing the bug and observing the
TestClient-based timing test still pass). The reliable, fast regression guard is therefore a
direct structural check on the registered route functions, not a timing test.
"""

import asyncio
import concurrent.futures as cf
import copy
import json
import threading
from datetime import UTC, datetime
from pathlib import Path

from fastapi.testclient import TestClient

import vera.api.app as app_module
from vera.api.app import app
from vera.state.store import ContextStore

DATASET_DIR = Path(__file__).parent.parent.parent / "docs" / "challenge-package" / "dataset"
client = TestClient(app)


def test_tick_and_reply_are_not_async_def() -> None:
    """These two call compose_and_validate(), which can block on a synchronous provider SDK
    call — they must run in FastAPI's thread pool (plain `def`), not the event loop (`async
    def`), or a slow LLM call blocks every other concurrent request including /v1/healthz."""
    assert not asyncio.iscoroutinefunction(app_module.tick), (
        "/v1/tick must be a plain `def`, not `async def` — see this file's module docstring"
    )
    assert not asyncio.iscoroutinefunction(app_module.reply), (
        "/v1/reply must be a plain `def`, not `async def` — see this file's module docstring"
    )


def test_push_context_is_still_correctly_async() -> None:
    """The one route with genuine async I/O (`await request.body()`) should stay `async def` —
    this isn't blanket "everything must be sync", it's specifically about routes with blocking
    synchronous calls inside them."""
    assert asyncio.iscoroutinefunction(app_module.push_context)


def test_healthz_and_metadata_are_plain_def() -> None:
    """No blocking work either, but consistently plain `def` avoids ever reintroducing this bug
    if blocking work is added to them later."""
    assert not asyncio.iscoroutinefunction(app_module.healthz)
    assert not asyncio.iscoroutinefunction(app_module.metadata)


# =============================================================================
# Real concurrent-request regressions for two further races, both found via genuine concurrent
# HTTP evidence against a live uvicorn server during the production-readiness audit (not assumed
# from reading the code): plain `def` routes run in FastAPI's real OS thread pool, so a
# check-then-act sequence with no lock around it is a genuine race, not merely theoretical, once
# real work (building a brief, calling a composer) sits between the check and the write.
# =============================================================================


def test_context_store_push_version_race_is_atomic_under_forced_concurrency() -> None:
    """A barrier forces two real threads into ContextStore.push()'s read-compare-write section at
    the same instant, for a version-3 and a version-4 push racing from the same base version.
    Without the lock in ContextStore.push, CPython's GIL makes this hard but not impossible to
    lose; the contract requires version 4 to *always* win, not just usually."""
    lost_updates = 0
    rounds = 100
    for _ in range(rounds):
        store = ContextStore()
        store.push("merchant", "race", 1, {"marker": "base"}, datetime.now(UTC))
        barrier = threading.Barrier(2)

        def push_v3(store: ContextStore = store, barrier: threading.Barrier = barrier) -> None:
            barrier.wait()
            store.push("merchant", "race", 3, {"marker": "v3"}, datetime.now(UTC))

        def push_v4(store: ContextStore = store, barrier: threading.Barrier = barrier) -> None:
            barrier.wait()
            store.push("merchant", "race", 4, {"marker": "v4"}, datetime.now(UTC))

        t1 = threading.Thread(target=push_v3)
        t2 = threading.Thread(target=push_v4)
        t1.start()
        t2.start()
        t1.join()
        t2.join()

        if store.version_of("merchant", "race") != 4:
            lost_updates += 1

    assert lost_updates == 0, f"{lost_updates}/{rounds} rounds: version 3 beat version 4"


def _pharmacies_payloads(suffix: str) -> tuple[str, str, dict, dict, dict]:
    cat = json.loads((DATASET_DIR / "categories" / "pharmacies.json").read_text())
    merchants = json.loads((DATASET_DIR / "merchants_seed.json").read_text())["merchants"]
    m = copy.deepcopy(next(x for x in merchants if x["merchant_id"] == "m_009_apollo_pharmacy_jaipur"))
    triggers = json.loads((DATASET_DIR / "triggers_seed.json").read_text())["triggers"]
    t = copy.deepcopy(next(x for x in triggers if x["kind"] == "supply_alert"))

    mid, tid = f"{m['merchant_id']}_{suffix}", f"{t['id']}_{suffix}"
    m["merchant_id"] = mid
    m["conversation_history"] = []  # guarantee a genuine, unsuppressed send
    t["id"] = tid
    t["merchant_id"] = mid
    t["suppression_key"] = f"{t['suppression_key']}:{suffix}"  # isolate from the dataset-level
    # cross-merchant suppression_key finding (documented separately) so this test measures only
    # the reservation race.
    return mid, tid, cat, m, t


def _push_pharmacies_scenario(suffix: str) -> tuple[str, str]:
    mid, tid, cat, m, t = _pharmacies_payloads(suffix)
    now = "2026-04-26T10:00:00Z"
    client.post("/v1/context", json={"scope": "category", "context_id": "pharmacies", "version": 1, "payload": cat, "delivered_at": now})
    client.post("/v1/context", json={"scope": "merchant", "context_id": mid, "version": 1, "payload": m, "delivered_at": now})
    client.post("/v1/context", json={"scope": "trigger", "context_id": tid, "version": 1, "payload": t, "delivered_at": now})
    return mid, tid


def test_concurrent_ticks_for_same_merchant_trigger_produce_at_most_one_action() -> None:
    """Reproduces, in-process via TestClient's real thread pool dispatch, the exact bug found
    against a live uvicorn server: 1/15 rounds at 12-way concurrency returned two actions for one
    (merchant, trigger) before ConversationStore.try_reserve existed. Runs enough rounds to give a
    prior-bug reproduction a real chance to resurface if the fix regresses."""
    duplicate_rounds = 0
    rounds = 20
    for i in range(rounds):
        _mid, tid = _push_pharmacies_scenario(f"tick{i}")

        def fire(_: int, tid: str = tid) -> list[dict]:
            resp = client.post("/v1/tick", json={"now": "2026-04-26T10:05:00Z", "available_triggers": [tid]})
            return list(resp.json().get("actions", []))

        with cf.ThreadPoolExecutor(max_workers=12) as ex:
            results = list(ex.map(fire, range(12)))

        total_actions = sum(len(a) for a in results)
        if total_actions > 1:
            duplicate_rounds += 1

    assert duplicate_rounds == 0, f"{duplicate_rounds}/{rounds} rounds returned more than one action for a single (merchant, trigger)"


def test_concurrent_identical_replies_produce_at_most_one_send() -> None:
    """Reproduces the reply-side race found against a live server: without a per-conversation
    lock, concurrent identical replies raced on conv.last_incoming_message/status, causing one
    thread to misclassify a genuine commitment message as a repeated auto-reply while another
    concurrently returned 'send' for the same incoming turn. At most one 'send' must ever occur
    per round."""
    duplicate_send_rounds = 0
    rounds = 15
    for i in range(rounds):
        mid, tid = _push_pharmacies_scenario(f"reply{i}")
        tick_resp = client.post("/v1/tick", json={"now": "2026-04-26T10:05:00Z", "available_triggers": [tid]}).json()
        actions = tick_resp.get("actions", [])
        assert actions, "setup: expected a genuine send from a fresh, never-discussed supply_alert"
        conv_id = actions[0]["conversation_id"]

        def fire(_: int, conv_id: str = conv_id, mid: str = mid) -> dict:
            resp = client.post(
                "/v1/reply",
                json={
                    "conversation_id": conv_id,
                    "merchant_id": mid,
                    "customer_id": None,
                    "from_role": "merchant",
                    "message": "Ok lets do it. Whats next?",
                    "received_at": "2026-04-26T10:06:00Z",
                    "turn_number": 2,
                },
            )
            return resp.json()

        with cf.ThreadPoolExecutor(max_workers=8) as ex:
            results = list(ex.map(fire, range(8)))

        send_count = sum(1 for r in results if r.get("action") == "send")
        if send_count > 1:
            duplicate_send_rounds += 1

    assert duplicate_send_rounds == 0, f"{duplicate_send_rounds}/{rounds} rounds produced more than one 'send' for one incoming reply"
