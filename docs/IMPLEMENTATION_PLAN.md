# fin-link — Phased Implementation Plan

> Prerequisite reading: `docs/TECHNICAL_DESIGN.md` v2.0.
> **Architecture: markdown + git, no database, no web GUI, manual invocation only.**
> This file is the **work order**. Each phase is independently usable.
> Status: draft v2.0

---

## Naming note

The product doc's "Phase 1" means *Personal Investment Journal*. This plan inserts a **Phase 0
(Foundation)** before it, so numbering is offset by one against the product doc:

| This plan | Product doc |
|---|---|
| Phase 0 — Foundation & ledger | (prerequisite) |
| Phase 1 — Journal (thesis capture) | Phase 1 — Personal Investment Journal |
| Phase 2 — Research Assistant | Phase 2 — AI Research Assistant |
| Phase 3 — Hypothesis Validation | Phase 3 — Hypothesis Validation |
| Phase 4 — Review & Risk | §8, §16 (+ product doc Phase 4) |
| Phase 5 — Portfolio Intelligence | product doc Phase 4 |

Pipelines **P1–P5** (TECHNICAL_DESIGN §5) are a *separate* numbering from these phases
(e.g. pipeline P1 runs inside plan-Phase 1).

---

## 0. Guiding rules for the implementer

1. **Finish a phase before starting the next.** Exit criteria are checkboxes; none may fail.
2. **Append, never rewrite.** An agent rewriting a thesis file can destroy hand-written evidence.
   Validations append a section; status changes mutate named frontmatter keys only.
3. **Atomic write + git commit** after every mutating run; damage must be one `git diff` from undone.
4. **No numbers from the model.** All figures come from `finlink domain/`; the model interprets.
5. **Every claim carries a source URL and published date.**
6. **`finlink doctor` must pass** before and after every pipeline run, and in CI.
7. **Every pipeline runs offline** with the `echo` driver at zero API cost.
8. **`require_parameters: true`** on every OpenRouter call.

---

## Phase 0 — Foundation & ledger
**Goal:** a correct, trustworthy portfolio ledger in markdown, with zero AI.
**Value to you:** you get a ledger and honest PnL numbers *before* any model touches them. If the
numbers are wrong, nothing built above them matters.
**Deliberately excluded:** LLM, news, validation, reports, ingestion.

### Scope
- `uv` project: pyproject, ruff, mypy, pytest, pre-commit. `finlink` CLI entrypoint.
- `git init`; `.gitignore` for `data/`; repo skeleton per TECHNICAL_DESIGN §2.
- `config/config.yaml` — base currency USD, risk-rule placeholders, `models:` block left for the user.
- Pydantic models for frontmatter + ledger/position rows.
- `domain/portfolio.py`, `domain/pnl.py` — weights, cash, cost basis, realised/unrealised PnL,
  **USD normalisation** (HKD pegged 7.8, SEK live). `Decimal` only.
- `portfolio/positions.md`, `portfolio/ledger.md`, `portfolio/cash.md` + hand-edit support.
- Generic CSV import for existing holdings.
- **`finlink doctor`** — validates all files against Pydantic models, checks links, fails loud.
- `finlink show` — renders portfolio as a markdown table.
- `skills/ingest` is *not* in this phase; `risk-check` scaffolding begins in Phase 4.

### Exit criteria
- [ ] Buy 10 AAPL @ 100, sell 4 @ 120 -> realised PnL, remaining cost basis, weights provably correct.
- [ ] A SEK-denominated holding converts to USD correctly; HKD uses the 7.8 peg (unit-tested).
- [ ] No floats anywhere in financial code (grep-checked).
- [ ] `finlink doctor` passes on a deliberately corrupted file -> **fails with a clear error**.
- [ ] `finlink show` renders correct holdings + cash + weights.
- [ ] ruff, mypy, pytest green.

---

## Phase 1 — Personal Investment Journal (thesis capture)
**Goal:** every transaction carries a reason; the reason becomes a structured, falsifiable thesis.
**Value to you:** this is the actual experiment the product doc asks for — *will you record your
reasoning, and does it change your behaviour?* Everything else is leverage on this dataset.
**Maps to:** product doc Phase 1 + §4.

### Scope
- `llm/` module: OpenAI SDK -> OpenRouter, `require_parameters: true`, Pydantic structured output,
  `echo` driver for offline tests, versioned prompt templates, `logs/llm_runs.jsonl`, `finlink cost-report`.
- Pipeline **P1 thesis_decompose**: reason text -> 1 core + 2–5 sub hypotheses, each with an
  `observable_metric`; `invalidation_conditions` populated.
