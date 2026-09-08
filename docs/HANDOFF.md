# Handoff — 2026-09-08 (afternoon)

Short pickup note for whoever continues fin-link next. Last updated: Tue Sep 8
~18:20 HKT (2026-09-08). Previous session: escaped the Alpha Vantage quota trap
with a free keyless Tencent driver, ingested all 7 held tickers.

## Goal

Run the remaining pipeline (risk, review, exposure, snapshot, report) with real
prices, and validate theses against real evidence with the real LLM.

## Current state — DONE

- **All 7 held tickers now have real prices** via the new Tencent driver
  (`data/prices/*.csv`, ~393 daily bars each, last dates correct):
  - `00700.HK` (tencent 435.40 HKD, 2026-09-08), `00981.HK`, `01810.HK`,
    `02359.HK` — all HKD, from HKEX via Tencent.
  - `BABA`, `GLD`, `SGOV` — USD, cross-checked identical to the old AV cache
    (2026-09-04 close/volume match exactly).
- News cached for all 7 (`data/news/*.jsonl`, 30+ items each).
- Theses exist + active for all 7 (`theses/*.md`). SE tickers removed from
  `positions.md` + ledger + theses (`INVE-B.ST`, `LUG.ST`).
- `finlink doctor` green. `finlink quote 00700.HK` works (last=435.4, SMA50).
- Fundamentals metrics: empty for HK tickers (Tencent has no P/E/market cap);
  BABA still has AV metrics from the old cache.

## Code state (all uncommitted this session)

- `finlink/ingest/tencent_driver.py` — **NEW**; free, keyless daily OHLCV for HK
  + US via `web.ifzq.gtimg.cn/appstock/app/fqkline/get`. Symbol mapping:
  `00700.HK -> hk00700` (leading zeros kept), `BABA -> usBABA.N`,
  `GLD -> usGLD.AM`, `SGOV -> usSGOV.N`; bare US -> `us<T>.N`. Fundamentals
  returned empty (prices only).
- `finlink/cli.py::_drivers` — **Tencent is now the default market driver.**
  `--driver alphavantage` still available for fundamentals; `--driver mock`
  offline.
- `finlink/ingest/alphavantage_driver.py` — kept (fundamentals + fallback), but
  **AV free tier is IP-locked on this machine** (both keys report 25/day cap
  even >4h after ET midnight, zero usage between). AV is NOT reliable for
  prices here.
- Also in worktree (from previous sessions, uncommitted):
  `rss_news_driver.py` (Google News), relevance prefilter, validate pipeline
  fixes. Tests: **250 passed**, `ruff` clean.

## Remaining steps (in order)

1. **Unlock portfolio commands** (all require full prices — should work now):
   ```bash
   .venv/bin/finlink show
   .venv/bin/finlink risk-check
   .venv/bin/finlink exposure
   .venv/bin/finlink snapshot
   .venv/bin/finlink report
   .venv/bin/finlink review --driver echo   # dry, offline
   ```
2. **Run real LLM validation** (paid, needs explicit user approval):
   ```bash
   .venv/bin/finlink validate --thesis <slug>   # one
   .venv/bin/finlink validate                   # all active
   ```
   OpenRouter key + `deepseek/deepseek-v4-flash` models configured.
3. **Commit** uncommitted work.

## Watch-outs

- Do NOT refetch BABA/GLD/SGOV unless needed — already cached; re-running with
  `--driver alphavantage` would burn AV quota for no new rows.
- Tencent is a Chinese consumer API — free, keyless, no SLA. If it stops
  returning US history (only 1 bar = suffix missing), check `_symbol()`.
- Third-party APIs (Tencent, Google News RSS) are the only live network calls;
  everything else reads `data/`.
- `doctor` before/after every mutation.
- `data/` is a disposable cache; deleting + re-ingesting restores it.
