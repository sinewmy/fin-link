# fin-link — Technical Design Document

> Companion to `docs/AI股票投资辅助应用_V2_架构与产品设计文档.md` (the product doc).
> That doc defines **what and why**. This doc defines **how**.
> Status: draft v2.0 — **markdown-first, no database, no web GUI, manual-only**
> Audience: an implementation agent (assume no prior context).

---

## 0. Architecture in one paragraph

fin-link is a **git-backed markdown workspace** for investment decisions, driven by a small Python
CLI and exposed to the user through **Codex skills**. There is no database and no web application.
Portfolio holdings, transactions, and investment theses are markdown files with strict YAML
frontmatter, committed to git. Ingested market data is a regenerable cache kept out of git. Every
LLM interaction is a deterministic CLI pipeline with Pydantic-validated structured output, invoked
manually by the user or by a skill. The skills are the user interface.

---

## 1. Locked decisions

Not specified in the product doc. Defaults below; each is reversible, but reversing costs rework.

| # | Decision | Choice | Rationale |
|---|----------|--------|-----------|
| D1 | Deployment | Single-user. No auth, no multi-tenancy. | Product doc is written in first person ("AI 帮助我"). |
| D2 | Markets | **US + Hong Kong (`.HK`) + Sweden (`.ST`)** | User's portfolio. See §2.3 for coverage caveats. |
| D3 | Base currency | **USD**. HKD treated as pegged (7.8); SEK converted via live FX. | User decision. Every PnL figure is USD-normalised. |
| D4 | Storage | **Markdown + YAML frontmatter in git.** No database. | See §1.1. |
| D5 | UI | **Codex skills** (`SKILL.md`) + markdown reports. No web GUI. | The agent conversation *is* the interface. |
| D6 | Scheduling | **None.** All pipelines are manual / CLI-invocable. | User decision (Q3). |
| D7 | Language/runtime | **Python 3.12**, `uv` | Finance/data ecosystem (pandas, numpy, httpx). |
| D8 | LLM access | **OpenAI Python SDK pointed at OpenRouter.** `require_parameters: true` mandatory. | Best structured-output support. See §4.2. |
| D9 | Model selection | **User-specified.** Config file maps each pipeline to a model ID. | User decision (Q2). |
| D10 | "Agents" | **Deterministic pipelines, not autonomous agents.** | Product doc deprioritises autonomy; deterministic code is debuggable. |

### 1.1 Why markdown + git instead of a database (and what it costs)

Honest trade-off — this is a real architectural bet, not just a simplification.

**Gained:**

- **The LLM is the query engine.** At 20–30 theses the corpus is ~10k tokens; the model reads the
  whole folder. "What's challenged?" is not a SQL query, it's reading 20 files. The relational
  schema (§3) existed to serve joins the model can do natively.
- **Git is a better audit trail than a history table.** "Why did this thesis change?" is `git log -p`.
- **Phase 0 (docker, Postgres, Alembic, migrations) is largely deleted.**
- **It attacks the biggest risk in the product doc (§15.5 — will you actually use it?).** Less
  infrastructure between you and the habit is the whole point.

**Given up — and how each is mitigated:**

| Risk | Mitigation (mandatory) |
|---|---|
| No referential integrity; links can dangle | `finlink doctor --check-links` |
| Schema drift (`status: challenged` vs `CHALLENGED`) | Pydantic frontmatter model; `doctor` **fails loud** |
| **An agent rewrite can silently destroy hand-written evidence** | **Append-only writes (§4.1)** + **git commit after every run (§4.1)** |
| No interactive charts | Markdown tables; static HTML can be generated later from the same files |
| Fast structured queries at scale | Degrades around 100+ theses; md is a clean migration source if ever needed |

**Two non-negotiable disciplines:**

1. **Agents append; they never rewrite.** A validation is appended as a new
   `## Validation — YYYY-MM-DD` section. Frontmatter status changes go through a script that mutates
   *named keys only*. The phrase "here is the updated file" is forbidden in any skill.
2. **Atomic write + git commit after every agent run.** Write temp -> rename -> commit. Any damage is
   one `git diff` from being undone.

---

## 2. Repository layout

