---
name: ingest
description: Refresh the fin-link market and news cache for held tickers. Use when the user wants updated prices, fundamentals, or news.
---

# Ingest market data

Refresh the regenerable cache in `data/`.

## When to use

The user asks for updated prices, fundamentals, or news, or before running `validate`.

## Steps

1. Run `finlink ingest [TICKER...]` (defaults to all held tickers).
2. Onboarding a **new** ticker: run `finlink onboard <ticker>` first. HK (`.HK`) and Swedish (`.ST`)
   tickers must be verified — if price or currency is null, **report a coverage failure**, do not
   store nulls.
3. Summarise what changed; do not dump raw data.

## Hard rules

- `data/` is a **cache, not a source of truth** — it is gitignored and safe to delete.
- Discard any item without a resolvable source URL and published date.
- **Never output a price or metric the CLI did not produce.**
- Ingestion is idempotent; re-running is safe.
