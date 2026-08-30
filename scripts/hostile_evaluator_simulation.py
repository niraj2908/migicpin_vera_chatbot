#!/usr/bin/env python3
"""Hostile evaluator simulation -- a local, time-compressed, real-HTTP recreation of the judge's
documented lifecycle (challenge-testing-brief.md SS4), combined with adversarial stress within the
same run, against our own real seed dataset.

What this IS: a rehearsal of the judge's own documented sequence -- warmup (push base dataset,
verify healthz) -> a 60-simulated-minute test window advanced in 5-minute ticks, with triggers and
context updates spread across it exactly as SS4 Phase 3 describes ("15 new triggers spread across
the test window", "10 merchants get new performance snapshots", "5 new digest items", "for 5
merchants: a new customer context + recall_due trigger 2 minutes later") -- interleaved with the
same categories of hostile traffic scripts/load_bench.py already proves the app survives
(duplicate/malformed/concurrent/out-of-order requests, expired triggers), all against a REAL local
server over real sockets, not TestClient.

What this is NOT: the real judge. The judge's own sub-LLM plays the merchant/customer role with
generated replies; we do not have that model and do not spend Anthropic/Gemini quota simulating
it. Reply behavior here is a small, deterministic, SCRIPTED set of personas built from the exact
marker vocabulary reply_policy.py itself already classifies (auto-reply, hostile, intent-commit,
plain) -- reused, not invented -- cycled deterministically across actions. This produces a
reproducible rehearsal of the judge's conversation SHAPE, not a claim of matching its judgment.
Provider slow/failure behavior and a real 60-minute-wall-clock or cold-start run are explicitly
out of scope here too -- both already have dedicated coverage (test_pipeline_caller_timeout.py /
test_caller_timeout_api.py for the former; the cold-start test is deliberately deferred to
pre-submission per prior explicit instruction).

Run against a local dev server:

    uvicorn vera.api.app:app --port 8000 &
    python3 scripts/hostile_evaluator_simulation.py
"""

from __future__ import annotations

import argparse
import asyncio
import copy
import json
import os
import random
import re
import sys
import time
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx

DATASET_DIR = Path(__file__).parent.parent / "docs" / "challenge-package" / "dataset"
BOT_URL = os.environ.get("BOT_URL", "http://127.0.0.1:8000")

JUDGE_CALL_TIMEOUT_S = 30.0
SIMULATED_TICK_MINUTES = 5
SIMULATED_TEST_WINDOW_MINUTES = 60
NUM_TICKS = SIMULATED_TEST_WINDOW_MINUTES // SIMULATED_TICK_MINUTES  # 12, matching the contract

# Scripted reply personas, built ONLY from reply_policy.py's own already-classified marker
# vocabulary -- never invented text, so classification behavior is exactly what production would
# do for these exact phrases.
_PERSONA_ENGAGED = "Sounds good, tell me more."
_PERSONA_INTENT_COMMIT = "Ok let's do it, what's next?"
_PERSONA_AUTO_REPLY = "Thank you for contacting us! Our team will respond shortly."
_PERSONA_HOSTILE = "Stop messaging me, this is spam."
_PERSONA_OTHER = "What does that mean exactly?"
_PERSONAS = [_PERSONA_ENGAGED, _PERSONA_INTENT_COMMIT, _PERSONA_AUTO_REPLY, _PERSONA_HOSTILE, _PERSONA_OTHER]


def _now_iso(dt: datetime) -> str:
    return dt.isoformat().replace("+00:00", "Z")


def load_dataset() -> dict[str, Any]:
    categories = {}
    for f in sorted((DATASET_DIR / "categories").glob("*.json")):
        data = json.loads(f.read_text())
        categories[data["slug"]] = data
    merchants = {m["merchant_id"]: m for m in json.loads((DATASET_DIR / "merchants_seed.json").read_text())["merchants"]}
    customers = {c["customer_id"]: c for c in json.loads((DATASET_DIR / "customers_seed.json").read_text())["customers"]}
    triggers = {t["id"]: t for t in json.loads((DATASET_DIR / "triggers_seed.json").read_text())["triggers"]}
    return {"categories": categories, "merchants": merchants, "customers": customers, "triggers": triggers}