```
fin-link/
  portfolio/
    positions.md            # current holdings (hand-editable markdown table)
    ledger.md               # transactions, append-only
    cash.md                 # cash balance + currency breakdown
  theses/
    <TICKER>-<slug>.md      # one file per thesis: frontmatter + hypotheses + append-only validations
  reviews/
    YYYY-Www.md             # generated weekly review (two halves, §5 P4)
  alerts.md                 # current risk alerts (regenerated, hand-acknowledgeable)
  config/
    config.yaml             # base_currency, models per pipeline, risk rules, limits
  data/                     # GITIGNORED — regenerable cache, never a source of truth
    prices/<TICKER>.csv     # append-only OHLCV
    metrics/<TICKER>.json   # fundamentals snapshot
    news/<TICKER>.jsonl     # append-only, deduped by URL
    fx.json                 # latest FX rates + as_of
  logs/
    llm_runs.jsonl          # every LLM call: model, tokens, cost, prompt version
    ingest_runs.jsonl
  skills/
    record-trade/SKILL.md
    validate/SKILL.md
    review/SKILL.md
    risk-check/SKILL.md
    ingest/SKILL.md
  finlink/                  # Python package (CLI + pipelines)
  tests/
```

**The `data/` gitignore rule is a principle:** git tracks *decisions and reasoning* — the
irreplaceable asset. `data/` is a cache; losing it costs one re-run. Losing `theses/` costs years.

**Not inside `~/knowledge-base`.** Machine-generated content updating frequently would pollute a
curated vault. A separate `~/fin-link` repo keeps decisions durable, and an optional **publish step**
(P4) copies *synthesized* review pages into `~/knowledge-base/Wiki/` so the reasoning still compounds
into the KB without the churn.

### 2.3 Market coverage and currency

- **US** — no suffix. Reliable.
- **Sweden** — `.ST` (Nasdaq OMX Stockholm, e.g. `VOLV-B.ST`). Reliable.
- **Hong Kong** — `.HK`. **Caveat:** Yahoo coverage for HK (and A-shares) is incomplete; large caps
  are fine, smaller names can return nulls. **Every HK ticker must pass a verification check at
  onboarding** (`finlink onboard <ticker>` fails loudly if price or currency is null).

**Currency handling:** all money is stored in source currency with `currency` on every record; every
PnL, weight, and valuation is **USD-normalised** at calculation time using `data/fx.json` (HKD pegged
at 7.8, SEK live). `Decimal` throughout — never float.

---

## 3. Data model (markdown + frontmatter)

The spine from the product doc is unchanged; it is expressed as files rather than tables:

```
ledger.md (Transaction) -> theses/<t>.md (Thesis) -> Hypothesis (frontmatter tree)
      -> Evidence (in appended validation sections) -> Validation (append-only section)
```

### 3.1 `portfolio/positions.md`

Markdown table, hand-editable. `finlink` reads it; it never rewrites it wholesale.

| column | type | notes |
|---|---|---|
| ticker | str | `AAPL`, `0700.HK`, `VOLV-B.ST` |
| quantity | Decimal | |
| avg_cost | Decimal | source currency |
| currency | str | USD / HKD / SEK |
| opened_at | date | |
| thesis_slug | str \| null | links to `theses/` |
| notes | str | |

### 3.2 `portfolio/ledger.md`

Append-only markdown table: date, ticker, side, quantity, price, fees, currency, **reason**,
thesis_slug, fx_rate_usd_at_trade.

### 3.3 `theses/<TICKER>-<slug>.md`

YAML frontmatter (Pydantic-validated, `ThesisFrontmatter`) + markdown body.

```yaml
---
ticker: NVDA
slug: ai-datacenter-capex
status: active            # draft | active | challenged | invalidated | closed
created: 2026-09-06
horizon: 2y
base_currency: USD
invalidation_conditions:
  - "Hyperscaler capex guidance turns negative for two consecutive quarters"
  - "NVDA data-centre revenue growth < 20% YoY"
confidence: medium        # low | medium | high — NEVER a probability
hypotheses:
  - id: h1
    kind: core
    statement: "AI datacenter capex keeps growing"
    observable_metric: "Hyperscaler capex guidance"
    status: pending       # pending | supported | challenged | invalidated | expired
  - id: h2
    kind: sub
    parent: h1
    statement: "NVDA retains >80% merchant GPU share"
    observable_metric: "NVDA DC revenue vs competitors"
    status: pending
---
```

