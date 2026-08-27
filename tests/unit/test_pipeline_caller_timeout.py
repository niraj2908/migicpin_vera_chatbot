"""Regression tests for the caller-side timeout added to compose_and_validate(): a dedicated,
bounded executor plus a deadline on future.result(), backstopping any composer whose own
internal timeout discipline is missing or broken -- see pipeline.py's module-level comments for
the full design rationale (why a Future-based timeout cannot cancel the underlying call, and why
that's an acceptable, clearly-labeled tradeoff for the FastAPI execution model this runs under).

_CALLER_TIMEOUT_SECONDS is monkeypatched down to keep these tests fast and deterministic; no
real network, no Gemini quota spent.
"""

import threading
import time

import pytest

import vera.pipeline as pipeline_module
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


class _CountingSleepComposer:
    """Sleeps `seconds`, then returns a valid message. Tracks call count to prove no retry."""

    def __init__(self, seconds: float, message: str = "Suresh, 20% off Diwali Thali. Reply YES.") -> None:
        self.seconds = seconds
        self.message = message
        self.call_count = 0

    def compose(self, brief: CompositionBrief) -> str:
        self.call_count += 1
        time.sleep(self.seconds)
        return self.message


class _ImmediateTimeoutErrorComposer:
    """Raises builtin TimeoutError immediately -- simulates a provider using a raw socket
    timeout, which IS the same exception class future.result() raises for a caller-side
    give-up. This is exactly the ambiguity the done()-check in pipeline.py disambiguates."""

    def compose(self, brief: CompositionBrief) -> str:
        raise TimeoutError("simulated raw socket timeout from the provider itself")


@pytest.fixture(autouse=True)
def _short_caller_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(pipeline_module, "_CALLER_TIMEOUT_SECONDS", 0.3)


def test_1_composer_completes_before_deadline_returns_normal_output() -> None:
    fast = _CountingSleepComposer(0.01)
    result = compose_and_validate(_brief(), fast)
    assert result.used_fallback is False
    assert "Diwali Thali" in result.message
    assert fast.call_count == 1


def test_2_composer_exceeds_caller_deadline_falls_back() -> None:
    """The genuine caller-side case: the task is still running when the deadline (patched to
    0.3s) elapses."""
    slow = _CountingSleepComposer(2.0)
    start = time.monotonic()
    result = compose_and_validate(_brief(), slow)
    elapsed = time.monotonic() - start

    assert elapsed < 1.0, "the caller must not wait anywhere near the composer's own 2s sleep"
    assert result.used_fallback is True
    assert result.fallback_reason is not None
    assert result.fallback_reason.startswith("caller_timeout")
    assert "Diwali Thali" in result.message  # grounded fallback, not empty/generic


def test_3_provider_raised_timeout_error_is_distinguishable_from_caller_timeout() -> None:
    """A composer that raises TimeoutError itself, fast, well within the deadline, must be
    classified as a provider_error -- not silently relabeled as the caller giving up, even
    though concurrent.futures.TimeoutError and builtin TimeoutError are the same class."""
    result = compose_and_validate(_brief(), _ImmediateTimeoutErrorComposer())
    assert result.used_fallback is True
    assert result.fallback_reason is not None
    assert result.fallback_reason.startswith("provider_error")
    assert "caller_timeout" not in result.fallback_reason


def test_4_firewall_rejection_still_works_through_the_new_executor_path() -> None:
    fabricated = _CountingSleepComposer(0.01, message="Suresh, get 90% off today! Reply YES.")
    result = compose_and_validate(_brief(), fabricated)
    assert result.used_fallback is True
    assert (result.fallback_reason or "").startswith("firewall_rejected")
    assert "90" not in result.message


def test_5_protected_brief_fields_unchanged_after_a_caller_timeout() -> None:
    """compose_and_validate() must never mutate the brief it was given -- cta/send_as/facts are
    decision-owned and must read back identical after a caller timeout as before it."""
    brief = _brief()
    original = (brief.cta, brief.send_as, tuple(brief.facts), brief.dominant_signal)

    slow = _CountingSleepComposer(2.0)
    compose_and_validate(brief, slow)

    assert (brief.cta, brief.send_as, tuple(brief.facts), brief.dominant_signal) == original


def test_10_no_automatic_retry_is_performed_by_the_timeout_mechanism() -> None:
    """A single compose_and_validate() call must invoke composer.compose() exactly once, whether
    it times out or not -- no internal retry loop."""
    slow = _CountingSleepComposer(2.0)
    compose_and_validate(_brief(), slow)
    # Give the background thread (still running past the caller's deadline -- see pipeline.py's
    # comment: the call is never cancelled) time to actually finish, so call_count is settled.
    time.sleep(2.5)
    assert slow.call_count == 1, "the composer must be invoked exactly once, never retried"


def test_background_task_genuinely_keeps_running_after_the_caller_gives_up() -> None:
    """Documents the explicit, honest tradeoff: the caller-side timeout does NOT cancel the
    underlying call. This is not a bug -- Python threads cannot be forcibly stopped -- but it
    must be true and verified, not merely asserted in a comment."""
    finished = threading.Event()

    class _MarkOnFinishComposer:
        def compose(self, brief: CompositionBrief) -> str:
            time.sleep(1.0)
            finished.set()
            return "done"

    composer = _MarkOnFinishComposer()
    start = time.monotonic()
    result = compose_and_validate(_brief(), composer)
    caller_elapsed = time.monotonic() - start

    assert caller_elapsed < 1.0  # caller returned long before the 1.0s sleep completed
    assert result.used_fallback is True
    assert not finished.is_set()  # the background call hadn't finished yet when the caller returned

    assert finished.wait(timeout=2.0), "the background call must still complete on its own"
