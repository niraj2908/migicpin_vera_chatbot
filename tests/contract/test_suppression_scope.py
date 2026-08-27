"""Regression tests for the P1 finding: SuppressionStore must be scoped by
(merchant_id, suppression_key), not by suppression_key alone.

Evidence (see the production-readiness/suppression-scope audit): 2 of 25 real triggers
(research_digest_release, supply_alert) supply a suppression_key with no merchant identity
embedded in it, unlike the other 23 and unlike generate_dataset.py's own generator convention.
A bare-string suppression set would let Merchant A's send silently suppress Merchant B's
legitimate, never-before-seen action if a fresh judge scenario reuses one of those keys across
merchants -- most concerning for supply_alert, a compliance/safety-relevant message.

Uses the real, unmodified pharmacies category/merchant/trigger seed data and genuine HTTP
requests through TestClient (which dispatches these plain `def` routes via FastAPI's real thread
pool, same as a live server), not mocks.
"""

import concurrent.futures as cf
import copy
import json
from pathlib import Path

from fastapi.testclient import TestClient

from vera.api.app import app

DATASET_DIR = Path(__file__).parent.parent.parent / "docs" / "challenge-package" / "dataset"
client = TestClient(app)
NOW = "2026-04-26T10:00:00Z"


def _push(scope: str, cid: str, version: int, payload: dict):
    return client.post(
        "/v1/context",
        json={"scope": scope, "context_id": cid, "version": version, "payload": payload, "delivered_at": NOW},
    )


def _real_pharmacies_payloads() -> tuple[dict, dict, dict]:
    cat = json.loads((DATASET_DIR / "categories" / "pharmacies.json").read_text())
    merchants = json.loads((DATASET_DIR / "merchants_seed.json").read_text())["merchants"]
    m = copy.deepcopy(next(x for x in merchants if x["merchant_id"] == "m_009_apollo_pharmacy_jaipur"))
    triggers = json.loads((DATASET_DIR / "triggers_seed.json").read_text())["triggers"]
    t = copy.deepcopy(next(x for x in triggers if x["kind"] == "supply_alert"))
    return cat, m, t


def _tick(trigger_ids: list[str]) -> list[dict]:
    resp = client.post("/v1/tick", json={"now": NOW, "available_triggers": trigger_ids})
    return list(resp.json().get("actions", []))


def test_1_same_merchant_same_suppression_key_suppressed_after_first_send() -> None:
    cat, m, t = _real_pharmacies_payloads()
    m["conversation_history"] = []
    _push("category", "pharmacies", 1, cat)
    _push("merchant", m["merchant_id"], 1, m)
    _push("trigger", t["id"], 1, t)

    first = _tick([t["id"]])
    assert len(first) == 1

    second = _tick([t["id"]])
    assert second == []  # already acted on for this merchant


def test_2_different_merchants_same_suppression_key_are_not_cross_suppressed() -> None:
    """The core fix: two distinct, legitimate merchants must not share suppression state just
    because a trigger's own suppression_key string happens to collide -- exactly the real
    trg_018-shaped scenario a fresh judge run could introduce twice."""
    cat, base_m, base_t = _real_pharmacies_payloads()
    shared_key = base_t["suppression_key"]  # the real "alert:atorvastatin:2026-04", unmodified
    _push("category", "pharmacies", 1, cat)

    m1 = copy.deepcopy(base_m)
    m1["merchant_id"] = "m_scope_test_1"
    m1["conversation_history"] = []
    t1 = copy.deepcopy(base_t)
    t1["id"] = "trg_scope_test_1"
    t1["merchant_id"] = m1["merchant_id"]
    t1["suppression_key"] = shared_key

    m2 = copy.deepcopy(base_m)
    m2["merchant_id"] = "m_scope_test_2"
    m2["conversation_history"] = []
    t2 = copy.deepcopy(base_t)
    t2["id"] = "trg_scope_test_2"
    t2["merchant_id"] = m2["merchant_id"]
    t2["suppression_key"] = shared_key  # identical key, different merchant

    _push("merchant", m1["merchant_id"], 1, m1)
    _push("trigger", t1["id"], 1, t1)
    _push("merchant", m2["merchant_id"], 1, m2)
    _push("trigger", t2["id"], 1, t2)

    actions_m1 = _tick([t1["id"]])
    assert len(actions_m1) == 1, "merchant 1's legitimate, never-before-seen alert must send"

    actions_m2 = _tick([t2["id"]])
    assert len(actions_m2) == 1, "merchant 2 must NOT be suppressed by merchant 1's unrelated send"
    assert actions_m2[0]["merchant_id"] == m2["merchant_id"]


