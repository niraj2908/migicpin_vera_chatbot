# CLAUDE.md

Rules for working in this repository. Follow these before writing or changing any code.

## Architecture boundary (non-negotiable)

- The decision layer (`vera/decision/`) is deterministic. Given the same normalized input, it
  must return the same `Decision`. Never let an LLM call influence `send`, `action_type`,
  `cta`, `suppression_key`, or scoring.
- The LLM (`vera/generation/composer.py`) only turns an already-decided `CompositionBrief` into
  a sentence. It must never receive raw unfiltered context, and its output must never bypass
  `vera/generation/firewall.py` before being returned.
- Every fact the LLM is allowed to state must originate from `Decision.facts_allowed`, built
  from real merchant/trigger data — never invented in a prompt.

## Required for any new behavior

- Add or update tests in `tests/unit/` (and `tests/contract/` if the API contract is touched).
  Cover: the happy path, suppression, missing/optional context, and category mismatch where
  relevant.
- Run `make test` and `make lint` before considering a change done.
- Do not change the `/v1/compose` request/response schema without explicit instruction — it is
  the challenge contract.

## Style

- Small, cohesive modules with typed interfaces (this is a Python project — use type hints and
  pydantic models, not dicts, at module boundaries).
- No comments explaining *what* code does; only the non-obvious *why* when it isn't clear from
  naming.
- No speculative abstractions, no unused dependencies, no dead code paths.
- No secrets committed. `.env` is gitignored; use `.env.example` for documenting required vars.

## Explicitly out of scope unless asked

- WhatsApp adapter, dashboard, voice interface — future channels, not part of the challenge
  kernel.
- Kubernetes, Kafka, vector databases, multi-agent orchestration — not needed at this scale.
