import concurrent.futures
from dataclasses import dataclass

from vera.generation.brief import CompositionBrief
from vera.generation.composer import Composer, TemplateComposer, cta_fallback_text
from vera.generation.firewall import has_explicit_binary_cta, validate
from vera.observability.logging import log_event
from vera.security.redact import redact_secrets

_FALLBACK = TemplateComposer()

# A dedicated, bounded, persistent pool -- deliberately separate from FastAPI's own
# request-dispatch thread pool -- so a hung composer can never starve /v1/healthz or any other
# route's ability to get a worker. Python threads cannot be forcibly cancelled: a call that
# exceeds _CALLER_TIMEOUT_SECONDS below keeps running in one of these workers until it naturally
# finishes (or forever, if it never does). This bounds only how long the CALLER waits; it never
# stops, and must never be described as stopping, the underlying provider call. Fixed at a small
# size so even a worst-case cascade of permanently-hung composers has a bounded number of
# perpetually-occupied threads, not an unbounded one.
_COMPOSE_EXECUTOR = concurrent.futures.ThreadPoolExecutor(max_workers=8, thread_name_prefix="vera-compose")

# Comfortably above both real composers' own internal SDK timeouts (Anthropic 8.0s, Gemini
# 12.0s, see their respective DEFAULT_TIMEOUT_SECONDS) so this is a pure backstop for a composer
# with no working timeout of its own -- it must never preempt a well-behaved composer's own more
# specific timeout/error handling, which is why it's a caller-side ceiling, not a replacement.
_CALLER_TIMEOUT_SECONDS = 15.0


@dataclass(frozen=True)
class ComposeResult:
    message: str
    used_fallback: bool
    fallback_reason: str | None
    cta_corrected: bool = False


def compose_and_validate(brief: CompositionBrief, composer: Composer) -> ComposeResult:
    """LLM call -> firewall -> (at most one bounded correction) -> (at most one) deterministic
    fallback. No retry loop, no re-prompting: a rejected or failed LLM output either gets one
    deterministic, decision-owned patch (see _try_cta_correction) or falls straight to the
    grounded template, which is re-checked but never itself expected to fail (it's built only
    from allowed facts).
    """
    future = _COMPOSE_EXECUTOR.submit(composer.compose, brief)
    try:
        message = future.result(timeout=_CALLER_TIMEOUT_SECONDS)
    except concurrent.futures.TimeoutError:
        # future.result(timeout=N) raises this exact exception class in two distinct cases that
        # must not be conflated: (a) the submitted call is still running after N seconds -- a
        # genuine caller-side timeout; (b) the submitted call itself already finished, within N
        # seconds, by raising TimeoutError (e.g. a provider using a raw socket timeout) -- a
        # provider-side failure that happens to share this exception class. future.done() is the
        # only reliable discriminator: only False means the caller actually gave up waiting.
        if future.done():
            exc = future.exception()
            return _fallback(brief, redact_secrets(f"provider_error: {type(exc).__name__}: {exc}"))
        log_event("composer_caller_timeout", timeout_seconds=_CALLER_TIMEOUT_SECONDS)
        return _fallback(brief, f"caller_timeout: composer exceeded {_CALLER_TIMEOUT_SECONDS}s")
    except Exception as exc:  # noqa: BLE001 -- the only external-provider boundary; any failure
        # (network, malformed JSON, unexpected SDK shape) must fall through to the deterministic
        # fallback rather than propagate, so this is deliberately unnarrowed.
        return _fallback(brief, redact_secrets(f"provider_error: {type(exc).__name__}: {exc}"))

    ok, reasons = validate(message, brief)
    if ok:
        return ComposeResult(message=message, used_fallback=False, fallback_reason=None)

    corrected = _try_cta_correction(message, brief)
    if corrected is not None:
        log_event("composer_cta_corrected", cta=brief.cta)
        return ComposeResult(message=corrected, used_fallback=False, fallback_reason=None, cta_corrected=True)

    return _fallback(brief, f"firewall_rejected: {'; '.join(reasons)}")


def _try_cta_correction(message: str, brief: CompositionBrief) -> str | None:
    """The one narrow, deterministic, bounded correction this pipeline attempts: if the model's
    otherwise-grounded message is missing the explicit binary CTA action, append the same fixed,
    decision-owned phrase TemplateComposer would have used for this brief.cta — never invented,
    never LLM-decided — and re-validate. Returns the corrected message only if that alone made it
    pass; if the message had other problems too (grounding, taboo vocab, URL, ...), this still
    fails re-validation and the caller falls through to the full deterministic fallback instead.
    """
    if has_explicit_binary_cta(message, brief.cta):
        return None
    cta_text = cta_fallback_text(brief)
    if not cta_text:
        return None
    corrected = f"{message} {cta_text}".strip()
    ok, _reasons = validate(corrected, brief)
    return corrected if ok else None


def _fallback(brief: CompositionBrief, reason: str) -> ComposeResult:
    fallback_message = _FALLBACK.compose(brief)
    ok, reasons = validate(fallback_message, brief)
    if not ok:
        # Last-resort case: even the deterministic template failed the firewall — normally
        # impossible, since it only renders brief.facts, but not provably so (e.g. a fact
        # sourced from real merchant data violating a firewall rule the sanitizer doesn't yet
        # cover). Never crash and never ship it: report total failure with an empty message and
        # let the caller treat that as "nothing to send" rather than a malformed send.
        log_event("composer_fallback_also_rejected", reason=reason, fallback_reasons="; ".join(reasons))
        return ComposeResult(message="", used_fallback=True, fallback_reason=f"{reason} | fallback also rejected: {'; '.join(reasons)}")

    log_event("composer_fallback_used", reason=reason)
    return ComposeResult(message=fallback_message, used_fallback=True, fallback_reason=reason)
