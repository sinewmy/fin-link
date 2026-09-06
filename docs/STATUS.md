
---

# Phase 1 — Status

**Completed.** Thesis capture: LLM abstraction, P1 decomposition, confirmation gate.

## What was added

| Component | File | Purpose |
|---|---|---|
| Output schemas | `finlink/llm/schemas.py` | `ThesisDecomposition` — the LLM contract |
| OpenRouter driver | `finlink/llm/drivers/openrouter.py` | OpenAI SDK -> OpenRouter, `require_parameters: true` |
| Echo driver | `finlink/llm/drivers/echo.py` | Deterministic fixtures, zero API cost |
| Client | `finlink/llm/client.py` | Prompt templating, retry-once, `llm_runs.jsonl` logging |
| P1 pipeline | `finlink/llm/pipelines/decompose.py` | reason -> thesis file (`status: draft`) |
| Prompt | `finlink/llm/prompts/decompose_thesis_v1.md` | Versioned, no opinions allowed |
| Config | `finlink/config.py` | Per-pipeline model IDs, API key |
| Git | `finlink/gitutil.py` | One commit per mutating run |

## Commands

```bash
finlink record-trade --ticker NVDA --side buy --quantity 20 --price 175 \
  --reason "..." --horizon 2y [--driver echo]
finlink confirm theses/NVDA-<slug>.md          # draft -> active
finlink set-frontmatter <file> --key status --value challenged
finlink cost-report
```

## To go live

1. Add to `config/config.yaml`:
   ```yaml
   models:
     P1_decompose: <your-model-id>
   openrouter_api_key: <key>       # or export OPENROUTER_API_KEY
   ```
2. Drop `--driver echo`.

## Bugs found and fixed

1. **Retry scaffolding leaked into generated content.** The correction message
   (`PREVIOUS OUTPUT WAS REJECTED...`) was appended to the user prompt, and the echo
   driver echoed it into the thesis body — exactly the corruption the append-only
   design exists to prevent. Fixed: retry text is isolated, and the echo driver strips
   it. Regression test: `test_echo_reason_extraction_ignores_retry_noise`.
2. **Echo driver received no user content**, producing empty statements that then failed
   validation and triggered the retry path. Fixed: P1 passes a structured `user_prompt`.

## Design guarantees verified

- Thesis is created `draft`; `confirm` is the only path to `active`
  (`test_pipeline_creates_draft_not_active`).
- Reason text stored verbatim (`test_reason_stored_verbatim`).
- Every LLM call logged with tokens, prompt version, and hash.
- Malformed output retries once then fails loudly; never stored as garbage.
- `require_parameters: true` asserted on every OpenRouter call.
- Generated files pass the same Pydantic model `doctor` uses.
- One git commit per run; `confirm` twice is rejected.

---

# Phase 2 — Status

**Completed.** Ingestion: prices, fundamentals, news, FX, and quant metrics.

## What was added

| Component | File | Purpose |
|---|---|---|
| Data model | `finlink/ingest/base.py` | `PriceBar`, `NewsItem`, `Fundamentals`, driver protocols |
| Cache store | `finlink/ingest/store.py` | Append-only, idempotent, disposable `data/` |
| yfinance driver | `finlink/ingest/yfinance_driver.py` | Prices + fundamentals, fails loud on gaps |
| Mock driver | `finlink/ingest/mock.py` | Deterministic offline data |
| FX | `finlink/ingest/fx.py` | Frankfurter (ECB), no API key |
| Pipeline | `finlink/ingest/pipeline.py` | P2 ingest + run logging |
| Quant | `finlink/domain/quant.py` | Returns, volatility, drawdown, SMA — computed in code |

## Commands

```bash
finlink ingest [TICKERS...] [--driver mock]   # idempotent; defaults to held tickers
finlink onboard <TICKER...>                   # verify coverage, fail loud
finlink quote <TICKER>                        # cached price + metrics
finlink news <TICKER> [--limit N]
finlink fx-update                             # refresh SEK (HKD stays pegged)
finlink show                                  # now values the portfolio from cache
```

## Environment note: Yahoo Finance is rate-limited here

From this machine Yahoo returns **HTTP 429** for `query*.finance.yahoo.com`
(general egress works — GitHub returns 200). This is IP-level, not a code bug, so
the same code should work from your own machine. Verify with:

```bash
finlink onboard AAPL
```

If it fails with a coverage/rate error, use `--driver mock` for offline testing, or
swap in another provider behind the `MarketDataDriver` protocol.

FX works today via Frankfurter (real rate fetched: 1 SEK = 0.1047 USD).

## Bugs found and fixed

1. **`fx-update` overrode the HKD peg.** It wrote a live HKD rate into `config.fx`,
   which `build_rates()` prefers over the peg — contradicting "HKD is pegged, do not
   float it". Now HKD is skipped when `hkd_peg` is set.
2. **Closure captured loop variables** in the yfinance price loop (ruff B023): every
   bar would have reported the last row's values. Fixed by binding as default args.
3. **Mock driver invented data for unknown tickers**, so `onboard` reported OK for
   `FAKE-NOT-REAL`. Now strict by default in the CLI.
4. **Frankfurter returned 403** without a User-Agent header.

## Known gaps

- News is only wired for the mock driver; a real RSS/news driver is not yet
  connected (the `NewsDriver` protocol and dedupe/provenance logic are ready).
- `finlink show` ignores stale-price warnings; it uses whatever is cached.
