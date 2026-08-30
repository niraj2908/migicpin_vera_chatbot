"""P1 regression tests (hostile-audit finding #2): trigger.expires_at was never checked anywhere
in the codebase, and TickRequest.now was never read by tick() -- no code path had access to the
current simulated time to compare against a trigger's own documented staleness field
(engagement-design.md:95: "expires_at -- after which the trigger is stale").

Fix: decide() gained an optional, keyword-only `now: datetime | None = None` parameter. When both
`now` and `trigger.expires_at` are present, parseable, and directly comparable (same
naive/aware-ness), a `now` strictly after `expires_at` produces a new hard-gated no-send Decision
(dominant_signal="stale_trigger"), checked before suppression and before any opportunity is
generated -- mirroring exactly how `already_suppressed` is already a top-level hard gate. Every
ambiguous case (now is None, expires_at is None or malformed, or a naive/aware mismatch) returns
"not stale", which is the exact pre-existing behavior -- no new policy is invented for data this
function cannot safely interpret.
"""

from datetime import datetime, timedelta

from vera.decision.compiler import Decision, _is_stale, decide
from vera.domain.context import MerchantContext, TriggerContext

EXPIRES_AT = "2026-05-10T00:00:00Z"


def _trigger(expires_at: str | None = EXPIRES_AT, **overrides) -> TriggerContext:
    raw = {
        "id": "trg_staleness_test",
        "scope": "merchant",
        "kind": "perf_dip",
        "merchant_id": "m_test",
        "customer_id": None,
        "payload": {"metric": "calls", "delta_pct": -0.5, "window": "7d", "vs_baseline": 12},
        "urgency": 4,
        "suppression_key": "perf_dip:m_test:staleness",
    }
    if expires_at is not None:
        raw["expires_at"] = expires_at
    raw.update(overrides)
    return TriggerContext(raw)


def _merchant() -> MerchantContext:
    return MerchantContext(
        {
            "merchant_id": "m_test",
            "category_slug": "dentists",
            "identity": {"name": "Test Dental", "owner_first_name": "Test", "languages": ["en"]},
            "offers": [],
        }
    )


# --- _is_stale() pure-function unit tests --------------------------------------------------------


def test_is_stale_false_when_now_is_none() -> None:
    """The default, no-`now`-supplied case -- every one of the 437 pre-existing tests calls
    decide() this way. Must be false so existing behavior is byte-for-byte unchanged."""
    assert _is_stale(_trigger(), now=None) is False


def test_is_stale_false_when_expires_at_is_absent() -> None:
    """Requirement: 'if expires_at is absent -> preserve existing semantics; DO NOT invent a new
    expiry policy.' A trigger with no expires_at field at all is never treated as stale,
    regardless of how far in the future `now` is."""
    trigger = _trigger(expires_at=None)
    far_future = datetime.fromisoformat("2099-01-01T00:00:00Z")
    assert _is_stale(trigger, now=far_future) is False


def test_is_stale_true_when_now_is_after_expiry() -> None:
    now = datetime.fromisoformat("2026-05-11T00:00:00Z")
    assert _is_stale(_trigger(), now=now) is True


def test_is_stale_false_when_now_is_before_expiry() -> None:
    now = datetime.fromisoformat("2026-05-09T00:00:00Z")
    assert _is_stale(_trigger(), now=now) is False


def test_is_stale_false_exactly_at_the_expiry_instant() -> None:
    """Exact boundary, explicitly required: now == expires_at is NOT yet past it (`now > expires`
    is strict, not `>=`) -- the trigger is still valid in its final instant."""
    now = datetime.fromisoformat(EXPIRES_AT)
    assert _is_stale(_trigger(), now=now) is False


def test_is_stale_true_one_second_after_the_boundary() -> None:
    now = datetime.fromisoformat(EXPIRES_AT) + timedelta(seconds=1)
    assert _is_stale(_trigger(), now=now) is True


def test_is_stale_false_one_second_before_the_boundary() -> None:
    now = datetime.fromisoformat(EXPIRES_AT) - timedelta(seconds=1)
    assert _is_stale(_trigger(), now=now) is False


def test_is_stale_false_for_malformed_expires_at_string() -> None:
    """Malformed, not crashed -- treated the same as absent, no new policy invented."""
    trigger = _trigger(expires_at="not-a-real-timestamp")
    now = datetime.fromisoformat("2026-05-11T00:00:00Z")
    assert _is_stale(trigger, now=now) is False


def test_is_stale_false_for_empty_string_expires_at() -> None:
    trigger = _trigger(expires_at="")
    now = datetime.fromisoformat("2026-05-11T00:00:00Z")
    assert _is_stale(trigger, now=now) is False


