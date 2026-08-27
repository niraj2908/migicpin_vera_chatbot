"""Deterministic, code-driven proxies for the five Magicpin scoring dimensions.

This is NOT the official Magicpin judge and its output must never be reported as a judge score
— it exists so provider/case comparisons and regression checks can run offline, at scale, without
a live LLM judge. Every function is grounded in the actual CompositionBrief/category data (facts,
taboo vocab, CTA type), not a subjective read of the wording. Where a check is genuinely a soft
heuristic (specificity, genericity), that's stated in its docstring rather than implied.

Not part of `src/vera` on purpose: this is evaluation/test tooling, not part of the shipped
decision or generation pipeline.
"""

import re
from dataclasses import dataclass, field

from vera.generation.brief import CompositionBrief
from vera.generation.firewall import REPLY_TOKEN_RE, validate

_GENERIC_FILLER_MARKERS = (
    "would you like to",
    "we have great offers",
    "don't miss out",
    "increase your sales",
    "grow your business",
    "amazing deal",
    "best in the city",
    "act now",
    "limited time only",
)

_STOPWORDS = {
    "a", "an", "the", "is", "are", "to", "for", "your", "you", "and", "of", "in", "on",
    "at", "this", "that", "it", "with", "want", "me", "i", "reply", "yes", "no",
}


@dataclass
class DimensionReport:
    grounding_violations: list[str] = field(default_factory=list)
    specificity_signals: dict[str, bool] = field(default_factory=dict)
    category_fit_violations: list[str] = field(default_factory=list)
    merchant_fit_signals: dict[str, bool] = field(default_factory=dict)
    engagement_signals: dict[str, object] = field(default_factory=dict)

    @property
    def passes_grounding(self) -> bool:
        return not self.grounding_violations

    @property
    def passes_category_fit(self) -> bool:
        return not self.category_fit_violations


def grounding_violations(message: str, brief: CompositionBrief) -> list[str]:
    """Grounding IS what the firewall enforces — this is not a separate check, just the same
    one exposed under the dimension name the rubric uses."""
    ok, reasons = validate(message, brief)
    return [] if ok else reasons


def specificity_signals(message: str, brief: CompositionBrief) -> dict[str, bool]:
    has_number = bool(re.search(r"\d", message))
    fact_terms_used = sum(
        1 for fact in brief.facts if any(word for word in re.findall(r"[A-Za-z0-9]+", fact) if len(word) > 3 and word.lower() in message.lower())
    )
    return {
        "has_number": has_number,
        "uses_at_least_one_fact": fact_terms_used > 0,
        "uses_all_facts": fact_terms_used >= len(brief.facts),
    }


def category_fit_violations(message: str, brief: CompositionBrief) -> list[str]:
    """Taboo-vocabulary absence is hard-enforced (same source as the firewall). A soft signal —
    whether the message uses any of the category's allowed register — is reported separately in
    merchant_fit_signals/engagement rather than here, since its absence is not a violation."""
    violations = []
    message_lower = message.lower()
    for taboo in brief.vocab_taboo:
        if taboo.lower() in message_lower:
            violations.append(f"taboo phrase present: {taboo!r}")
    return violations


def merchant_fit_signals(message: str, brief: CompositionBrief) -> dict[str, bool]:
    subject = brief.owner_first_name or brief.merchant_name
    return {
        "greets_by_correct_subject": subject.lower() in message.lower(),
        "does_not_invent_owner_name": True if brief.owner_first_name else brief.merchant_name.lower() in message.lower() or subject == brief.merchant_name,
        "uses_category_register_word": any(w.lower() in message.lower() for w in brief.vocab_allowed),
    }


def engagement_signals(message: str, brief: CompositionBrief) -> dict[str, object]:
    reply_tokens = {t.upper() for t in REPLY_TOKEN_RE.findall(message)}
    generic_filler = [m for m in _GENERIC_FILLER_MARKERS if m in message.lower()]
    return {
        "cta_token_count": len(reply_tokens),
        "single_clear_cta": len(reply_tokens) <= 1 if brief.cta != "multi_choice_slot" else len(reply_tokens) <= 3,
        "cta_present_when_expected": (brief.cta == "none") or len(reply_tokens) > 0 or brief.cta == "open_ended",
        "generic_filler_phrases": generic_filler,
        "no_generic_filler": len(generic_filler) == 0,
    }


def evaluate(message: str, brief: CompositionBrief) -> DimensionReport:
    return DimensionReport(
        grounding_violations=grounding_violations(message, brief),
        specificity_signals=specificity_signals(message, brief),
        category_fit_violations=category_fit_violations(message, brief),
        merchant_fit_signals=merchant_fit_signals(message, brief),
        engagement_signals=engagement_signals(message, brief),
    )


def genericity_similarity(message_a: str, message_b: str) -> float:
    """Rough, explicitly-approximate interchangeability signal: Jaccard similarity of
    significant lowercase words. High similarity between messages composed for materially
    different merchant/offer/festival contexts is a genericity red flag worth a human look —
    not proof of a problem on its own."""

    def words(text: str) -> set[str]:
        return {w for w in re.findall(r"[a-z0-9]+", text.lower()) if w not in _STOPWORDS and len(w) > 2}

    set_a, set_b = words(message_a), words(message_b)
    if not set_a and not set_b:
        return 1.0
    if not set_a or not set_b:
        return 0.0
    return len(set_a & set_b) / len(set_a | set_b)
