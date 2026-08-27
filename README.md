# Magicpin Vera Chatbot / Vera Engine

## What this project is

A deterministic-first merchant-growth message engine built for the Magicpin Vera AI Challenge.

Core principle: **software decides whether, why, when, for whom, and how a message should be
sent; the LLM only writes the sentence.** The LLM is a language-realization component, not a
decision-maker.

## Current implementation

The current implementation is a **restaurant + `festival_upcoming` vertical slice** — one
merchant category, one trigger kind, taken through the complete official challenge lifecycle, as
the foundation to widen from. It implements the actual official 5-endpoint HTTP contract
(`docs/challenge-package/challenge-testing-brief.md`), not a simplified stand-in:

```
GET  /v1/healthz     GET  /v1/metadata
POST /v1/context     POST /v1/tick      POST /v1/reply
```

`/v1/context` accepts versioned pushes of category/merchant/customer/trigger context (idempotent
per `(scope, context_id, version)`, higher version replaces atomically, stale version rejected
with `409`). `/v1/tick` inspects currently-stored context and proactively decides what to send.
`/v1/reply` runs a small deterministic conversation policy (auto-reply detection, hostile
opt-out, intent-commitment handoff) and returns `send` / `wait` / `end`.

An earlier version of this repo used an invented `POST /v1/compose` interface based on
unverified assumptions about the contract. That endpoint no longer exists; it has been fully
replaced by the endpoints above once the actual challenge package was read.

## Architecture

```
context (pushed via /v1/context, read from an in-memory versioned store)
  → deterministic decision      (src/vera/decision/)
  → CompositionBrief            (src/vera/generation/brief.py)
  → LLM generation              (src/vera/generation/composer/ — provider-neutral)
  → output firewall             (src/vera/generation/firewall.py)
  → final response (/v1/tick action or /v1/reply send/wait/end)
```

Explicitly:

- The LLM does **not** decide whether to send, the CTA, the suppression key, the action type, or
  the `/v1/reply` action (`send`/`wait`/`end`). All of these come out of
  `decision/compiler.py` and `decision/reply_policy.py` — pure functions with no LLM call in
  them, covered by determinism tests. If a provider's JSON response ever includes a key named
  like one of these protected fields, `extract_message` rejects the whole response rather than
  reading it (`generation/composer/shared.py`).
- Generated content must be grounded in the facts the decision layer explicitly allows
  (`Decision.facts_allowed`). The output firewall rejects numeric/percentage/price claims that
  don't trace back to an allowed fact, taboo category vocabulary, URLs (scheme-prefixed and bare
  domains), competing/mismatched CTAs, and meta/break-character phrasing that suggests a
  prompt-injection attempt partially landed — while explicitly *not* rejecting harmless
  natural-language rewording of a supported fact. A rejected or failed LLM call falls through to
  exactly one deterministic, still-grounded template composition; there is no retry/regeneration
  loop. If even that fallback can't pass the firewall (e.g. a fact itself contains something the
  firewall rejects), the pipeline reports total failure rather than crash or ship it, and the API
  layer treats that as "nothing to send" / ends the conversation rather than send a malformed body.
- **Providers are pluggable behind one `Composer` protocol** (`generation/composer/__init__.py`):
  `TemplateComposer` (deterministic, default), `AnthropicComposer`, `GeminiComposer`. All prompt
  construction, the minimal provider payload, and strict JSON parsing/rejection live once in
  `generation/composer/shared.py` — no per-provider duplication. Selected via `COMPOSER_PROVIDER`
  (see Setup); a missing/misconfigured provider falls back to `TemplateComposer` and logs why,
  it never crashes the app.
