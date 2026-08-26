# Vera Engine

A deterministic merchant-growth decision engine with an LLM language layer, built for the
MagicPin Vera AI Challenge.

Core principle: **software decides whether, why, when, for whom, and how a message should be
sent; the LLM only writes the sentence.**

## Pipeline

```
context -> decision compiler (opportunity scoring, suppression) -> composition brief
        -> LLM composer -> output firewall -> response
```

See `docs/engineering-spec.md` for the full architecture and rationale. This repo currently
implements the vertical slice: one category
(restaurant) and one trigger type (festival) through the complete pipeline, as the foundation
to widen from.

## Setup

```bash
python3 -m venv .venv && source .venv/bin/activate
make install
cp .env.example .env   # optionally set ANTHROPIC_API_KEY to use the real LLM composer
```

Without `ANTHROPIC_API_KEY` set, the pipeline uses `TemplateComposer` — a deterministic,
always-grounded fallback — so the API is runnable and testable with no external dependency.

## Run

```bash
make run
# POST http://localhost:8000/v1/compose
```

## Test

```bash
make test
```

## Layout

```
src/vera/
├── api/          challenge-facing HTTP contract
├── domain/       merchant/customer/trigger models, truth classification
├── decision/     opportunity generation + deterministic decision compiler
├── generation/   composition brief, LLM composer, output firewall
tests/
├── unit/         decision compiler, opportunity scoring, firewall
└── contract/     API request/response contract
```

Further structure (`intelligence/`, `state/`, `security/`, `observability/`, adversarial and
load tests, evaluation harness) is added as the corresponding build phase starts — this repo
intentionally does not carry empty scaffolding ahead of real content.