- **User confirmation gate:** created as `status: draft`; user edits/approves -> `active`.
- `finlink record-trade` — appends to `ledger.md`, creates thesis file, commits.
- `finlink set-frontmatter --key --value` — key-scoped mutation (never whole-file rewrite).
- `skills/record-trade/SKILL.md`, copied to `.agents/skills/record-trade/SKILL.md` for project-level discovery.

### Exit criteria
- [ ] "NVDA keeps growing because AI datacenter capex keeps rising" -> core hypothesis + >=2
      sub-hypotheses with concrete observable metrics (e.g. "hyperscaler capex guidance").
- [ ] Thesis is `draft` until confirmed; confirmation is the only path to `active`.
- [ ] Original reason text stored **verbatim** under `## Thesis`.
- [ ] `finlink record-trade` runs with the `echo` driver at zero cost.
- [ ] Every LLM call has an `llm_runs.jsonl` entry with tokens, cost, prompt version.
- [ ] Malformed model output retries once, then **fails loudly**; never stored as garbage.
- [ ] `git log` after a run shows exactly one commit per mutation; `git checkout` restores prior state.

---

## Phase 2 — AI Research Assistant (data in)
**Goal:** automatically pull prices, fundamentals and news for what you hold.
**Value to you:** no more manual lookups, and you start building the evidence corpus Phase 3 needs.
**Maps to:** product doc Phase 2 + §9.

### Scope
- `ingest/` + `yfinance` driver (prices, fundamentals) + RSS news driver + `mock` driver.
- Normalisation into `data/prices/*.csv` (append-only), `data/metrics/*.json`, `data/news/*.jsonl`
  (deduped by URL). **Uncited items discarded.**
- `finlink onboard <ticker>` — verifies price + currency resolve; **fails loudly for HK/Swedish tickers
  with null data** (market caveat, TECHNICAL_DESIGN §2.3).
- `domain/quant.py` — returns, volatility, max drawdown, PE/PS/PB, growth, margins, debt, FCF.
- `finlink ingest [TICKER...]`, `finlink quote <ticker>`, `finlink news <ticker>`.
- `skills/ingest/SKILL.md`.

### Exit criteria
- [ ] `finlink ingest` populates prices + news; running twice creates no duplicates.
- [ ] Every news item has a resolvable `source_url` and `published_at`; uncited rejected.
- [ ] `finlink onboard 0700.HK` and `finlink onboard VOLV-B.ST` either succeed or **fail with a clear
      coverage error** — never silently store nulls.
- [ ] Valuation/drawdown figures reconcile with an external source for one ticker (manual spot-check).
- [ ] No LLM output writes any number into `data/` or any report (code review + test).
- [ ] Deleting `data/` and re-running `ingest` fully restores it (cache-not-source proof).

---

## Phase 3 — Hypothesis Validation (the core loop)
**Goal:** new information continuously and *adversarially* re-tests your theses.
**Value to you:** the product doc calls this the phase genuinely worth investing in. It's what no
journal tool does: your old reasoning gets re-examined, including for evidence that proves you wrong.
**Maps to:** product doc Phase 3 + §5.

### Scope
- Two-stage relevance matching: deterministic pre-filter, then LLM classification.
- Pipeline **P3 validate** with mandatory structure:
  - Pass A -> supporting evidence
  - Pass B -> contrary evidence, **independent, Pass A output withheld**
  - Portfolio context computed by `domain/` (§4.4) -> one model-written `position_note`
  - Verdict + confidence enum + required `### Uncertainty` section
- **Append-only** `## Validation — <date>` section; key-scoped frontmatter status update; git commit.
- Horizon expiry -> hypotheses `expired`; status history visible via `git log`.
- `finlink validate [--thesis SLUG]`, `finlink status`.
- `skills/validate/SKILL.md`.

### Exit criteria
- [x] A thesis with new news appends a validation containing **both** supporting and contrary sections.
- [x] Schema-level rejection: verdict `still_valid` with an empty contrary section is impossible.
- [x] Pass B runs without access to Pass A output (asserted in test).
- [x] A still-valid thesis on an over-weight position produces a `position_note` naming the breach
      (e.g. "22% of portfolio against a 15% limit") — P3 no longer validates in isolation.
- [x] A `position_note` containing a number absent from the computed portfolio context **fails
      validation** (negative test required).
- [x] Horizon expiry moves stale hypotheses to `expired`.
- [x] `finlink validate` runs end-to-end on the `echo` driver at zero cost.
- [x] **Hand-written content in an existing thesis file is byte-identical after a validation run**
      (regression test — this is the append-only guarantee).