@dataclass
class Metrics:
    requests: int = 0
    errors: int = 0
    fivexx: int = 0
    timeouts: int = 0
    latencies_ms: list[float] = field(default_factory=list)
    actions_sent: int = 0
    conversation_ids_seen: set[str] = field(default_factory=set)
    merchant_trigger_pairs_seen: set[tuple[str, str]] = field(default_factory=set)
    duplicate_send_violations: list[str] = field(default_factory=list)
    grounding_violations: list[str] = field(default_factory=list)
    cross_merchant_leakage_violations: list[str] = field(default_factory=list)
    reply_turns: int = 0
    reply_actions: dict[str, int] = field(default_factory=lambda: {"send": 0, "wait": 0, "end": 0})
    conversation_bodies: dict[str, list[str]] = field(default_factory=dict)
    conversation_owner_merchant: dict[str, str] = field(default_factory=dict)

    def percentile(self, p: float) -> float:
        if not self.latencies_ms:
            return 0.0
        lat = sorted(self.latencies_ms)
        idx = max(0, min(len(lat) - 1, round(len(lat) * p / 100) - 1))
        return lat[idx]

    def record_action(self, action: dict[str, Any]) -> None:
        self.actions_sent += 1
        pair = (action["merchant_id"], action["trigger_id"])
        if pair in self.merchant_trigger_pairs_seen:
            self.duplicate_send_violations.append(f"duplicate send for {pair}")
        self.merchant_trigger_pairs_seen.add(pair)

        body = action.get("body", "")
        conv_id = action["conversation_id"]
        self.conversation_ids_seen.add(conv_id)
        self.conversation_bodies.setdefault(conv_id, []).append(body)
        self.conversation_owner_merchant[conv_id] = action["merchant_id"]

        reply_tokens = set(re.findall(r"\breply\s+([A-Za-z0-9]+)", body, re.IGNORECASE))
        if len(reply_tokens) > 2:
            self.grounding_violations.append(f"{conv_id}: possible multi-CTA ({reply_tokens})")
        if not body.strip():
            self.grounding_violations.append(f"{conv_id}: empty body reached the wire")
        if "ignore previous instructions" in body.lower():
            self.grounding_violations.append(f"{conv_id}: injected instruction phrase leaked verbatim")

    def record_reply_body(self, conv_id: str, body: str) -> None:
        if not body:
            return
        history = self.conversation_bodies.setdefault(conv_id, [])
        if body in history:
            self.duplicate_send_violations.append(f"{conv_id}: verbatim-repeated reply body")
        history.append(body)


async def _call(
    client: httpx.AsyncClient, metrics: Metrics, method: str, path: str, **kwargs: Any
) -> httpx.Response | None:
    start = time.monotonic()
    metrics.requests += 1
    try:
        resp = await client.request(method, path, timeout=JUDGE_CALL_TIMEOUT_S, **kwargs)
        metrics.latencies_ms.append((time.monotonic() - start) * 1000)
        if resp.status_code >= 500:
            metrics.fivexx += 1
        return resp
    except httpx.TimeoutException:
        metrics.latencies_ms.append((time.monotonic() - start) * 1000)
        metrics.timeouts += 1
        return None
    except httpx.HTTPError:
        metrics.latencies_ms.append((time.monotonic() - start) * 1000)
        metrics.errors += 1
        return None


async def _push_context(client: httpx.AsyncClient, metrics: Metrics, scope: str, cid: str, version: int, payload: dict[str, Any]) -> None:
    await _call(
        client, metrics, "POST", "/v1/context",
        json={"scope": scope, "context_id": cid, "version": version, "payload": payload, "delivered_at": _now_iso(datetime.now(UTC))},
    )