- Context payloads are stored and read as plain `dict[str, Any]` behind typed accessor
  properties (`domain/context.py`), not parsed into a rigid schema — real payloads carry fields
  beyond any abbreviated example (e.g. a restaurant's `customer_aggregate` differs from a
  dentist's), and a strict schema would silently drop or reject real judge data.

## Challenge alignment

The submission is evaluated across five dimensions (`docs/challenge-package/challenge-brief.md`
§8, cross-checked against the actual scoring prompt in
`docs/challenge-package/judge_simulator.py`):

- **Decision Quality** — is the action/message appropriate for the actual trigger.
- **Specificity** — does the message use real merchant facts rather than generic language.
- **Category Fit** — does the message read like it belongs to this merchant's category.
- **Merchant Fit** — does the message reflect this specific merchant's data and offers.
- **Engagement Compulsion** — would the message make a recipient want to act, without being
  spammy or deceptive.

## Reliability and security

Implemented today:

- Deterministic decision layer and reply policy — no LLM call in either; both are covered by
  determinism/replay tests (`tests/evaluation/test_counterfactual.py::test_replaying_the_identical_request_is_deterministic`).
- Context idempotency: same-or-lower version rejected with `409 stale_version` and the state left
  untouched; higher version replaces atomically. Tested directly against `ContextStore`
  (`tests/unit/test_context_store.py`) and through the live HTTP endpoint.
- Suppression: a trigger's `suppression_key` is marked used on send and checked before any future
  decision for that trigger; re-ticking the same available_triggers does not duplicate a send.
- Output firewall (see Architecture above), with an always-grounded deterministic fallback.
- Bounded input: `/v1/context` payload capped at the contract's documented 500KB (checked against
  raw wire bytes, not the parsed object), request string/collection fields length-capped, `/v1/tick`
  cannot return more than the contract's documented 20 actions.
- Context text is treated as data, never instructions — the system prompt says so explicitly, the
  minimal provider payload (`build_provider_payload`) never carries internal IDs/scores/suppression
  keys in the first place, and a dedicated adversarial test confirms an instruction-like string
  inside an offer title cannot change `send_as`, `cta`, or `suppression_key`, which come only from
  our own code. A second layer at the parsing boundary rejects any provider response that names a
  protected field or contains a meta/break-character phrase (`extract_message`).
- No secrets in the repository — `.env.example` holds only empty placeholders; provider keys are
  read from the environment at runtime and never logged. `observability/logging.py` denies any
  field named like a secret/token/key outright, and separately runs every string field through
  `security/redact.py`, which strips the literal configured key value out of any string before it's
  logged — defense in depth in case a provider SDK ever echoed one back in an error.
- Per-tick LLM time budget: once 8 of the documented 10s `/v1/tick` budget has elapsed, remaining
  actions in that tick use the deterministic composer instead of calling the LLM again. Each
  provider call itself carries an explicit timeout (`ANTHROPIC_TIMEOUT_SECONDS` /
  `GEMINI_TIMEOUT_SECONDS`, default 8s) — no unbounded calls, no retry loop, at most one fallback.