Body = append-only sections, oldest first:

```
## Thesis
<original user reason, verbatim>

## Validation — 2026-09-06
### Supporting
- <claim> — [source](url) (2026-09-01) strength=moderate
### Contrary
- <claim> — [source](url) (2026-09-02) strength=strong
### Portfolio context
weight 22.3% | sector(Info Tech) 41.0% | limit 15% BREACHED | cash 4.1%
### Verdict
still_valid — confidence medium
### Uncertainty
<what could not be determined>
```

### 3.4 Enums (Python `str` enums; validated on every read)

```python
ThesisStatus      = DRAFT | ACTIVE | CHALLENGED | INVALIDATED | CLOSED
HypothesisStatus  = PENDING | SUPPORTED | CHALLENGED | INVALIDATED | EXPIRED
EvidenceDirection = SUPPORTING | CONTRARY | NEUTRAL
EvidenceStrength  = WEAK | MODERATE | STRONG
Confidence        = LOW | MEDIUM | HIGH          # ordinal, NOT a probability
Verdict           = STILL_VALID | PARTIALLY_VALID | CHALLENGED | INVALIDATED | UNDETERMINED
```

### 3.5 `config/config.yaml`

Base currency, per-pipeline model IDs, risk rules, budget caps, FX settings. **No model IDs are
hardcoded in source** — the user supplies them (D9).

---

## 4. Key technical solutions

### 4.1 Safe writes to markdown (the highest-risk area)

An agent rewriting a whole file can silently delete hand-written evidence. This is the single
biggest operational risk in a markdown-first design, so it is constrained in code:

- **Append-only for validations.** `finlink validate` appends one `## Validation — <date>` section
  via atomic append. It never rewrites the body.
- **Key-scoped frontmatter mutation.** Status/confidence updates go through
  `finlink set-frontmatter <file> --key status --value challenged`, which parses YAML, mutates the
  *named key only*, and re-serialises. Whole-file regeneration is forbidden.
- **Atomic write + git commit.** temp file -> `os.replace` -> `git commit` with a message naming the
  pipeline. Every run is revertible.
- **`finlink doctor`.** Validates every file against Pydantic models, checks links resolve, detects
  enum drift, and **fails loudly**. Run it in CI and before/after every pipeline.

### 4.2 LLM access via OpenRouter

OpenAI Python SDK against the OpenRouter base URL:

```python
client = OpenAI(base_url="https://openrouter.ai/api/v1", api_key=cfg.openrouter_key)
client.chat.completions.create(
    model=cfg.model_for(pipeline),
    messages=[...],
    response_format={
        "type": "json_schema",
        "json_schema": {"name": "thesis", "strict": True,
                        "schema": ThesisDecomposition.model_json_schema()},
    },
    extra_body={"provider": {"require_parameters": True}},
)
```

- **`require_parameters: true` is mandatory on every call.** Without it OpenRouter routes by
  price/uptime and may select an endpoint that **silently ignores the schema** — the number one cause
  of "structured outputs don't work on OpenRouter".
- Enforcement still varies by upstream provider, so the retry path is retained: on schema-validation
  failure, retry once with the validation error appended, then fail the run loudly. **Never** accept
  unvalidated JSON.
- Every call is appended to `logs/llm_runs.jsonl`: pipeline, model, prompt version/hash, tokens,
  cost, latency, status. `finlink cost-report` summarises spend.
- Per-pipeline model mappings in `config.yaml` — a cheap model is fine for report prose, a strong one
  for thesis decomposition and validation.

### 4.3 Quant vs LLM separation (hard boundary)

| Computed by `finlink/domain/` (code) | Produced by the LLM |
|---|---|
| Price, returns, volatility, drawdown | News summarisation |
| PE/PS/PB, growth, margins, debt, FCF | Filing/earnings narrative interpretation |
| Position weight, sector/country concentration, cash % | Thesis -> hypothesis decomposition |
| Cost basis, realised/unrealised PnL, FX conversion | Evidence -> supporting/contrary classification |
| **All risk-rule evaluation** | Review-report prose |

Consequence: **no LLM output may produce a number that appears in a report.** Numbers are computed
first, passed *to* the model as context, and the model may only interpret them.

### 4.4 Portfolio context on every validation

Product doc §6.2 requires `Individual Stock Thesis + Portfolio Context` together.