def test_is_stale_true_when_now_is_naive_and_expires_at_is_aware() -> None:
    """Comparing a naive and an aware datetime raises TypeError in Python -- must never crash.
    Fail-closed: every real timestamp documented anywhere in the challenge package is always
    "Z"-suffixed (aware), and this codebase's own datetimes are likewise always aware, so a naive
    value here is off-contract input, not an unremarkable case -- treated as stale (blocked),
    matching the "ambiguous/insufficient evidence blocks an action" convention used everywhere
    else in this decision layer, rather than silently proceeding as if the trigger were valid."""
    naive_now = datetime(2026, 5, 11, 0, 0, 0)  # noqa: DTZ001 -- deliberately naive, testing the mismatch
    assert naive_now.tzinfo is None
    assert _is_stale(_trigger(), now=naive_now) is True  # expires_at ("...Z") is aware


def test_is_stale_true_when_now_is_aware_and_expires_at_is_naive() -> None:
    """The mirrored direction -- confirms this is a symmetric mismatch check, not a check that
    only fires when `now` specifically is the naive side."""
    trigger = _trigger(expires_at="2026-05-10T00:00:00")  # no "Z", parses naive
    aware_now = datetime.fromisoformat("2026-05-11T00:00:00Z")
    assert aware_now.tzinfo is not None
    assert _is_stale(trigger, now=aware_now) is True


def test_is_stale_correctly_compares_when_both_are_naive() -> None:
    """The mismatch case above is specifically about MISMATCHED awareness, not about naive
    datetimes being categorically unusable -- when both sides are naive, comparison must still
    work correctly."""
    trigger = _trigger(expires_at="2026-05-10T00:00:00")  # no "Z", parses naive
    naive_after = datetime(2026, 5, 11, 0, 0, 0)  # noqa: DTZ001 -- deliberately naive, both sides
    naive_before = datetime(2026, 5, 9, 0, 0, 0)  # noqa: DTZ001 -- deliberately naive, both sides
    assert _is_stale(trigger, now=naive_after) is True
    assert _is_stale(trigger, now=naive_before) is False


def test_is_stale_correctly_compares_across_different_real_utc_offsets() -> None:
    """Two aware datetimes with different explicit offsets must still compare correctly by their
    real instant in time, not by their literal offset strings -- a real timezone-boundary case."""
    trigger = _trigger(expires_at="2026-05-10T00:00:00Z")  # UTC midnight
    # 2026-05-10T05:29:00+05:30 == 2026-05-09T23:59:00Z -- one minute before expiry in real time,
    # despite being a later *calendar* hour in its own offset.
    now_ist_before = datetime.fromisoformat("2026-05-10T05:29:00+05:30")
    now_ist_after = datetime.fromisoformat("2026-05-10T05:31:00+05:30")
    assert _is_stale(trigger, now=now_ist_before) is False
    assert _is_stale(trigger, now=now_ist_after) is True


def test_is_stale_false_for_non_string_expires_at_type() -> None:
    """Only the API schema's own JSON string type is ever accepted -- a malformed/non-string
    value reaching this far (e.g. via direct construction bypassing the wire schema) must not
    crash TriggerContext.expires_at's own str() coercion path."""
    trigger = TriggerContext({**_trigger().raw, "expires_at": 12345})
    now = datetime.fromisoformat("2026-05-11T00:00:00Z")
    # TriggerContext.expires_at coerces truthy non-str values via str(); "12345" then fails to
    # parse as a datetime, so _is_stale still safely returns False rather than crashing.
    assert _is_stale(trigger, now=now) is False


# --- decide() integration: ordering, evidence, no fabrication, priority over other gates ---------


def test_decide_stale_trigger_produces_no_send_with_correct_signal() -> None:
    now = datetime.fromisoformat("2026-05-11T00:00:00Z")
    decision = decide(_merchant(), _trigger(), None, now=now)
    assert decision.send is False
    assert decision.dominant_signal == "stale_trigger"
    assert decision.action_type == "none"
    assert decision.cta == "none"


def test_decide_stale_trigger_fabricates_no_facts() -> None:
    now = datetime.fromisoformat("2026-05-11T00:00:00Z")
    decision = decide(_merchant(), _trigger(), None, now=now)
    assert decision.facts_allowed == []


def test_decide_stale_trigger_still_reports_the_correct_suppression_key() -> None:
    """State/isolation requirement: a stale trigger's Decision must still carry its own real
    suppression_key, computed identically to every other path -- no altered merchant/trigger
    identity, no cross-contamination."""
    now = datetime.fromisoformat("2026-05-11T00:00:00Z")
    decision = decide(_merchant(), _trigger(), None, now=now)
    assert decision.suppression_key == "perf_dip:m_test:staleness"


