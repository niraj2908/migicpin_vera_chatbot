import pytest

from vera.security.redact import redact_secrets


def test_redacts_configured_secret_value(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-fake-secret-value-123")
    text = "provider error: auth failed for key test-fake-secret-value-123 on request"
    redacted = redact_secrets(text)
    assert "test-fake-secret-value-123" not in redacted
    assert "[REDACTED]" in redacted


def test_leaves_text_unchanged_when_no_secret_configured(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    text = "a perfectly ordinary error message"
    assert redact_secrets(text) == text


def test_redacts_gemini_key_specifically(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setenv("GEMINI_API_KEY", "another-fake-secret-456")
    text = "oops another-fake-secret-456 leaked"
    assert "another-fake-secret-456" not in redact_secrets(text)