def test_3_same_merchant_different_trigger_different_key_both_send() -> None:
    """Merchant-scoping must still discriminate by key -- two genuinely different opportunities
    for the SAME merchant must not suppress each other."""
    cat, base_m, base_t = _real_pharmacies_payloads()
    _push("category", "pharmacies", 1, cat)

    m = copy.deepcopy(base_m)
    m["merchant_id"] = "m_scope_test_3"
    m["conversation_history"] = []
    _push("merchant", m["merchant_id"], 1, m)

    t1 = copy.deepcopy(base_t)
    t1["id"] = "trg_scope_test_3a"
    t1["merchant_id"] = m["merchant_id"]
    t1["suppression_key"] = "alert:moleculeA:2026-04"
    t1["payload"]["molecule"] = "moleculeA"

    t2 = copy.deepcopy(base_t)
    t2["id"] = "trg_scope_test_3b"
    t2["merchant_id"] = m["merchant_id"]
    t2["suppression_key"] = "alert:moleculeB:2026-04"
    t2["payload"]["molecule"] = "moleculeB"

    _push("trigger", t1["id"], 1, t1)
    _push("trigger", t2["id"], 1, t2)

    actions = _tick([t1["id"], t2["id"]])
    assert len(actions) == 2, "two distinct suppression_keys for the same merchant must both send"


def test_4_concurrent_same_merchant_duplicate_tick_at_most_one_send() -> None:
    cat, base_m, base_t = _real_pharmacies_payloads()
    _push("category", "pharmacies", 1, cat)
    m = copy.deepcopy(base_m)
    m["merchant_id"] = "m_scope_test_4"
    m["conversation_history"] = []
    t = copy.deepcopy(base_t)
    t["id"] = "trg_scope_test_4"
    t["merchant_id"] = m["merchant_id"]
    t["suppression_key"] = "alert:scope4:2026-04"
    _push("merchant", m["merchant_id"], 1, m)
    _push("trigger", t["id"], 1, t)

    with cf.ThreadPoolExecutor(max_workers=10) as ex:
        results = list(ex.map(lambda _: _tick([t["id"]]), range(10)))

    total_actions = sum(len(r) for r in results)
    assert total_actions == 1, f"expected exactly 1 send across 10 concurrent identical ticks, got {total_actions}"


def test_5_concurrent_different_merchants_identical_suppression_key_both_survive() -> None:
    """Fires the two merchants' ticks genuinely concurrently (not sequentially, unlike test 2) to
    confirm the merchant-scoped fix also holds under real thread concurrency, not just in a
    single-threaded call order."""
    cat, base_m, base_t = _real_pharmacies_payloads()
    shared_key = "alert:scope5:2026-04"
    _push("category", "pharmacies", 1, cat)

    merchants_triggers = []
    for i in (1, 2):
        m = copy.deepcopy(base_m)
        m["merchant_id"] = f"m_scope_test_5_{i}"
        m["conversation_history"] = []
        t = copy.deepcopy(base_t)
        t["id"] = f"trg_scope_test_5_{i}"
        t["merchant_id"] = m["merchant_id"]
        t["suppression_key"] = shared_key
        _push("merchant", m["merchant_id"], 1, m)
        _push("trigger", t["id"], 1, t)
        merchants_triggers.append((m["merchant_id"], t["id"]))

    with cf.ThreadPoolExecutor(max_workers=2) as ex:
        futures = [ex.submit(_tick, [tid]) for _mid, tid in merchants_triggers]
        results = [f.result() for f in futures]

    total_actions = sum(len(r) for r in results)
    assert total_actions == 2, f"both merchants must send exactly once each even under concurrency, got {total_actions}"
    sent_merchant_ids = {a["merchant_id"] for r in results for a in r}
    assert sent_merchant_ids == {mid for mid, _tid in merchants_triggers}


