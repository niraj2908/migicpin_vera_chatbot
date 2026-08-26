import re

from vera.generation.brief import CompositionBrief

_PERCENT_RE = re.compile(r"(\d+(?:\.\d+)?)\s*%")
_LENGTH_TOLERANCE = 30


def _fact_numbers(facts: list[str]) -> set[str]:
    numbers: set[str] = set()
    for fact in facts:
        numbers.update(_PERCENT_RE.findall(fact))
    return numbers


def validate(message: str, brief: CompositionBrief) -> tuple[bool, list[str]]:
    """Reject output the LLM was not entitled to produce. Never trust the message on its own."""
    reasons: list[str] = []

    if not message.strip():
        reasons.append("empty message")

    if len(message) > brief.max_chars + _LENGTH_TOLERANCE:
        reasons.append(f"message exceeds max_chars ({len(message)} > {brief.max_chars})")

    allowed_numbers = _fact_numbers(brief.facts)
    claimed_numbers = set(_PERCENT_RE.findall(message))
    unsupported = claimed_numbers - allowed_numbers
    if unsupported:
        reasons.append(f"unsupported percentage claim(s): {sorted(unsupported)}")

    return (len(reasons) == 0, reasons)
