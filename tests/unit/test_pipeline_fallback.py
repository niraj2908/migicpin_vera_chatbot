import pytest

from vera.generation.brief import CompositionBrief
from vera.pipeline import compose_and_validate


def _brief() -> CompositionBrief:
    return CompositionBrief(
        category_slug="restaurants",
        voice_tone="warm_busy_practical",
        vocab_allowed=[],
        vocab_taboo=[],
        merchant_name="SK Pizza Junction",
        owner_first_name="Suresh",
        languages=["en"],
        facts=["20% off Diwali Thali"],
        cta="binary_yes_no",
        send_as="vera",
        dominant_signal="festival:Diwali",
    )


class _RaisingComposer:
    def __init__(self, exc: BaseException) -> None:
        self._exc = exc

    def compose(self, brief: CompositionBrief) -> str:
        raise self._exc


class _StaticComposer:
    def __init__(self, message: str) -> None:
        self._message = message

    def compose(self, brief: CompositionBrief) -> str:
        return self._message


def test_valid_grounded_output_is_used_as_is() -> None:
    result = compose_and_validate(_brief(), _StaticComposer("Suresh, 20% off Diwali Thali. Reply YES."))
    assert result.used_fallback is False
    assert result.fallback_reason is None
    assert "Diwali Thali" in result.message


def test_provider_timeout_falls_back_and_never_raises() -> None:
    result = compose_and_validate(_brief(), _RaisingComposer(TimeoutError("provider took too long")))
    assert result.used_fallback is True
    assert result.fallback_reason is not None
    assert result.fallback_reason.startswith("provider_error")
    assert result.message.strip() != ""


def test_provider_generic_error_falls_back_and_never_raises() -> None:
    """Covers quota errors, network errors, malformed-SDK-response errors uniformly — the
    pipeline treats any provider failure the same way: fall back, never propagate."""
    result = compose_and_validate(_brief(), _RaisingComposer(RuntimeError("429 rate limit exceeded")))
    assert result.used_fallback is True
    assert "provider_error" in (result.fallback_reason or "")


def test_unsupported_numeric_claim_triggers_firewall_fallback() -> None:
    result = compose_and_validate(_brief(), _StaticComposer("Suresh, get 90% off today! Reply YES."))
    assert result.used_fallback is True
    assert (result.fallback_reason or "").startswith("firewall_rejected")
    assert "90" not in result.message


def test_fallback_message_is_always_grounded_never_generic() -> None:
    result = compose_and_validate(_brief(), _RaisingComposer(ValueError("malformed json")))
    assert "Diwali Thali" in result.message  # the real fact, not a generic "want to run a campaign?"


def test_provider_error_message_with_secret_value_is_redacted(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-fake-secret-789")
    result = compose_and_validate(
        _brief(), _RaisingComposer(RuntimeError("auth failed for test-fake-secret-789"))
    )
    assert result.fallback_reason is not None
    assert "test-fake-secret-789" not in result.fallback_reason