def test_6_replayed_tick_request_no_duplicate_action() -> None:
    cat, base_m, base_t = _real_pharmacies_payloads()
    _push("category", "pharmacies", 1, cat)
    m = copy.deepcopy(base_m)
    m["merchant_id"] = "m_scope_test_6"
    m["conversation_history"] = []
    t = copy.deepcopy(base_t)
    t["id"] = "trg_scope_test_6"
    t["merchant_id"] = m["merchant_id"]
    t["suppression_key"] = "alert:scope6:2026-04"
    _push("merchant", m["merchant_id"], 1, m)
    _push("trigger", t["id"], 1, t)

    payload = {"now": NOW, "available_triggers": [t["id"]]}
    first = client.post("/v1/tick", json=payload).json()["actions"]
    replayed = client.post("/v1/tick", json=payload).json()["actions"]  # identical request, replayed
    assert len(first) == 1
    assert replayed == []


def test_7_supply_alert_compliance_scenario_two_pharmacies_both_get_the_recall() -> None:
    """Uses the REAL, unmodified trg_018 suppression_key verbatim, twice, for two different real
    pharmacy merchants -- the exact compliance/safety scenario the P1 finding was about."""
    cat, base_m, base_t = _real_pharmacies_payloads()
    real_key = base_t["suppression_key"]
    assert real_key == "alert:atorvastatin:2026-04"  # sanity: this is the real, unmodified key
    _push("category", "pharmacies", 1, cat)

    apollo = copy.deepcopy(base_m)
    apollo["conversation_history"] = []  # real merchant_id: m_009_apollo_pharmacy_jaipur
    _push("merchant", apollo["merchant_id"], 1, apollo)
    _push("trigger", base_t["id"], 1, base_t)

    sunrise = copy.deepcopy(base_m)
    sunrise["merchant_id"] = "m_010_sunrisepharm_pharmacy_lucknow"  # real merchant_id from the dataset
    sunrise["conversation_history"] = []
    t2 = copy.deepcopy(base_t)
    t2["id"] = "trg_supply_atorvastatin_recall_sunrise"
    t2["merchant_id"] = sunrise["merchant_id"]
    _push("merchant", sunrise["merchant_id"], 1, sunrise)
    _push("trigger", t2["id"], 1, t2)

    apollo_actions = _tick([base_t["id"]])
    sunrise_actions = _tick([t2["id"]])

    assert len(apollo_actions) == 1, "first pharmacy must get the real recall alert"
    assert len(sunrise_actions) == 1, "second pharmacy must ALSO get the same real recall alert -- not silently dropped"


def test_8_hostile_optout_merchant_suppression_still_works_unchanged() -> None:
    """is_merchant_suppressed/suppress_merchant (the hostile opt-out path) was already
    merchant-scoped before this fix and must remain exactly as it was."""
    cat, base_m, base_t = _real_pharmacies_payloads()
    _push("category", "pharmacies", 1, cat)
    m = copy.deepcopy(base_m)
    m["merchant_id"] = "m_scope_test_8"
    m["conversation_history"] = []
    t = copy.deepcopy(base_t)
    t["id"] = "trg_scope_test_8"
    t["merchant_id"] = m["merchant_id"]
    t["suppression_key"] = "alert:scope8:2026-04"
    _push("merchant", m["merchant_id"], 1, m)
    _push("trigger", t["id"], 1, t)

    first = _tick([t["id"]])
    assert len(first) == 1
    conv_id = first[0]["conversation_id"]

    hostile = client.post(
        "/v1/reply",
        json={
            "conversation_id": conv_id, "merchant_id": m["merchant_id"], "customer_id": None,
            "from_role": "merchant", "message": "Stop messaging me. This is useless spam.",
            "received_at": NOW, "turn_number": 2,
        },
    ).json()
    assert hostile["action"] == "end"

    t2 = copy.deepcopy(base_t)
    t2["id"] = "trg_scope_test_8b"
    t2["merchant_id"] = m["merchant_id"]
    t2["suppression_key"] = "alert:scope8b:2026-04"  # a genuinely new, different opportunity
    _push("trigger", t2["id"], 1, t2)
    after_optout = _tick([t2["id"]])
    assert after_optout == [], "a merchant who opted out must stay suppressed for any new trigger too"