---

## Phase 4 — Review & Risk (making it change behaviour)
**Goal:** periodic reflection at *both* individual-decision and whole-portfolio level, plus hard
non-overridable risk guardrails.
**Value to you:** where decision quality actually moves — you see your own repeated mistakes, and the
system catches concentration risk no matter how convincing your thesis sounds.
**Maps to:** product doc §8, §16 + Phase 4.

### Scope
- `domain/risk.py` pure rule engine (§4.7): max single-position weight, max sector weight, min cash %,
  max drawdown from peak, max single-transaction %.
- `alerts.md` regenerated on each run; alerts acknowledged, never silently cleared; **no LLM path can
  suppress one**.
- Pipeline **P4 review** -> `reviews/<week>.md` with two mandatory halves:
  - *Individual* — decisions, expectations, outcomes, where I was wrong, recurring patterns.
  - *Portfolio* — weight drift per position/sector/country, concentration + cash change, portfolio
    return and drawdown, alerts raised/acked, **thesis-vs-portfolio conflicts**.
- Usage instrumentation for product doc §16: % transactions with a reason, % with invalidation
  conditions, % theses re-validated after major news.
- Optional `--publish` to write a synthesized page into `~/knowledge-base/Wiki/`.
- `finlink review`, `finlink risk-check`; `skills/review/SKILL.md`, `skills/risk-check/SKILL.md`.

### Exit criteria
- [x] A deliberately over-concentrated portfolio triggers expected alerts; no LLM path can dismiss them.
- [x] Review renders **both** halves; all figures from `domain/` — no model-generated numbers.
- [x] Portfolio half shows measurable weight drift and at least one thesis-vs-portfolio conflict on
      seeded data (valid thesis on an over-limit position).
- [x] Review surfaces >=1 concrete behavioural pattern from seeded history
      (e.g. "3 of 5 sells happened within 2 weeks of purchase").
- [x] §16 metrics computed and printed.

---

## Phase 5 — Portfolio Intelligence
**Goal:** portfolio-level risk analytics across US/HK/Sweden.
**Value to you:** you see construction risk — concentration, correlation, currency exposure — that
per-stock analysis cannot show.
**Maps to:** product doc Phase 4.

### Scope
- Sector/industry/country/**currency** exposure; correlation matrix; growth vs defensive mix;
  portfolio-level volatility; FX exposure summary (USD/HKD/SEK).
- `portfolio_snapshot` equivalent — periodic snapshot files for historical drift.
- Static HTML generation from markdown (optional): charts as self-contained HTML, no server.
- Event-driven alerting (product doc Phase 5) **remains deferred** — no scheduler by design (D6).

### Exit criteria
- [x] Correlation + concentration + currency exposure render for a multi-market portfolio.
- [x] SEK/HKD exposure is explicit, not implicit.
- [x] Optional static HTML renders offline with no server.

---

## Phase 6+ — Deferred (explicitly out of scope)
- Automated broker integration / order execution — **contradicts the product doc's core boundary; do not build.**
- Autonomous multi-agent research (revisit only if deterministic pipelines prove insufficient).
- Backtesting, factor models, portfolio optimisation.
- Web GUI, database, multi-user tenancy, real-time streaming.
- Scheduled/background jobs (D6) — revisit only if the user asks.

---

## Cross-cutting backlog
- Prompt versioning: bump version on change; never edit a template in place.
- `finlink cost-report`; per-run budget caps in `config.yaml`.
- "Not investment advice / not for execution" disclaimer in every generated report.
- `finlink export` — full workspace as JSON/CSV (your data is the asset).
- `finlink doctor` coverage grows with each phase.

---

## Summary table

| Phase | Core question it answers | Builds on | Standalone value |
|---|---|---|---|
| 0 | Are my numbers right? | — | Trustworthy ledger + USD-normalised PnL |
| 1 | Why did I buy this? | 0 | Structured, falsifiable reasoning log |
| 2 | What's happening to what I own? | 0 | Automated research cache + metrics |
| 3 | Is my original reasoning still true? | 1 + 2 | **The differentiating loop** |
| 4 | Where do I keep going wrong — and was my *portfolio* the problem? | 3 | Behavioural feedback + portfolio review + risk rails |
| 5 | What's my construction risk across markets/currencies? | 4 | Multi-market portfolio intelligence |

**Critical path:** 0 -> 1 -> 2 -> 3. Phases 4–5 are additive; **3 is the phase that makes the product
what the product doc promises.**