- `domain/portfolio.py` computes, per validated asset: `weight_pct`, `sector_weight_pct`,
  `country_weight_pct`, `rule_breaches[]`, `cash_pct`, `holding_count`.
- Written into the validation's `### Portfolio context` line by **code**, never by the model.
- The model returns exactly one `position_note` string interpreting those numbers, e.g.:
  > "Thesis still valid, but this name is 22% of the portfolio against a 15% limit. Validity is not a reason to add."
- **Hard rule:** the model may not restate, recompute, or contradict the supplied numbers. Any
  numeric figure in `position_note` absent from the computed context fails validation.

### 4.5 Anti-confirmation-bias enforcement

The product doc's most important behavioural rule (§15.1), enforced structurally:

- `CONTRARY` is a first-class value in `EvidenceDirection`.
- Validation runs **two independent passes**: Pass A (supporting) and Pass B (contrary), with Pass A's
  output **withheld** from Pass B.
- A validation with verdict `still_valid` and an empty contrary section is **rejected at the schema
  level** — you cannot claim a thesis holds without having looked for the counter-case.

### 4.6 No false precision

- `confidence` is a 3-value ordinal enum. No floats, no probabilities, no scores out of 100.
- Every validation carries an `### Uncertainty` section (required) stating what could *not* be
  determined.
- Reports present evidence and observables, never a bull/bear probability. (Product doc §15.4.)

### 4.7 Risk engine (deterministic, non-overridable)

- `domain/risk.py` is a pure function: `(portfolio_state, rules) -> list[Alert]`. Never calls an LLM.
- Rules in `config.yaml`: max single-position weight, max sector weight, min cash %, max drawdown
  from peak, max single-transaction % of portfolio.
- Output is regenerated into `alerts.md`. Alerts are **acknowledged**, never silently cleared.
- **No code path lets a model suppress, downgrade, or close an alert.**

### 4.8 Idempotent, provenance-first ingestion

- Every item requires `source_url` + `published_at`; uncited items are **discarded, not stored**.
- Dedupe by URL against `data/news/*.jsonl`. Re-running is safe.
- Market data via Alpha Vantage; news via RSS/feeds. All normalised into `data/`.

---

## 5. Pipelines (deterministic, manual invocation)

No scheduler (D6). Each pipeline is a CLI command; skills wrap them.

### P1 — Thesis decomposition

**Trigger:** `finlink record-trade` (or skill `/record-trade`).
**Input:** ticker, side, quantity, price, **reason text**, horizon.
**Output:** appends to `ledger.md`; creates `theses/<ticker>-<slug>.md` with `status: draft`.
**Gate:** the user must confirm/edit before it becomes `active`. The model never silently overwrites
the user's own reasoning — the reason text is stored verbatim in `## Thesis`.

*Purpose of the LLM here:* purely to structure the reason into falsifiable hypotheses with observable
metrics. It delivers **no investment insight**. It exists because (a) P3 cannot match evidence against
an unstructured sentence, and (b) it lowers the friction that product doc §15.5 identifies as the
make-or-break risk. A form-only path remains possible without any other change.

### P2 — Ingestion

**Trigger:** `finlink ingest [TICKER...]`.
**Steps:** fetch prices/fundamentals/news -> normalise -> dedupe -> append to `data/` -> log run.
**Guarantee:** idempotent. `data/` is a cache; deleting it is safe.

### P3 — Validation *(the core value loop)*

**Trigger:** `finlink validate [--thesis SLUG]`.
**Steps:**
1. Load active theses + hypotheses.
2. Deterministic pre-filter: same ticker, published after thesis creation, token overlap with
   `observable_metric`.
3. **Pass A — supporting** -> evidence lines.
4. **Pass B — contrary**, independent, Pass A withheld -> evidence lines.
5. Compute **portfolio context** (§4.4) in `domain/`.
6. Synthesise verdict, confidence, `position_note`, uncertainty.
7. **Append** one `## Validation — <date>` section; key-scoped frontmatter update; git commit.
8. Regenerate `alerts.md` via the risk engine.

### P4 — Review

