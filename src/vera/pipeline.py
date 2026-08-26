from vera.api.schemas import ComposeResponse
from vera.decision.compiler import decide
from vera.domain.models import CustomerState, MerchantState, Trigger
from vera.generation.brief import build_brief
from vera.generation.composer import Composer, TemplateComposer, get_default_composer
from vera.generation.firewall import validate

_FALLBACK = TemplateComposer()


def run(
    merchant: MerchantState,
    trigger: Trigger,
    customer: CustomerState | None = None,
    composer: Composer | None = None,
) -> ComposeResponse:
    decision = decide(merchant, trigger, customer)

    if not decision.send:
        return ComposeResponse(
            send=False,
            message="",
            cta="",
            identity=decision.identity,
            suppression_key=decision.suppression_key,
            rationale=decision.reason,
        )

    brief = build_brief(decision, merchant)
    active_composer = composer or get_default_composer()

    message = active_composer.compose(brief)
    ok, reasons = validate(message, brief)
    if not ok:
        message = _FALLBACK.compose(brief)
        ok, reasons = validate(message, brief)
        assert ok, f"deterministic fallback failed firewall validation: {reasons}"

    return ComposeResponse(
        send=True,
        message=message,
        cta=decision.cta,
        identity=decision.identity,
        suppression_key=decision.suppression_key,
        rationale=decision.reason,
    )
