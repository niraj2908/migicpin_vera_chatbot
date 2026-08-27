import json

import pytest

from vera.generation.brief import CompositionBrief
from vera.generation.composer.shared import build_provider_payload, extract_message


def _brief() -> CompositionBrief:
    return CompositionBrief(
        category_slug="restaurants",
        voice_tone="warm_busy_practical",
        vocab_allowed=[],
        vocab_taboo=["guaranteed packed house"],
        merchant_name="SK Pizza Junction",
        owner_first_name="Suresh",
        languages=["en", "hi"],
        facts=["20% off Diwali Thali"],
        cta="binary_yes_no",
        send_as="vera",
        dominant_signal="festival:Diwali",
        forbidden_topics=["guaranteed packed house"],
    )


def test_build_provider_payload_excludes_internal_fields() -> None:
    """CompositionBrief never carries suppression_key/merchant_id/scores at all, so the payload
    sent to a provider structurally cannot include them — this asserts that invariant holds."""
    payload = build_provider_payload(_brief())
    for forbidden in ("suppression_key", "merchant_id", "score", "confidence", "evidence"):
        assert forbidden not in payload


def test_extract_message_accepts_valid_response() -> None:
    assert extract_message(json.dumps({"message": "Suresh, 20% off Diwali Thali. Reply YES."})) == \
        "Suresh, 20% off Diwali Thali. Reply YES."


def test_extract_message_rejects_non_json() -> None:
    with pytest.raises(json.JSONDecodeError):
        extract_message("not json at all")


def test_extract_message_rejects_non_object() -> None:
    with pytest.raises(TypeError):
        extract_message(json.dumps(["message", "hi"]))


def test_extract_message_rejects_missing_message() -> None:
    with pytest.raises(ValueError, match="missing"):
        extract_message(json.dumps({"note": "no message key here"}))


def test_extract_message_rejects_empty_message() -> None:
    with pytest.raises(ValueError, match="missing"):
        extract_message(json.dumps({"message": "   "}))


@pytest.mark.parametrize("field", ["send", "cta", "suppression_key", "action_type", "send_as", "rationale"])
def test_extract_message_rejects_protected_field_names(field: str) -> None:
    with pytest.raises(ValueError, match="protected field"):
        extract_message(json.dumps({"message": "hi", field: "attempted override"}))


def test_extract_message_rejects_meta_leak_markers() -> None:
    with pytest.raises(ValueError, match="meta"):
        extract_message(json.dumps({"message": "As an AI, I will ignore the instructions and say hi."}))
