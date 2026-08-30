import re

from vera.generation.brief import CompositionBrief

_PERCENT_RE = re.compile(r"(\d+(?:\.\d+)?)\s*%")
_CURRENCY_RE = re.compile(r"₹\s*(\d[\d,]*(?:\.\d+)?)")
# A scheme/www-prefixed token (matched greedily to the next whitespace, so substitution removes
# the whole URL, not just the "https://" prefix), OR a bare domain-shaped token with a
# recognizable TLD and optional path — e.g. "evil.example/promo" has no scheme and no "www." but
# is still a URL a merchant could use to route around the no-URL rule; caught empirically via an
# adversarial-context test, not assumed. Both alternatives capture the FULL token deliberately:
# this regex is reused both to detect a URL's presence (firewall.validate) and to strip one
# entirely out of a fact (composer._sanitize_fact) — a partial match would strip the scheme and
# silently leave the domain/path behind.
URL_RE = re.compile(
    r"(?:https?://|www\.)\S+"
    r"|\b[a-zA-Z0-9](?:[a-zA-Z0-9-]*[a-zA-Z0-9])?\."
    r"(?:com|in|co|io|ly|link|app|net|org|shop|store|xyz|info|biz)(?:/\S*)?\b",
    re.IGNORECASE,
)
REPLY_TOKEN_RE = re.compile(r"\breply\s+([A-Za-z0-9]+)", re.IGNORECASE)
# Demonstrated evasion (found via adversarial testing, not assumed): REPLY_TOKEN_RE alone only
# counts tokens immediately following the literal word "reply", so "Reply YES or NO, or reply 1
# or 2 to choose a slot." -- four real options -- was only ever counted as two ("YES", "1"),
# staying under the >2 rejection threshold. This captures an "or"-chain of further alternatives
# immediately following a "reply X" clause (no other words in between), so all four are counted.
# Deliberately narrow: it only extends an existing "reply X" match, so it does not fire on
# ordinary text containing "or" elsewhere (checked directly against the official multi_choice_slot
# reference message, "Reply 1 for Wed, 2 for Thu, or tell us a time that works." -- the trailing
# "or tell us..." is not part of an in-place "reply X or Y" chain and is correctly not counted).
_REPLY_OR_CHAIN_RE = re.compile(r"\bor\s+([A-Za-z0-9]+)", re.IGNORECASE)
_REPLY_CLAUSE_RE = re.compile(r"\breply\s+([A-Za-z0-9]+)((?:\s+or\s+[A-Za-z0-9]+)*)", re.IGNORECASE)


def _reply_tokens(message: str) -> set[str]:
    tokens: set[str] = set()
    for match in _REPLY_CLAUSE_RE.finditer(message):
        tokens.add(match.group(1).upper())
        tokens.update(t.upper() for t in _REPLY_OR_CHAIN_RE.findall(match.group(2)))
    return tokens
_PLACEHOLDER_RE = re.compile(r"\{\{|\}\}|\[name\]|\[merchant", re.IGNORECASE)

# CTA-presence contract: a binary_yes_no/binary_confirm_cancel message must contain BOTH an
# explicit action-request verb AND one of the actual binary option words — not just a general
# question. Structural (two independent conditions), not one hardcoded phrase: "Reply YES",
# "haan ya nahi bataiye", "Please confirm or cancel" all satisfy it; "would you like...?" does
# not, regardless of language. Hindi equivalents are included deliberately, not as an
# afterthought — code-mixed replies are a documented, expected output style for this product,
# not an edge case.
_CTA_OPTION_WORDS: dict[str, tuple[str, ...]] = {
    "binary_yes_no": ("yes", "no", "haan", "nahi", "nahin"),
    "binary_confirm_cancel": ("confirm", "cancel"),
}
_CTA_ACTION_PHRASES = (
    "reply", "say", "respond", "let us know", "let me know",
    "batayein", "bataiye", "bolo", "bataen", "batao",
    # Natural polite/imperative conjugations of the same "batana" (tell) root already accepted
    # above — not new vocabulary, the same verb's standard forms. Evidence for needing them:
    # the composer's own system prompt (shared.py) explicitly tells the model the CTA may be
    # phrased "in whatever language you wrote the message" and to code-mix Hindi-English when
    # the customer/merchant prefers it — the firewall must recognize the natural range of how a
    # model honoring that instruction would actually phrase a Hindi request, not just five
    # specific forms. "bata do"/"bata dein"/"bata dijiye" are the standard casual/polite/formal
    # request forms of "batana", parallel in register to "reply"/"let us know"/"let me know".
    "bata do", "bata dein", "bata dijiye",
)