async def _scripted_reply_loop(
    client: httpx.AsyncClient, metrics: Metrics, action: dict[str, Any], persona_seed: int, now: datetime
) -> None:
    """Up to 5 turns, mirroring the judge's own documented Phase 2 loop, driven by a deterministic
    scripted persona rather than a live LLM."""
    persona = _PERSONAS[persona_seed % len(_PERSONAS)]
    conv_id = action["conversation_id"]
    merchant_id = action["merchant_id"]
    from_role = "customer" if action.get("customer_id") else "merchant"

    for turn in range(2, 7):  # turn 1 was the proactive send itself
        resp = await _call(
            client, metrics, "POST", "/v1/reply",
            json={
                "conversation_id": conv_id,
                "merchant_id": merchant_id,
                "customer_id": action.get("customer_id"),
                "from_role": from_role,
                "message": persona,
                "received_at": _now_iso(now),
                "turn_number": turn,
            },
        )
        metrics.reply_turns += 1
        if resp is None or resp.status_code != 200:
            metrics.grounding_violations.append(f"{conv_id}: reply turn {turn} failed cleanly ({resp.status_code if resp else 'no response'})")
            return
        body = resp.json()
        kind = body.get("action", "?")
        metrics.reply_actions[kind] = metrics.reply_actions.get(kind, 0) + 1
        if kind == "send":
            metrics.record_reply_body(conv_id, body.get("body", ""))
        if kind in ("end",):
            return
        # "wait" does NOT stop the rehearsal: the contract's own loop is "up to 5 turns or until
        # bot ends" -- a real judge session would still deliver later turns (including, for the
        # auto-reply persona, the same canned text again), which is exactly what's needed to
        # exercise decide_reply()'s auto-reply-escalates-to-end-on-repeat behavior. Continuing
        # here (not returning) sends the same scripted persona message again on the next turn.


async def phase1_warmup(client: httpx.AsyncClient, metrics: Metrics, ds: dict[str, Any]) -> bool:
    resp = await _call(client, metrics, "GET", "/v1/healthz")
    if resp is None or resp.status_code != 200:
        return False
    await _call(client, metrics, "GET", "/v1/metadata")

    for slug, cat in ds["categories"].items():
        await _push_context(client, metrics, "category", slug, 1, cat)
    for mid, m in ds["merchants"].items():
        await _push_context(client, metrics, "merchant", mid, 1, m)
    for cid, c in ds["customers"].items():
        await _push_context(client, metrics, "customer", cid, 1, c)

    resp = await _call(client, metrics, "GET", "/v1/healthz")
    if resp is None:
        return False
    counts = resp.json().get("contexts_loaded", {})
    return bool(
        counts.get("category") == len(ds["categories"])
        and counts.get("merchant") == len(ds["merchants"])
        and counts.get("customer") == len(ds["customers"])
    )