**Trigger:** `finlink review [--week YYYY-Www]`.
**Output:** `reviews/<week>.md` with **two mandatory halves**:
1. **Individual** — what I decided, what I expected, what happened, where I was wrong, recurring patterns.
2. **Portfolio** — weight drift per position/sector/country (start vs end), concentration and cash
   change, portfolio return and drawdown, alerts raised/acked, and **thesis-vs-portfolio conflicts**
   (still-valid theses sitting on names that breach a limit).

Optional publish step copies a synthesized version into `~/knowledge-base/Wiki/`.

### P5 — Event-driven alerting *(deferred)*

No scheduler exists (D6), so proactive alerting is out of scope until the user wants automated runs.

---

## 6. Skills as the user interface

Five skills in `skills/`, discovered at project level via `.agents/skills/` (never installed globally).
Each is a thin wrapper over one CLI command
and carries the write-safety rules in its own instructions.

| Skill | Wraps | Purpose |
|---|---|---|
| `record-trade` | `finlink record-trade` | Log a trade + draft a thesis |
| `validate` | `finlink validate` | Re-test theses against new evidence |
| `review` | `finlink review` | Weekly two-half report |
| `risk-check` | `finlink risk-check` | Evaluate rules, regenerate `alerts.md` |
| `ingest` | `finlink ingest` | Refresh market/news cache |

Every `SKILL.md` must state: **append only, never rewrite; run `finlink doctor` before and after;
commit after completion; never output a financial number the CLI did not produce.**

---

## 7. Engineering standards (non-negotiable)

1. **Append, never rewrite.** Agents append sections and mutate named frontmatter keys only.
2. **Provenance or nothing.** No claim without a source URL and published date.
3. **No numbers from the model.** (§4.3)
4. **No confidence without uncertainty.** (§4.6)
5. **Atomic writes + git commit** after every mutating run. (§4.1)
6. **Doctor passes in CI.** `finlink doctor` must be green; it fails loud, never auto-repairs silently.
7. **Money is `Decimal`.** Never float. All reporting in USD.
8. **Schema-first LLM I/O.** Pydantic models are the contract; prompts are versioned files, never
   inline strings.
9. **`require_parameters: true`** on every OpenRouter call. (§4.2)
10. **Every pipeline runs offline** with an `echo` driver at zero API cost.

---

## 8. Open questions

| # | Question | Default |
|---|---|---|
| Q1 | Model IDs per pipeline (extraction vs reporting) | User to specify; config-driven (D9) |
| Q2 | Does any HK holding fall outside Yahoo coverage? | `finlink onboard` verifies per ticker |
| Q3 | Publish synthesized reviews into `~/knowledge-base/Wiki/`? | Off by default; opt-in flag |
| Q4 | Broker CSV import format? | Manual entry + generic CSV adapter |

---

## 9. Traceability to the product doc

| Product doc requirement | Implemented by |
|---|---|
| §2.2 Core loop | P1 -> P3 -> P4; §3 spine |
| §3.1 Portfolio & cash | `portfolio/positions.md`, `cash.md`, `domain/pnl.py` |
| §4.1–4.2 Transaction + thesis | `ledger.md` reason column, P1, `observable_metric` |
| §5 Validation + contrary evidence | P3 passes A/B, §4.5 |
| §6 Portfolio view | `domain/portfolio.py`, P4 portfolio half |
| §6.2 Thesis + portfolio context | §4.4, P3 step 5, P4 conflicts |
| §7.1/7.2 Quant vs LLM | §4.3 |
| §8 Risk engine | `domain/risk.py`, §4.7 |
| §9 Data ingestion | §4.8, `data/` cache |
| §10 Storage | §2 layout, §3 file schemas — **markdown instead of PostgreSQL; deliberate deviation** |
| §15.1 Confirmation bias | §4.5 |
| §15.2 Hallucination | §4.8 provenance, §4.2 validation |
| §15.3 Data quality | `data/` is a cache; "not for execution" disclaimer on every report |
| §15.4 False precision | §4.6 |
| §15.5 Will you actually use it | §1.1 — the core justification for this architecture |
| §16 Success criteria | P4 content + usage instrumentation |

**Deliberate deviations from the product doc, flagged for the user:**

1. **No PostgreSQL** (§10) — replaced by markdown + git (§1.1).
2. **No "Simple Web UI"** (Phase 1) — replaced by Codex skills (§6). Serves §15.5, the stated top risk.
3. **P5 event-driven alerting deferred** — no scheduler by user decision (D6).
