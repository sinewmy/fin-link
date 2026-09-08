# Handoff — 2026-09-08 (evening)

Short pickup note for whoever continues fin-link next. Last updated: Tue Sep 8
~21:00 HKT (2026-09-08). Session: escaped the AV quota trap with a free Tencent
driver, ingested all 7 tickers, ran real-LLM validations, hit + fixed three
numeric-audit bugs in `review`, and deliberately STOPPED before burning more
paid calls on the weekly review.

## Current state — WHAT WORKS

- **All 7 held tickers have real prices** via the Tencent driver
  (`data/prices/*.csv`): 00700.HK, 00981.HK, 01810.HK, 02359.HK (HKD, as of
  2026-09-08), BABA/GLD/SGOV (USD, as of 2026-09-04, cross-checked identical to
  the old AV cache).
- News cached for all 7. Fundamentals empty for HK (Tencent is prices-only;
  BABA retains old AV metrics).
- **All 7 theses validated with the real LLM** (2026-09-08 sections appended,
  committed `802babb` + `265ac99`):
  - 00700.HK partially_valid/medium, 00981.HK partially_valid/medium,
    01810.HK undetermined/low, 02359.HK challenged/medium,
    BABA undetermined/low, GLD undetermined/low, SGOV undetermined/low.
  - 00700.HK file has **3 validation sections** from today (re-run 3× in the
    all-thesis runs; harmless but worth a cleanup if you care).
- `finlink show / exposure / snapshot / risk-check / doctor` all work.
  Snapshot row for 2026-09-08 written (HHI 0.0965, cash 35.28%, vol 18.56%).
- Tests: **255 passed**, `ruff` clean, `doctor` OK.

## Code state (all uncommitted — user is committing)

- `finlink/ingest/tencent_driver.py` — **NEW**; free keyless HK+US OHLCV
  (web.ifzq.gtimg.cn fqkline). Mapping: `00700.HK -> hk00700` (leading zeros
  kept), `BABA -> usBABA.N`, `GLD -> usGLD.AM`, `SGOV -> usSGOV.N`, bare US ->
  `us<T>.N`. Fundamentals returned empty. **Default market driver now**
  (`finlink/cli.py::_drivers`); `--driver alphavantage` kept for fundamentals.
- `finlink/llm/schemas.py` — numeric audits rebuilt:
  - `_numbers()` now thousands-aware + `.`-only decimal tokenizer, preserves
    ticker/date digits (fixes `00700`/`10`/`80` corruption).
  - `check_numbers` (review) is **value-based** with 0.01% relative tolerance:
    accepts rounding (`225005` ≈ `225005.06`), rejects invented baselines
    (`225600.83`, `35.19`, `0.15` as a fraction).
  - `check_position_note_numbers` (validate) strips ticker tokens.
- `finlink/llm/pipelines/review.py` — `allowed_numbers` now derives from
  `render_facts` (single source of truth); passes tickers into the audit.
- `finlink/llm/pipelines/validate.py` — direction retry via `validate_extra`.
- `finlink/llm/client.py` — retries 2 → 3 attempts.
- Prompts hardened: `review_v1.md` (do NOT reconstruct a past not in the
  facts), `validate_synthesis_v1.md` (NEVER do arithmetic), both evidence-pass
  prompts (required `hypothesis_id`).
- Tests added: Tencent driver, ticker-digits, thousands-separator, retry,
  rounding-vs-unit-change. `tests/test_phase1_llm.py` expects 3 attempts now.

## Known blocker — `finlink review` (real LLM) fails this week

**Symptom**: `finlink review --week 2026-W37` raises
`ValueError: review narrative contains numbers the CLI did not compute: ...` —
each run names a DIFFERENT invented number (595.77, 225600.83, 35.19, 0.15,
then `(2)/(4)/(5)/(6)` list markers).

**Root cause**: this is the FIRST snapshot week — `start_snapshot ==
end_snapshot` (both derived from today's prices), zero trades in period, and
no prior-week total exists. The model, told to write a retrospective with
nothing to compare, fabricates a prior-week baseline / return delta / numbered
list, and the (now-correct) numeric audit rejects it. It's a CONTENT problem,
not an audit bug — the audit verifiably accepts every number present in the
facts (repro: build `ReviewInput` from `cli._portfolio_view` + `render_facts`,
run `allowed_numbers`, all fact figures pass).

**Options** (do NOT just keep re-running paid LLM):
1. **Wait for next week** — once there are 2+ snapshot rows or period trades,
   the drift/return data is real and the review should complete.
2. `finlink review --week 2026-W37 --driver echo` — zero-cost shape preview.
3. If a real review is needed NOW with no history, the honest fix is to make
   the CLI pass an explicit "no prior-week comparison available" sentinel into
   `render_facts`/prompt and (ideally) skip asking for a "portfolio change"
   half — TBD.

## Remaining steps (next session)

1. **Commit** everything (user's plan; sandbox .git is read-only so the
   auto-commit inside commands fails — expected).
2. Verify the committed tree: `git status`, `finlink doctor`, 255 tests.
3. Revisit `finlink review` only when there's real history (>1 snapshot or
   period trades), or use `--driver echo` for a shape check.
4. Optional cleanup: dedupe 00700.HK's three validation sections.
5. Optional: fetch fundamentals for the 4 HK tickers (needs a priced source —
   AV is IP-locked here; Tencent has none). Not a pipeline blocker.

## Watch-outs

- AV free tier is IP-locked on this machine (both keys "25 req/day" even >4h
  after ET midnight) — do NOT rely on AV for prices here.
- Tencent is keyless/free but has no SLA; if US history returns 1 bar, the
  MIC suffix (`.N`/`.AM`) is missing — check `_symbol()`.
- `--driver echo` is offline/zero-cost; any real LLM run costs money and
  needs user approval.
- `data/` is a disposable cache; deleting + `ingest` restores it.
- `doctor` before/after every mutation.