Not yet implemented / not yet tested (see Known gaps below): live-LLM composer behavior against
either provider (no `ANTHROPIC_API_KEY` or `GEMINI_API_KEY` configured in this environment), the
official `judge_simulator.py`, actual public deployment, authentication (deliberately not added —
the contract doesn't call for it and it would block the judge from reaching the endpoints),
fuzz/property-based testing.

## Testing

109 tests currently pass (`make test`):

- `tests/unit/` — decision compiler, opportunity scoring, reply policy, output firewall,
  context store versioning/idempotency, the provider-neutral composer package (`shared.py`'s
  strict JSON parsing/rejection, `get_default_composer()`'s config dispatch and safe fallback
  with no real network calls), `TemplateComposer` grounding, the pipeline's fail-closed fallback
  behavior against mocked providers (timeout, generic provider error, firewall rejection), and
  secret redaction.
- `tests/contract/` — the 5 endpoints against real dataset payloads: healthz/metadata shape,
  context idempotency and version replacement over real HTTP, invalid-scope and oversized-payload
  rejection, a full tick→send flow, tick deduplication on re-tick, category-mismatch no-send,
  `/v1/reply` flows for hostile opt-out, duplicate replies after end, auto-reply wait→end,
  intent-commitment handoff, the prompt-injection-as-data adversarial case, and
  (`test_security.py`) malformed JSON/oversized-field/wrong-type handling, a URL smuggled inside
  offer data never reaching a response, and a canary-secret-value scan across every endpoint's
  response body.
- `tests/evaluation/` — golden cases (5) and counterfactual mutations (8) anchored on the actual
  seed dataset in `docs/challenge-package/dataset/`, not invented: offer removed, offer changed,
  a discount percentage change flowing end-to-end into the composed message (and the old
  percentage being firewall-rejected afterward), category relevance flipped, trigger made stale,
  suppression applied, identical-request replay.

`ruff check` and `mypy --strict` both pass with zero issues on `src/`.

**A real bug was found and fixed while writing this test suite, not left as a known gap**: the
deterministic `TemplateComposer` fallback rendered `brief.facts` verbatim, so a fact sourced from
real merchant data (e.g. an offer title containing a URL) could make the *fallback itself* fail
the firewall it exists to satisfy — the code used a bare `assert`, which would have crashed the
request with a 500 instead of failing safely. Root-caused and fixed in two places: (1)
`TemplateComposer` now sanitizes URL-like substrings out of every fact before rendering, and (2)
`pipeline._fallback()` no longer asserts — if even the sanitized fallback can't pass, it reports
total failure and the API layer treats that as "don't send" rather than shipping or crashing. A
second, related gap was found in the same pass: `firewall.py`'s URL regex only matched
`https://`/`www.`-prefixed text, letting a bare domain like `evil.example/promo` through
undetected — fixed to also catch domain-shaped tokens with a recognized TLD. Both are covered by
regression tests (`test_template_composer.py`, `test_firewall.py::test_rejects_bare_domain_*`).

Measured (in-process, `TemplateComposer` path — no LLM call, no network layer): `/v1/tick`
p50 0.6ms / p95 0.8ms / p99 2.1ms over 200 calls; `/v1/healthz` mean 0.8ms. These bound our own
code's overhead only — they say nothing about a live LLM call's latency or a real deployment's
network/ASGI-server overhead, neither of which has been measured yet.

## Repository structure

```
src/vera/
├── api/             the 5 challenge endpoints + request schemas
├── domain/
│   ├── context.py   typed accessors over raw category/merchant/customer/trigger payloads
│   └── truth.py     FACT/DERIVED/INFERRED/UNTRUSTED/UNKNOWN classification
├── decision/
│   ├── opportunity.py   candidate opportunity generation + scoring
│   ├── compiler.py      picks the dominant opportunity; owns send/cta/suppression/send_as
│   └── reply_policy.py  deterministic auto-reply/hostile/intent-commit classification
├── generation/
│   ├── brief.py          CompositionBrief — the only thing the LLM receives
│   ├── composer/         provider-neutral: Composer protocol, TemplateComposer,
│   │                     get_default_composer() config dispatch (__init__.py); shared.py
│   │                     (system prompt, minimal payload builder, strict JSON parse/reject);
│   │                     anthropic_provider.py; gemini_provider.py
│   └── firewall.py       validates every generated claim against the brief's approved facts
├── state/store.py    ContextStore, ConversationStore, SuppressionStore
├── security/         payload-size bound (/v1/context), secret-value redaction for logs/errors
├── observability/    one structured log line per decision-relevant event, secret-denylisted
│                     and redacted
└── pipeline.py        compose -> firewall -> (at most one) deterministic fallback,
                        never crashes even if the fallback itself fails validation

tests/
├── unit/          decision compiler, opportunity scoring, reply policy, firewall, context
│                  store, composer package (parsing/dispatch/fallback), redaction
├── contract/      the 5 endpoints over real HTTP with real dataset payloads, security tests
└── evaluation/    golden cases + counterfactual mutations anchored on the seed dataset

scripts/
└── real_model_eval.py   diagnostic harness — real, billed calls against whichever of
                          ANTHROPIC_API_KEY / GEMINI_API_KEY is set; not part of pytest/CI

docs/
├── engineering-spec.md          our own architecture & winning-strategy spec
└── challenge-package/           the official Magicpin challenge materials — brief, testing
                                  brief, examples, dataset seeds, judge_simulator.py
```

`src/vera/intelligence/` and the load/chaos/soak test suites described in
`docs/engineering-spec.md` are added only when the corresponding build phase actually starts —
this repo does not carry empty scaffolding ahead of real content.

## Development status

**Implemented**
- The real 5-endpoint contract, backed by an in-memory `Store` (context versioning, conversation
  state, suppression) — not the old single-endpoint stand-in.
- Deterministic decision compiler + reply policy, both LLM-free and directly tested for
  determinism.
- Grounded `CompositionBrief` → provider-neutral composer (`TemplateComposer` deterministic
  default, or `AnthropicComposer`/`GeminiComposer` when configured, both temperature 0) →
  output firewall, wired into both `/v1/tick` and `/v1/reply`.
- Restaurant category + `festival_upcoming` trigger only — deliberately not widened yet.
- 109 passing tests; clean `ruff` and `mypy --strict`; a real (measured, not claimed) latency
  number for the deterministic path.

**Currently being evaluated / known gaps**
- Neither `ANTHROPIC_API_KEY` nor `GEMINI_API_KEY` is configured in this development
  environment, so no real provider composer has ever actually been called — only
  `TemplateComposer` has been exercised, live and under test. LLM-path latency, and whether real
  generations pass the firewall on the first try, are unknown for both providers.
- `docs/challenge-package/judge_simulator.py` has not been run — it requires its own LLM
  provider key (for the judge's scoring calls), which is not present here.
- Not deployed anywhere. No public HTTPS URL exists yet.
- `challenge-brief.md` §7 describes a different submission mode (a plain `compose()` function +
  `submission.jsonl`) that conflicts with the HTTP-harness contract this repo implements; the
  HTTP harness was chosen because it's the one `judge_simulator.py` actually speaks. Still
  unresolved if you have a newer version of the brief.
- The testing brief's failure-mode table treats any URL in a body as a hard fail; the main brief
  says URLs are allowed "when they add clear value." This repo's firewall rejects all URLs,
  siding with the harness that actually scores submissions.

**Planned**
- Exercise `AnthropicComposer` against a real key; verify firewall pass rate on live generations.
- Run `judge_simulator.py` locally.
- Deploy to a public HTTPS URL and re-verify the full lifecycle against that deployment.
- Only after the above: widen beyond one category/trigger kind.

No WhatsApp integration, production deployment, multi-category support, or live-LLM/judge results
exist yet. Claims to that effect belong only in the sections above once true.

## Setup

```bash
python3 -m venv .venv && source .venv/bin/activate
make install
cp .env.example .env   # then optionally set a provider key below in your own .env — never in git
```

Without any provider key set, the pipeline uses `TemplateComposer`, so the API is fully runnable
and testable with no external dependency and no network calls.

### Provider configuration

```bash
COMPOSER_PROVIDER=          # template | anthropic | gemini — empty auto-detects from the keys below
                             # (Anthropic preferred if both are set); "template" always wins.

ANTHROPIC_API_KEY=          # set to use AnthropicComposer
ANTHROPIC_MODEL=            # default: claude-sonnet-5
ANTHROPIC_TIMEOUT_SECONDS=  # default: 8

GEMINI_API_KEY=             # set to use GeminiComposer
GEMINI_MODEL=                # default: gemini-3.7-flash (current stable Flash model —
                              # gemini-2.5-flash is shut down; verified against
                              # ai.google.dev/gemini-api/docs/models, not assumed)
GEMINI_TIMEOUT_SECONDS=      # default: 8
```

A missing or invalid provider key never crashes the app — `get_default_composer()` falls back to
`TemplateComposer` and logs why (`composer_provider_unavailable` / `composer_config_invalid`).
Keys are read only from the environment, `.env` is gitignored, and `.env.example` holds only
empty placeholders — never a real value.

Before a real submission, also set `VERA_TEAM_NAME`, `VERA_TEAM_MEMBERS`, and
`VERA_CONTACT_EMAIL` — `/v1/metadata` currently returns `REPLACE_BEFORE_SUBMISSION` placeholders
for these rather than fabricated identity information.

### Real-model evaluation (diagnostic, not CI)

```bash
GEMINI_API_KEY=... python3 scripts/real_model_eval.py       # or ANTHROPIC_API_KEY=...
```

Runs "strong" and adversarial (prompt-injection, taboo-vocabulary-bait, thin-facts,
protected-field-name-injection) cases through the real `compose_and_validate` pipeline against
whichever provider key(s) are set, and reports firewall pass rate, fallback usage, and latency
per case. Never prints, logs, or writes a key value; refuses to run if neither key is set. This
is diagnostic output, not an official Magicpin judge score — only `judge_simulator.py` running
against a live deployment produces that.

## Run

```bash
make run
# POST http://localhost:8000/v1/context, /v1/tick, /v1/reply ; GET /v1/healthz, /v1/metadata
```

## Test

```bash
make test   # pytest
make lint   # ruff + mypy
```