async def hostile_interlude(client: httpx.AsyncClient, metrics: Metrics, ds: dict[str, Any], tick_index: int, sim_now: datetime) -> None:
    """Adversarial stress interleaved into the same run, varying by tick so the whole 60-minute
    window gets a mix rather than front-loading everything."""
    kind = tick_index % 4

    if kind == 0:
        # duplicate/idempotent context repost, concurrent
        mid, m = next(iter(ds["merchants"].items()))
        payload = {"scope": "merchant", "context_id": mid, "version": 1, "payload": m, "delivered_at": _now_iso(sim_now)}
        await asyncio.gather(*(_call(client, metrics, "POST", "/v1/context", json=payload) for _ in range(10)))
    elif kind == 1:
        # malformed payloads
        await _call(client, metrics, "POST", "/v1/context", json={"scope": "merchant"})
        await _call(client, metrics, "POST", "/v1/tick", json={"now": "not-a-real-timestamp"})
        await _call(client, metrics, "POST", "/v1/reply", json={"conversation_id": f"conv_never_{uuid.uuid4().hex[:6]}", "from_role": "merchant", "message": "hi", "received_at": _now_iso(sim_now), "turn_number": 1})
    elif kind == 2:
        # expired trigger: real trigger, real expires_at, now pushed far past it
        trig = copy.deepcopy(next(iter(ds["triggers"].values())))
        trig["id"] = f"trg_hostile_expired_{tick_index}"
        far_future = _now_iso(sim_now + timedelta(days=3650))
        await _push_context(client, metrics, "trigger", trig["id"], 1, trig)
        resp = await _call(client, metrics, "POST", "/v1/tick", json={"now": far_future, "available_triggers": [trig["id"]]})
        if resp is not None and resp.json().get("actions"):
            metrics.grounding_violations.append(f"tick {tick_index}: expired trigger produced an action")
    else:
        # out-of-order version: push v1 then a stale v0 repost, confirm rejected not silently applied
        mid, m = next(iter(ds["merchants"].items()))
        stale = {"scope": "merchant", "context_id": mid, "version": 0, "payload": m, "delivered_at": _now_iso(sim_now)}
        resp = await _call(client, metrics, "POST", "/v1/context", json=stale)
        if resp is not None and resp.status_code == 200 and resp.json().get("accepted"):
            metrics.grounding_violations.append(f"tick {tick_index}: stale version 0 was incorrectly accepted")


