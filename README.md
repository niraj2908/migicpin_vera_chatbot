# Magicpin Vera Chatbot / Vera Engine

## What this project is

A deterministic-first merchant-growth message engine built for the Magicpin Vera AI Challenge.

Core principle: **software decides whether, why, when, for whom, and how a message should be
sent; the LLM only writes the sentence.** The LLM is a language-realization component, not a
decision-maker.

## Current implementation

The current implementation is a **restaurant + festival vertical slice** — one merchant
category, one trigger type, taken through the complete pipeline end to end, as the foundation
to widen from. It is exposed as a single stateless HTTP endpoint, `POST /v1/compose`, which
accepts a merchant, a trigger, and an optional customer, and returns a composed message.

This does **not** yet implement the official challenge's technical contract (see
[Development status](#development-status) below) — that contract is a stateful, 5-endpoint HTTP
bot, and rebuilding this slice against it is the next planned step.

## Architecture

```
context
  → normalization
  → deterministic decision   (src/vera/decision/)
  → CompositionBrief          (src/vera/generation/brief.py)
  → LLM generation            (src/vera/generation/composer.py)
  → output firewall           (src/vera/generation/firewall.py)
  → final response
```

Explicitly:

- The LLM does **not** decide whether to send.
- The LLM does **not** decide the CTA.
- The LLM does **not** decide the suppression key.
- Deterministic code (`decision/opportunity.py`, `decision/compiler.py`) owns all business
  decisions — given the same normalized input, it returns the same decision every time.
- Generated content must be grounded in the facts the decision layer explicitly allows
  (`Decision.facts_allowed`). The output firewall rejects numeric claims (e.g. a percentage)
  that don't trace back to an allowed fact, and the pipeline falls back to a deterministic,
  merchant-specific template if the LLM's output is rejected.

## Challenge alignment

The submission is evaluated across five dimensions:

- **Decision Quality** — is the action/message appropriate for the actual trigger.
- **Specificity** — does the message use real merchant facts rather than generic language.
- **Category Fit** — does the message read like it belongs to this merchant's category.
- **Merchant Fit** — does the message reflect this specific merchant's data and offers.
- **Engagement Compulsion** — would the message make a recipient want to act, without being
  spammy or deceptive.

The architecture above is designed around these five dimensions: determinism and grounding
target Decision Quality and Specificity; the brief/firewall separation targets Merchant and
Category Fit by construction; compulsion levers are the composer's job within firewall-checked
bounds.

## Reliability and security

Implemented today:

- Deterministic decisions — the decision compiler has no LLM call in it and is covered by a
  determinism test (`tests/unit/test_decision_compiler.py`).
- Output firewall — rejects empty messages, overlong messages, and percentage claims not
  present in the allowed facts (`src/vera/generation/firewall.py`).
- Deterministic fallback — if the LLM composer's output fails firewall validation, the pipeline
  falls back to `TemplateComposer`, which builds the message directly from the same grounded
  facts (never a generic marketing template).
- No secrets in the repository — `.env.example` holds only an empty placeholder; real keys are
  read from the environment at runtime and never logged.

Not yet implemented (tracked in `docs/engineering-spec.md`, not claimed as done): adversarial /
prompt-injection testing, rate limiting, authentication, chaos and load testing, and a security
red-team pass.

## Testing

13 tests currently pass (`make test`):

- `tests/unit/test_decision_compiler.py` — 5 tests: determinism, send on a relevant close
  festival, suppression under high campaign fatigue, no-send on a weak/irrelevant trigger,
  suppression-key stability.
- `tests/unit/test_opportunity.py` — 2 tests: fallback opportunity always present, festival
  opportunity scores higher when a relevant offer exists.
- `tests/unit/test_firewall.py` — 4 tests: accepts grounded output, rejects unsupported
  percentage claims, rejects empty output, rejects overlong output.
- `tests/contract/test_api_contract.py` — 2 tests: `send: true` for a relevant festival,
  `send: false` for an irrelevant one.

`ruff check` and `mypy --strict` both pass with zero issues on `src/`.

No golden dataset, counterfactual evaluation, or LLM-composer quality evaluation exists yet —
see Development status.

## Repository structure

```
src/vera/
├── api/          HTTP layer (currently one stateless endpoint: POST /v1/compose)
├── domain/       merchant/customer/trigger models, truth classification
├── decision/     opportunity generation + deterministic decision compiler
├── generation/   composition brief, LLM composer, output firewall
└── pipeline.py   wires decision -> brief -> composer -> firewall -> response

tests/
├── unit/         decision compiler, opportunity scoring, firewall
└── contract/     API request/response contract

docs/
├── engineering-spec.md          our own architecture & winning-strategy spec
└── challenge-package/           the official Magicpin challenge materials —
                                  brief, testing brief, examples, dataset seeds,
                                  and judge_simulator.py (source of truth for
                                  the real technical contract)

evaluation/golden/  reserved for the golden/counterfactual dataset (not yet built)
```

`src/vera/intelligence/`, `state/`, `security/`, `observability/` and the adversarial/load/chaos
test suites described in `docs/engineering-spec.md` are added when the corresponding build
phase actually starts — this repo does not carry empty scaffolding ahead of real content.

## Development status

**Implemented**
- Restaurant + festival vertical slice through the full pipeline (decision → brief → LLM
  composer → firewall → response), behind a single stateless `POST /v1/compose` endpoint.
- Deterministic decision compiler with opportunity scoring and campaign-fatigue suppression.
- Grounded LLM composer (`AnthropicComposer`) with a deterministic, always-grounded fallback
  (`TemplateComposer`) used automatically when no `ANTHROPIC_API_KEY` is set or when the
  firewall rejects the LLM's output.
- 13 passing unit/contract tests; clean `ruff` and `mypy --strict`.

**Currently being evaluated**
- The official challenge package (`docs/challenge-package/`) defines the actual binding
  technical contract: a stateful bot exposing `GET /v1/healthz`, `GET /v1/metadata`,
  `POST /v1/context`, `POST /v1/tick`, and `POST /v1/reply`, with versioned context push and
  multi-turn conversation handling (`send` / `wait` / `end`). Our current `/v1/compose`
  endpoint does not implement this contract. Rebuilding the API layer and domain models against
  the real schemas (5 categories, real `MerchantContext`/`CategoryContext`/`TriggerContext`
  shapes) is the immediate next step, not yet started.

**Planned**
- Contract rebuild against `docs/challenge-package/challenge-testing-brief.md`.
- Real `CategoryContext`/`MerchantContext`/`CustomerContext`/`TriggerContext` models covering
  the actual 5 categories (dentists, salons, restaurants, gyms, pharmacies).
- Conversation state machine (auto-reply detection, intent-transition handling, graceful exit).
- Golden and counterfactual evaluation dataset under `evaluation/golden/`.
- Running `docs/challenge-package/judge_simulator.py` against the bot for a real score signal.

No WhatsApp integration, production deployment, multi-category support, or performance/security
benchmark results exist yet. Claims to that effect belong only in the sections above once true.

## Setup

```bash
python3 -m venv .venv && source .venv/bin/activate
make install
cp .env.example .env   # optionally set ANTHROPIC_API_KEY to use the real LLM composer
```

Without `ANTHROPIC_API_KEY` set, the pipeline uses `TemplateComposer`, so the API is runnable
and testable with no external dependency.

## Run

```bash
make run
# POST http://localhost:8000/v1/compose
```

## Test

```bash
make test   # pytest
make lint   # ruff + mypy
```
