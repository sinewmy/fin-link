
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
   openrouter_api_key: <key>       # or export OPENROUTER_API_KEY_CODEX
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

---

# Phase 3 — Status

**Completed.** Hypothesis validation: the core loop.

## What was added

| Component | File | Purpose |
|---|---|---|
| Risk engine | `finlink/domain/risk.py` | Pure rule evaluation -> `Alert`; never touches an LLM |
| Portfolio context | `finlink/domain/context.py` | Per-name weight/sector/country/breaches + number allow-list |
| Relevance filter | `finlink/domain/relevance.py` | Deterministic stage-1 matching (no model call) |
| Horizon maths | `finlink/domain/horizon.py` | `2y`/`6m`/`30d` -> end date; expiry detection |
| Schemas | `finlink/llm/schemas.py` | `EvidencePass`, `Synthesis` with the bias guards baked in |
| Prompts | `finlink/llm/prompts/validate_*_v1.md` | Three versioned prompts: supporting, contrary, synthesis |
| Pipeline | `finlink/llm/pipelines/validate.py` | P3: pre-filter -> Pass A -> Pass B -> context -> verdict |
| Echo fixtures | `finlink/llm/drivers/echo.py` | P3 runs end-to-end offline at zero cost |

## Commands

```bash
finlink validate [--thesis SLUG] [--ticker T] [--driver echo] [--dry-run]
finlink status                       # one row per thesis, incl. horizon overdue
finlink doctor                       # before and after
```

## Design guarantees verified

- **Pass B never sees Pass A.** Both passes receive identical raw candidates; only the
  system prompt differs. Asserted by `test_pass_b_never_receives_pass_a_output`.
- **`still_valid` with an empty contrary section is impossible** at the schema level, and
  the echo driver adds a second guard: no contrary evidence -> `undetermined`.
- **Append-only.** The body is never rewritten; only named frontmatter keys change.
  Regression test: `test_handwritten_content_is_byte_identical_after_validation`.
- **No model-generated numbers.** `position_note` is audited against the computed
  context; a figure absent from it fails validation.
- **Horizon expiry** marks stale hypotheses `expired` — expired is never "supported".

## Bugs found and fixed

1. **Echo driver matched `"BREACHED"` as a substring**, so a portfolio *inside* its limits
   ("no limits breached") was reported as breached. Now matches the explicit `LIMIT ...`
   marker.
2. **Jaccard overlap starved long metrics.** A multi-clause `observable_metric` scored
   ~0.09 against a headline that genuinely reported on it, so every real item was
   filtered out and every validation came back `undetermined`. Switched to metric
   coverage (|intersection| / |metric|).
3. **Contrary pass crashed on a single candidate.** Splitting the candidate list left
   Pass B with nothing and no `not_found_reason`, which the schema (correctly) rejected.
   It now returns an honest "reviewed N, found none".
4. **A stacked `@property` decorator** in `context.py` silently shadowed the
   `PortfolioContext` class, so `build_context` returned the class, not an instance.
   Caught by tests, not by mypy.

## Known gaps

- Sector and country maps are not yet populated from fundamentals; `build_context`
  reports `Unclassified`/`Unknown` until a mapping is wired in (Phase 5).
- `alerts.md` is not written yet — the risk engine runs and feeds validations, but the
  standalone `finlink risk-check` command arrives with Phase 4.
- News is only wired for the mock driver, as noted in Phase 2.

---

# Phase 4 — Status

**Completed.** Review & risk: two-half weekly review, alert ledger, §16 metrics.

## What was added

| Component | File | Purpose |
|---|---|---|
| Alert ledger | `finlink/domain/alerts.py` | `alerts.md` lifecycle: open -> acknowledged -> resolved |
| Drift + patterns | `finlink/domain/drift.py` | Weight drift, round trips, behavioural patterns |
| Usage metrics | `finlink/domain/usage.py` | Product doc §16 process measures |
| Review schema | `finlink/llm/schemas.py` | `ReviewNarrative` + numeric audit |
| Prompt | `finlink/llm/prompts/review_v1.md` | Versioned P4 prompt |
| Pipeline | `finlink/llm/pipelines/review.py` | P4: computed facts + model narrative |

