# CLAUDE.md

Rules for working in this repository. Follow these before writing or changing any code.

## Architecture boundary (non-negotiable)

- The decision layer (`vera/decision/compiler.py`, `vera/decision/reply_policy.py`) is
  deterministic. Given the same normalized input, it must return the same `Decision` /
  `ReplyDecision`. Never let an LLM call influence `send`, `action_type`, `cta`, `send_as`,
  `suppression_key`, the `/v1/reply` action (`send`/`wait`/`end`), or opportunity scoring.
- The LLM (`vera/generation/composer/`) only turns an already-decided `CompositionBrief` into a
  sentence. It must never receive raw unfiltered context — only the minimal payload
  `shared.build_provider_payload()` builds from the brief — and its output must never bypass
  `vera/generation/firewall.py` before being returned. `shared.extract_message()` additionally
  rejects any provider response naming a protected field (`send`, `cta`, `suppression_key`, etc.)
  or containing a meta/break-character phrase, before the text is even considered.
- Every fact the LLM is allowed to state must originate from `Decision.facts_allowed`, built
  from real merchant/trigger data — never invented in a prompt.
- Composer providers are pluggable behind the `Composer` protocol
  (`generation/composer/__init__.py`), selected via `COMPOSER_PROVIDER`. Adding a provider means
  a new file in `generation/composer/` that uses `shared.py`'s prompt/payload/parsing — never
  duplicate that logic per provider. A missing/misconfigured provider key must fall back to
  `TemplateComposer` and log why; it must never raise out of `get_default_composer()`.
- `pipeline.compose_and_validate()` must never crash, even if the deterministic fallback itself
  fails firewall validation (this happened once — a fact sourced from real merchant data
  contained a URL, and the fallback rendered it verbatim; see `pipeline._fallback()`'s handling
  and `TemplateComposer`'s fact sanitization). It returns a `ComposeResult`; an empty
  `.message` means total failure, and callers must treat that as "don't send" / end the
  conversation, never ship an empty or malformed body.
- Never log or put into an exception message a real secret value. `security/redact.py` strips
  any configured provider key's literal value out of a string before `observability/logging.py`
  logs it — keep using `redact_secrets()` on anything derived from a provider SDK exception.
- The official challenge contract (`docs/challenge-package/challenge-testing-brief.md`) is the
  source of truth for wire schemas — the five endpoints (`/v1/healthz`, `/v1/metadata`,
  `/v1/context`, `/v1/tick`, `/v1/reply`), field names, status codes, and idempotency semantics.
  Do not change any of it without re-reading that file; do not invent fields not documented
  there or in `examples/api-call-examples.md`.

## Required for any new behavior

- Add or update tests in `tests/unit/` (and `tests/contract/` if an endpoint is touched, or
  `tests/evaluation/` if it's a new decision case). Cover: the happy path, suppression, missing
  optional context, and category mismatch where relevant.
- Run `make test` and `make lint` before considering a change done.
- Do not change any `/v1/*` request/response schema without explicit instruction and without
  re-checking `docs/challenge-package/` — it is the challenge contract, not ours to redesign.

## Style

- Small, cohesive modules with typed interfaces (this is a Python project — use type hints
  everywhere).
- Context payloads (`domain/context.py`) are a deliberate exception to "prefer pydantic models
  over dicts": they're stored and read as `dict[str, Any]` behind typed accessor properties,
  because real judge payloads carry fields beyond any documented example (e.g. category-varying
  `customer_aggregate` shapes) and a strict schema would silently drop or reject real data. Don't
  "fix" this into a rigid pydantic `MerchantContext` — add a new accessor property instead.
- No comments explaining *what* code does; only the non-obvious *why* when it isn't clear from
  naming.
- No speculative abstractions, no unused dependencies, no dead code paths.
- No secrets committed. `.env` is gitignored; use `.env.example` for documenting required vars.

## Explicitly out of scope unless asked

- WhatsApp adapter, dashboard, voice interface — future channels, not part of the challenge
  kernel.
- Kubernetes, Kafka, vector databases, multi-agent orchestration — not needed at this scale.
