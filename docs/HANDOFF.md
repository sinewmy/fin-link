# Handoff — 2026-09-08

Short pickup note for whoever continues fin-link next. Last updated: Tue Sep 8
00:47 HKT (2026-09-08). Previous session: ingest + downstream pipeline debugging.

## Goal

Finish data ingestion for the 9 held tickers, then run the remaining pipeline
(risk, review, exposure, snapshot, report) with real prices, and validate theses
against real evidence with the real LLM.

## Current state

- Prices + fundamentals cached for **3 of 9**: `BABA`, `GLD`, `SGOV`
  (`data/prices/*.csv`, `data/metrics/*.json`, as of 2026-09-04).
- News cached for **all 9** (`data/news/*.jsonl`, 15 items each).
- Theses exist + active for all 9 (`theses/*.md`).
- Doctor passes, `finlink status` shows 9 active.
- **Alpha Vantage both keys daily-capped 25/day**; they reset ~midnight ET =
  12:00 noon HKT. All 6 missing tickers fail with `daily request cap reached`
  until then.
- Missing prices: `00700.HK`, `00981.HK`, `01810.HK`, `02359.HK`,
  `INVE-B.ST`, `LUG.ST`.
- The `.env` has 2 AV keys; Finnhub key present but returns 403 (likely
  expired/inactive) — don't rely on it.

## Code state (all uncommitted)

- `finlink/ingest/alphavantage_driver.py` — **new**; multi-key round-robin
  (`ALPHAVANTAGE_API_KEY` + `ALPHAVANTAGE_API_KEY-2` in `.env`), falls over on
  429 AND on AV's JSON daily-cap message, sleeps 13s/call. Symbol mapping:
  HK `.HK` -> 4-digit `.HK` (no `.HKG` rewrite); SE `.ST` untouched.
- `finlink/ingest/rss_news_driver.py` — **new**; Google News RSS, no key.
- `finlink/domain/relevance.py` — added `best_metric_overlap` +
  `prefilter_metrics` (per-hypothesis scoring, no dilution).
- `finlink/llm/pipelines/validate.py` — uses `prefilter_metrics`.
- Removed `yfinance_driver.py` (Yahoo 429s from this IP); dropped yfinance dep.
- Tests: `tests/test_phase2_ingest.py`, `tests/test_phase3_validate.py`
  (added multi-key rotation, HK symbol, dilution regression). **245 passed**
  last full run.

## Remaining steps (in order)

1. **Fetch remaining prices** (after ET-midnight reset):
   ```bash
   .venv/bin/finlink ingest 00700.HK 00981.HK 01810.HK 02359.HK INVE-B.ST LUG.ST \
     --driver alphavantage --days 100
   ```
   - Uses both AV keys automatically, ~13s/call, so ~10 min if quota is fresh.
   - If `INVE-B.ST` / `LUG.ST` still return `{}`, that's an AV coverage gap
     (SE not supported). Decide then: keep them as manual/cash-style positions
     or find another source (yfinance no; Finnhub key invalid).
2. **Verify cache**: rows in `data/prices/*.csv`, `data/metrics/*.json` for all 9.
3. **Unlock portfolio commands** (all require full prices):
   `finlink show`, `risk-check`, `exposure`, `snapshot`, `report`,
   `finlink review --driver echo` (dry), then real.
4. **Run real LLM validation** (paid, needs approval):
   ```bash
   .venv/bin/finlink validate --thesis <slug>            # one
   .venv/bin/finlink validate                            # all active
   ```
   OpenRouter key + `deepseek/deepseek-v4-flash` models already configured.
5. **Commit** uncommitted work (prev agent left everything staged-in-worktree).

## Watch-outs

- Do NOT refetch BABA/GLD/SGOV (already cached; halves AV quota usage).
- AV daily cap: keep runs <= ~24 calls/key/day.
- `--driver echo` is offline/zero-cost; real LLM costs money, get approval first.
- `doctor` before/after every mutation.
- `data/` is a disposable cache; deleting + re-ingesting restores it.