## Commands

```bash
finlink risk-check                    # evaluate rules, regenerate alerts.md
finlink alerts                        # show the ledger
finlink alerts --ack <rule>:<scope>   # acknowledge (never deletes)
finlink review [--week 2026-W37] [--publish]
```

## Design guarantees verified

- **Alerts are acknowledged, never silently cleared.** A condition that stops breaching
  is marked `resolved` and kept; an acknowledged alert is never auto-resolved.
- **No LLM path can suppress an alert.** `Alert` is frozen, evaluation is a pure
  function of `(view, rules)`, and the narrative has no input to the risk engine.
- **Both review halves always render**, with every figure from `domain/`.
- **Behavioural patterns are countable** and cite the trades behind them.
- **Thesis-vs-portfolio conflicts** are surfaced: valid thesis + breached limit.

## Known gaps

- Sector/country are still `Unclassified`/`Unknown` — no mapping from fundamentals yet
  (Phase 5). Drift is computed per position today.
- `max_transaction_pct` is only evaluated when the caller supplies a figure; the CLI
  does not yet pass one.
- `--publish` writes into `~/knowledge-base/Wiki/` but does not check `AGENTS.md` rules
  or handle an existing file (it overwrites).
- Drawdown is taken as the worst single-name drawdown, not a portfolio-level series
  (that needs a snapshot history — Phase 5).

---

# Phase 5 — Status

**Completed.** Portfolio intelligence: exposure, correlation, snapshots, static HTML.

## What was added

| Component | File | Purpose |
|---|---|---|
| Classification | `finlink/domain/classify.py` | Sector/country/currency from config + ticker suffix |
| Exposure | `finlink/domain/exposure.py` | Sector/country/currency exposure, HHI, growth/defensive |
| Statistics | `finlink/domain/stats.py` | Correlation matrix, portfolio volatility, drawdown |
| Snapshots | `finlink/domain/snapshots.py` | Append-only history for drift charts (idempotent per day) |
| Static HTML | `finlink/report/html.py` | Inline-SVG report, no server, no external assets |

## Commands

```bash
finlink exposure           # currency/sector/country exposure + concentration
finlink snapshot           # record today (idempotent per day)
finlink report             # self-contained HTML with charts
finlink report --with-correlation
```

## Design guarantees verified

- **SEK/HKD exposure is explicit.** Currency weights are USD-normalised and printed as
  their own table plus an FX summary line.
- **Classification is deterministic**, never guessed from a company name: explicit
  `sectors:`/`countries:` config -> ticker suffix (`.HK`, `.ST`) -> position currency
  -> `Unclassified`. Unmapped names are *counted and flagged*, not silently bucketed.
- **Correlation is honest about data.** A pair with too few overlapping observations is
  reported `n/a` rather than dropped, and a constant series is undefined (0), not 1.
- **The HTML report is offline-safe.** Zero external fetches; charts are inline SVG.
  User-controlled text is HTML-escaped.

## Notes

- All maths is Decimal-only — no numpy, no floats.
- With the mock driver every pair correlates ~1.00 because it emits the same
  deterministic wave; real prices give real dispersion (verified at +0.995/-0.996).
- Portfolio drawdown is now computed from the weighted return series, closing the
  Phase 4 gap where it used the worst single-name drawdown.

## Known gaps

- Sector/country still need the user to supply `sectors:`/`countries:` in config; only
  suffix-based country inference happens automatically.
- Snapshots are manual (`finlink snapshot`) — no scheduler, by design (D6).
- History charts need >= 2 snapshots before they appear.

---

# Phase 5+ — Status

**Completed.** Classification coverage + `finlink improve-slugs`.

## What was added