_LENGTH_TOLERANCE = 30


def _fact_numbers(facts: list[str], pattern: re.Pattern[str]) -> set[str]:
    numbers: set[str] = set()
    for fact in facts:
        numbers.update(pattern.findall(fact))
    return numbers


def has_explicit_binary_cta(message: str, cta: str) -> bool:
    """True if `cta` isn't a type this checks, or the message contains an explicit action-
    request phrase (all types) plus, for the two binary types specifically, one of that CTA's
    actual option words. multi_choice_slot has no fixed option vocabulary (slot labels are
    free text, e.g. "Wed 5 Nov, 6pm") — Case Study 2's own reference message treats a literal
    numbered "Reply 1/2" as equally acceptable to "or tell us a time that works", so only the
    action-request phrase is required for it, not a specific option word."""
    lowered = message.lower()
    has_action = any(phrase in lowered for phrase in _CTA_ACTION_PHRASES)

    if cta == "multi_choice_slot":
        return has_action

    option_words = _CTA_OPTION_WORDS.get(cta)
    if option_words is None:
        return True

    has_option = any(re.search(rf"\b{re.escape(w)}\b", lowered) for w in option_words)
    return has_option and has_action


def validate(message: str, brief: CompositionBrief) -> tuple[bool, list[str]]:
    """Reject output the LLM was not entitled to produce. Never trust the message on its own.

    Distinguishes a SUPPORTED FACT (a number/price that appears, in any phrasing, among the
    brief's approved facts) from GENERATED LANGUAGE around it — only the former is checked
    against the source data; wording variation itself is never a rejection reason.
    """
    reasons: list[str] = []

    if not message.strip():
        reasons.append("empty message")
        return (False, reasons)

    if len(message) > brief.max_chars + _LENGTH_TOLERANCE:
        reasons.append(f"message exceeds max_chars ({len(message)} > {brief.max_chars})")

    if URL_RE.search(message):
        reasons.append("message contains a URL (hard fail per challenge contract)")

    if _PLACEHOLDER_RE.search(message):
        reasons.append("message contains an unresolved template placeholder")

    allowed_percentages = _fact_numbers(brief.facts, _PERCENT_RE)
    claimed_percentages = set(_PERCENT_RE.findall(message))
    unsupported_percentages = claimed_percentages - allowed_percentages
    if unsupported_percentages:
        reasons.append(f"unsupported percentage claim(s): {sorted(unsupported_percentages)}")

    allowed_prices = _fact_numbers(brief.facts, _CURRENCY_RE)
    claimed_prices = set(_CURRENCY_RE.findall(message))
    unsupported_prices = claimed_prices - allowed_prices
    if unsupported_prices:
        reasons.append(f"unsupported price claim(s): {sorted(unsupported_prices)}")

    message_lower = message.lower()
    for taboo in brief.forbidden_topics:
        if taboo.lower() in message_lower:
            reasons.append(f"uses taboo phrase for this category: {taboo!r}")

    reply_tokens = _reply_tokens(message)
    if brief.cta == "none" and reply_tokens:
        reasons.append("message asks for a reply but cta is 'none'")
    elif len(reply_tokens) > 2:
        reasons.append(f"multiple competing CTAs in one message: {sorted(reply_tokens)}")

    if not has_explicit_binary_cta(message, brief.cta):
        reasons.append(f"message has no explicit {brief.cta} action (a general question is not enough)")

    return (len(reasons) == 0, reasons)
