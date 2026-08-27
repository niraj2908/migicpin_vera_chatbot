from datetime import UTC, datetime

from vera.state.store import ContextStore

NOW = datetime(2026, 4, 26, 10, 0, 0, tzinfo=UTC)


def test_first_push_is_accepted() -> None:
    store = ContextStore()
    result = store.push("merchant", "m_001", 1, {"a": 1}, NOW)
    assert result.accepted is True
    assert result.ack_id is not None
    assert store.get("merchant", "m_001") == {"a": 1}


def test_repost_of_same_version_is_an_idempotent_no_op() -> None:
    """Contract: 'Idempotent by (context_id, version). Re-posting the same version is a
    no-op.' The documented 409 is reserved for a strictly HIGHER existing version, not an
    equal one -- this must be accepted, and must not re-store a differing payload."""
    store = ContextStore()
    first = store.push("merchant", "m_001", 1, {"a": 1}, NOW)
    result = store.push("merchant", "m_001", 1, {"a": 2}, NOW)
    assert result.accepted is True
    assert result.ack_id == first.ack_id  # ack_id is a pure function of (context_id, version)
    # no-op: the differing payload from the repost must NOT overwrite what's stored
    assert store.get("merchant", "m_001") == {"a": 1}


def test_repost_of_a_strictly_lower_version_is_rejected_as_stale() -> None:
    store = ContextStore()
    store.push("merchant", "m_001", 2, {"a": 1}, NOW)
    result = store.push("merchant", "m_001", 1, {"a": 2}, NOW)
    assert result.accepted is False
    assert result.reason == "stale_version"
    assert result.current_version == 2
    assert store.get("merchant", "m_001") == {"a": 1}


def test_higher_version_replaces_atomically() -> None:
    store = ContextStore()
    store.push("merchant", "m_001", 1, {"a": 1}, NOW)
    result = store.push("merchant", "m_001", 2, {"a": 2}, NOW)
    assert result.accepted is True
    assert store.get("merchant", "m_001") == {"a": 2}
    assert store.version_of("merchant", "m_001") == 2


def test_lower_version_after_higher_is_rejected_and_does_not_corrupt_state() -> None:
    store = ContextStore()
    store.push("merchant", "m_001", 5, {"a": 5}, NOW)
    result = store.push("merchant", "m_001", 3, {"a": 3}, NOW)
    assert result.accepted is False
    assert result.current_version == 5
    assert store.get("merchant", "m_001") == {"a": 5}


def test_scopes_and_ids_are_isolated() -> None:
    store = ContextStore()
    store.push("merchant", "id_1", 1, {"kind": "merchant"}, NOW)
    store.push("trigger", "id_1", 1, {"kind": "trigger"}, NOW)
    store.push("merchant", "id_2", 1, {"kind": "other_merchant"}, NOW)

    assert store.get("merchant", "id_1") == {"kind": "merchant"}
    assert store.get("trigger", "id_1") == {"kind": "trigger"}
    assert store.get("merchant", "id_2") == {"kind": "other_merchant"}


def test_counts_reflect_distinct_context_ids_per_scope() -> None:
    store = ContextStore()
    store.push("category", "restaurants", 1, {}, NOW)
    store.push("category", "salons", 1, {}, NOW)
    store.push("merchant", "m_001", 1, {}, NOW)
    # a version bump on the same context_id must not double-count
    store.push("merchant", "m_001", 2, {}, NOW)

    counts = store.counts()
    assert counts["category"] == 2
    assert counts["merchant"] == 1