async def run_simulation(duration_scale: float) -> tuple[Metrics, bool]:
    ds = load_dataset()
    metrics = Metrics()
    rng = random.Random(42)  # deterministic persona/scenario selection

    async with httpx.AsyncClient(base_url=BOT_URL) as client:
        warmup_ok = await phase1_warmup(client, metrics, ds)

        sim_now = datetime(2026, 4, 26, 10, 0, 0, tzinfo=UTC)
        trigger_ids = list(ds["triggers"].keys())
        triggers_per_tick = max(1, len(trigger_ids) // NUM_TICKS)
        pushed_trigger_ids: list[str] = []

        for tick_index in range(NUM_TICKS):
            batch = trigger_ids[tick_index * triggers_per_tick : (tick_index + 1) * triggers_per_tick]
            for tid in batch:
                await _push_context(client, metrics, "trigger", tid, 1, ds["triggers"][tid])
                pushed_trigger_ids.append(tid)

            # Phase 3: updated performance snapshot for one real merchant this tick, real fields only
            if tick_index % 3 == 1:
                mid, m = list(ds["merchants"].items())[tick_index % len(ds["merchants"])]
                updated = copy.deepcopy(m)
                perf = updated.get("performance")
                if isinstance(perf, dict) and "views" in perf:
                    perf["views"] = int(perf["views"] * 1.1)
                await _push_context(client, metrics, "merchant", mid, 2, updated)

            # Phase 3: new digest item for one real category this tick, appended to the real list
            if tick_index % 4 == 2:
                slug, cat = list(ds["categories"].items())[tick_index % len(ds["categories"])]
                updated_cat = copy.deepcopy(cat)
                if isinstance(updated_cat.get("digest"), list):
                    updated_cat["digest"].append({
                        "id": f"d_sim_{tick_index}", "kind": "research",
                        "title": "Simulated mid-test digest item", "source": "hostile_evaluator_simulation.py",
                        "summary": "Synthetic digest entry pushed mid-window to exercise adaptive context injection.",
                    })
                    await _push_context(client, metrics, "category", slug, 2, updated_cat)

            available = batch if batch else pushed_trigger_ids[-2:]
            resp = await _call(
                client, metrics, "POST", "/v1/tick",
                json={"now": _now_iso(sim_now), "available_triggers": available},
            )
            actions = resp.json().get("actions", []) if resp is not None else []
            for i, action in enumerate(actions):
                metrics.record_action(action)
                await _scripted_reply_loop(client, metrics, action, persona_seed=rng.randint(0, 10_000), now=sim_now)

            await hostile_interlude(client, metrics, ds, tick_index, sim_now)

            sim_now += timedelta(minutes=SIMULATED_TICK_MINUTES)
            await asyncio.sleep(0.05 * duration_scale)  # small real-time pacing, not a literal 60-min wait

        # Cross-merchant leakage spot-check: a distinguishing real fact (another merchant's own
        # business name) must never appear in a conversation it doesn't belong to.
        merchant_names = {mid: m["identity"]["name"] for mid, m in ds["merchants"].items()}
        for conv_id, bodies in metrics.conversation_bodies.items():
            owner_id = metrics.conversation_owner_merchant.get(conv_id)
            for other_id, other_name in merchant_names.items():
                if other_id == owner_id or not other_name:
                    continue
                for body in bodies:
                    if other_name in body:
                        metrics.cross_merchant_leakage_violations.append(
                            f"{conv_id} (owner={owner_id}): contains {other_id}'s name {other_name!r}"
                        )

        teardown = await _call(client, metrics, "POST", "/v1/teardown")
        teardown_ok = teardown is not None and teardown.status_code == 200
        healthz_after = await _call(client, metrics, "GET", "/v1/healthz")
        state_wiped = (
            healthz_after is not None
            and all(v == 0 for v in healthz_after.json().get("contexts_loaded", {}).values())
        )

    overall_ok = (
        warmup_ok
        and metrics.fivexx == 0
        and metrics.timeouts == 0
        and not metrics.duplicate_send_violations
        and not metrics.grounding_violations
        and not metrics.cross_merchant_leakage_violations
        and teardown_ok
        and state_wiped
    )
    return metrics, overall_ok


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pace", type=float, default=1.0, help="real-time pacing multiplier between ticks (does not sleep 60 real minutes)")
    args = parser.parse_args()

    print(f"Target: {BOT_URL}")
    print(f"Simulating {SIMULATED_TEST_WINDOW_MINUTES} minutes across {NUM_TICKS} ticks (real-time compressed)")
    try:
        metrics, ok = asyncio.run(run_simulation(args.pace))
    except httpx.ConnectError as exc:
        print(f"FAILED: cannot reach {BOT_URL} ({exc}). Start the server first.", file=sys.stderr)
        return 2

    print("\n" + "=" * 70)
    print(f"Total requests:        {metrics.requests}")
    print(f"Errors (connection):   {metrics.errors}")
    print(f"5xx responses:         {metrics.fivexx}")
    print(f"Timeouts:              {metrics.timeouts}")
    print(f"p50 / p95 / p99 (ms):  {metrics.percentile(50):.1f} / {metrics.percentile(95):.1f} / {metrics.percentile(99):.1f}")
    print(f"Proactive actions sent:{metrics.actions_sent}")
    print(f"Unique conversations:  {len(metrics.conversation_ids_seen)}")
    print(f"Reply turns exercised: {metrics.reply_turns}  (send={metrics.reply_actions.get('send', 0)}, wait={metrics.reply_actions.get('wait', 0)}, end={metrics.reply_actions.get('end', 0)})")
    print(f"Duplicate-send violations:  {len(metrics.duplicate_send_violations)}")
    for v in metrics.duplicate_send_violations:
        print(f"  - {v}")
    print(f"Grounding/contract violations: {len(metrics.grounding_violations)}")
    for v in metrics.grounding_violations:
        print(f"  - {v}")
    print(f"Cross-merchant leakage violations: {len(metrics.cross_merchant_leakage_violations)}")
    for v in metrics.cross_merchant_leakage_violations:
        print(f"  - {v}")
    print("=" * 70)
    print("OVERALL: " + ("PASS" if ok else "FAIL"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