| Component | File | Purpose |
|---|---|---|
| Canonical ticker match | `finlink/domain/classify.py` | `canonical()` — config keys match across case, leading zeros, suffix |
| Adoptability gate | `finlink/domain/slug.py` | `is_adoptable()` — rejects a slug unfit to be a link target |
| Slug schema | `finlink/llm/schemas.py` | `SlugSuggestion` — words only, never the slug |
| Prompt | `finlink/llm/prompts/slug_v1.md` | Versioned P6 prompt |
| Pipeline | `finlink/llm/pipelines/slug.py` | P6: model proposes words, `domain/` derives the slug |
| Config coverage | `finlink/cli.py` | `check` now warns on unmapped holdings |

## Commands

```bash
finlink check                              # also reports missing sector/country mappings
finlink improve-slugs --driver echo --dry-run
finlink improve-slugs --only-unreadable    # CJK / hash slugs only
finlink improve-slugs --ticker BABA
```

## Bugs found and fixed

1. **`sectors:` / `countries:` keys never matched.** The config used broker names
   (`'INVESTOR B'`, `'LUNDIN GOLD'`) and bare codes (`'00700'`), while classification
   works with normalised tickers (`INVE-B.ST`, `0700.HK`). Every one of those holdings
   reported `Unclassified` — silently, in every exposure report. Fixed by matching on a
   canonical form (case-insensitive, leading zeros stripped, suffix-aware) and by
   switching the config to real symbols.
2. **`backfill-theses` crashed with `--replace`.** The option was declared on the
   decorator but missing from the function signature: 6 tests failed with
   `TypeError: unexpected keyword argument 'replace'`.

## Design note: why the model does not pick the slug

A slug is a filename *and* a link target. Letting a model name it outright means a
re-run can break every link in `positions.md` — creativity is a bug here. So the
model proposes **words**, `domain/slug.py` derives the slug, and `is_adoptable()`
rejects anything that would not survive as a link. That is what makes
`improve-slugs` safe to run when the deterministic rule produced a name like
`cjk-770b597d817e`.

Verified: `00700.HK-cjk-770b597d817e.md` -> `00700.HK-fundamentals-tencent.md`, with
the file, its frontmatter, and all four `positions.md` links moving together.

## Known gaps

- `improve-slugs` uses `--driver echo` fixtures offline; the real quality of the
  naming depends on the configured `P6_slug` model.
- `check` warns about unmapped sectors but does not write them — adding a mapping is
  still a deliberate, manual edit.

---

# Phase 7 — Ledger as source of truth (record-trade syncs positions + cash)

**Completed.** `record-trade` now keeps `ledger.md`, `positions.md` and `cash.md`
consistent in a single atomic commit, and `seed-ledger` migrates an existing
hand-seeded portfolio into ledger-as-truth.

## What was added

| Component | File | Purpose |
|---|---|---|
| Sync logic | `finlink/ledger_sync.py` | positions derived from ledger (FIFO), incremental cash deltas, table writers |
| Seed bridge | `finlink seed-ledger` | hand-seeded `positions.md` -> opening `buy` rows; refuses if ledger non-empty |
| Collision guard | `finlink record-trade` | slug collision is checked BEFORE any file write (no partial mutations) |
| Tests | `tests/test_ledger_sync.py` | derivation, cash debits/credits, seed, multi-currency |

## Design

- `ledger.md` is the event log / source of truth. `positions.md` and `cash.md` are
  current-state registries.
- `record-trade` re-derives positions from the ledger and applies ONLY the new trade's
  cash delta in its source currency — it never replays history against current cash.
- Sells never create theses and require no LLM driver (no API key needed).
- A same-slug re-buy fails **before** writing anything, telling the user how to link
  the existing thesis or pass `--slug`.

## Flow for the current portfolio (to follow when ready)

1. Polish `positions.md` / `cash.md` by hand.
2. `finlink seed-ledger` -> ledger gets one buy per holding.
3. `finlink backfill-theses --driver echo --dry-run` -> create theses from `notes`
   (or `record-trade` fresh buys), then `finlink confirm` each.
4. Then `record-trade` / `ingest` work as documented.