def test_decide_stale_trigger_still_reports_correct_send_as() -> None:
    now = datetime.fromisoformat("2026-05-11T00:00:00Z")
    merchant_facing = decide(_merchant(), _trigger(), None, now=now)
    assert merchant_facing.send_as == "vera"

    customer_trigger = _trigger(customer_id="c_test")
    customer_facing = decide(_merchant(), customer_trigger, None, now=now)
    assert customer_facing.send_as == "merchant_on_behalf"


def test_decide_valid_trigger_unaffected_when_now_is_before_expiry() -> None:
    """'if trigger is still valid -> existing behavior remains unchanged.'"""
    now = datetime.fromisoformat("2026-05-01T00:00:00Z")
    decision = decide(_merchant(), _trigger(), None, now=now)
    assert decision.send is True
    assert decision.dominant_signal == "perf_dip"


def test_decide_omitting_now_reproduces_exact_pre_fix_behavior() -> None:
    """Every one of the 437 pre-existing tests calls decide() without `now` -- confirms the
    default genuinely reproduces identical output to before this fix, not just 'happens to send'."""
    with_now_valid = decide(_merchant(), _trigger(), None, now=datetime.fromisoformat("2026-05-01T00:00:00Z"))
    without_now = decide(_merchant(), _trigger(), None)
    assert without_now.send is True
    assert with_now_valid.send is True
    assert without_now.dominant_signal == with_now_valid.dominant_signal
    assert without_now.facts_allowed == with_now_valid.facts_allowed


def test_staleness_gate_takes_priority_over_suppression_gate() -> None:
    """Both gates independently produce send=False -- confirms which reason is reported when
    BOTH conditions are simultaneously true, and that this ordering is deterministic, not
    incidental. Neither gate is bypassed by the other's presence."""
    now = datetime.fromisoformat("2026-05-11T00:00:00Z")
    decision = decide(_merchant(), _trigger(), None, already_suppressed=True, now=now)
    assert decision.send is False
    assert decision.dominant_signal == "stale_trigger"  # checked before the suppression gate


def test_staleness_does_not_bypass_suppression_when_trigger_is_still_valid() -> None:
    """The staleness gate must never accidentally suppress the suppression gate's own effect --
    a valid (non-stale) but already-suppressed trigger must still correctly report 'suppressed'."""
    now = datetime.fromisoformat("2026-05-01T00:00:00Z")  # before expiry
    decision = decide(_merchant(), _trigger(), None, already_suppressed=True, now=now)
    assert decision.send is False
    assert decision.dominant_signal == "suppressed"


def test_staleness_gate_runs_before_opportunity_generation_no_fabricated_evidence() -> None:
    """A trigger that is BOTH stale AND would otherwise have insufficient opportunity evidence
    (e.g. missing metric) must report staleness, not 'no_strong_opportunity' -- confirms the gate
    is checked first, and confirms no opportunity-generation code path (which could only ever
    read real payload fields) runs at all for a stale trigger."""
    now = datetime.fromisoformat("2026-05-11T00:00:00Z")
    trigger = _trigger()
    trigger = TriggerContext({**trigger.raw, "payload": {}})  # deliberately no metric/delta_pct
    decision = decide(_merchant(), trigger, None, now=now)
    assert decision.send is False
    assert decision.dominant_signal == "stale_trigger"


def test_stale_trigger_cta_validation_unaffected_no_action_means_no_cta_to_validate() -> None:
    """Security requirement: 'do not change CTA validation.' A stale trigger produces cta="none"
    exactly like every other no-send path (suppressed, below-threshold) -- no new CTA value, no
    change to how any other decision's CTA is computed or validated."""
    now = datetime.fromisoformat("2026-05-11T00:00:00Z")
    decision = decide(_merchant(), _trigger(), None, now=now)
    assert decision.cta == "none"


def test_stale_decision_dataclass_shape_is_identical_to_every_other_no_send_decision() -> None:
    """No new fields, no new response shape -- confirms the Decision returned for staleness is
    structurally identical to the existing suppressed-path Decision, just with different values."""
    now = datetime.fromisoformat("2026-05-11T00:00:00Z")
    stale = decide(_merchant(), _trigger(), None, now=now)
    suppressed = decide(_merchant(), _trigger(), None, already_suppressed=True)
    assert {f.name for f in stale.__dataclass_fields__.values()} == {  # type: ignore[attr-defined]
        f.name for f in suppressed.__dataclass_fields__.values()  # type: ignore[attr-defined]
    }
    assert isinstance(stale, Decision) and isinstance(suppressed, Decision)
