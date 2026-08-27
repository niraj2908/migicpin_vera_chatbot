"""get_default_composer() dispatch — no real network calls in this file. A dummy, obviously-fake
value like "test-key-not-real" is used only to satisfy our own "is a key present" check; it is
never a real credential and never matches any secret-scan pattern."""

import pytest

from vera.generation.composer import (
    AnthropicComposer,
    GeminiComposer,
    TemplateComposer,
    get_default_composer,
)


def _clear_provider_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for var in ("COMPOSER_PROVIDER", "ANTHROPIC_API_KEY", "GEMINI_API_KEY"):
        monkeypatch.delenv(var, raising=False)


def test_no_config_at_all_defaults_to_template(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_provider_env(monkeypatch)
    assert isinstance(get_default_composer(), TemplateComposer)


def test_explicit_template_wins_even_if_keys_present(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_provider_env(monkeypatch)
    monkeypatch.setenv("COMPOSER_PROVIDER", "template")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key-not-real")
    assert isinstance(get_default_composer(), TemplateComposer)


def test_anthropic_without_key_falls_back_to_template_not_crash(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_provider_env(monkeypatch)
    monkeypatch.setenv("COMPOSER_PROVIDER", "anthropic")
    assert isinstance(get_default_composer(), TemplateComposer)


def test_gemini_without_key_falls_back_to_template_not_crash(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_provider_env(monkeypatch)
    monkeypatch.setenv("COMPOSER_PROVIDER", "gemini")
    assert isinstance(get_default_composer(), TemplateComposer)


def test_unknown_provider_value_falls_back_to_template(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_provider_env(monkeypatch)
    monkeypatch.setenv("COMPOSER_PROVIDER", "not_a_real_provider")
    assert isinstance(get_default_composer(), TemplateComposer)


def test_anthropic_with_key_constructs_anthropic_composer(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_provider_env(monkeypatch)
    monkeypatch.setenv("COMPOSER_PROVIDER", "anthropic")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key-not-real")
    assert isinstance(get_default_composer(), AnthropicComposer)


def test_gemini_with_key_constructs_gemini_composer(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_provider_env(monkeypatch)
    monkeypatch.setenv("COMPOSER_PROVIDER", "gemini")
    monkeypatch.setenv("GEMINI_API_KEY", "test-key-not-real")
    assert isinstance(get_default_composer(), GeminiComposer)


def test_auto_detect_prefers_anthropic_when_both_keys_present(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_provider_env(monkeypatch)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key-not-real")
    monkeypatch.setenv("GEMINI_API_KEY", "test-key-not-real")
    assert isinstance(get_default_composer(), AnthropicComposer)


def test_auto_detect_uses_gemini_when_only_gemini_key_present(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_provider_env(monkeypatch)
    monkeypatch.setenv("GEMINI_API_KEY", "test-key-not-real")
    assert isinstance(get_default_composer(), GeminiComposer)
