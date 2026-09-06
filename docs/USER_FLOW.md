# fin-link — Typical Use Case Flow

> Explains **what you do**, **what the system does underneath**, and **which files/fields change**.
> Companion to `TECHNICAL_DESIGN.md` (structure) and `IMPLEMENTATION_PLAN.md` (build order).
> For setup and commands, see `USAGE.md`.
> Status: draft v1.0

---

## 0. The four layers

Every action moves through the same four layers. Knowing them makes the flows below obvious.

```
1. YOU        conversation: "I bought 20 NVDA at 175 because AI capex keeps rising"
                 |
2. SKILL      .agents/skills/record-trade/SKILL.md  — collects inputs, enforces write-safety rules
                 |
3. CLI        finlink record-trade ...      — one deterministic command, testable, offline-capable
                 |
4. MODULES    domain/  (math, risk — pure, no LLM)
              llm/     (structuring, classification, prose — no math)
              ingest/  (market/news fetch — cache only)
                 |
5. FILES      portfolio/*.md  theses/*.md  reviews/*.md  alerts.md   <- git-tracked, the asset
              data/*.csv|json|jsonl                                  <- cache, gitignored
              logs/llm_runs.jsonl                                    <- audit + cost
```

**One rule explains the whole design:** `domain/` computes every number; the LLM only interprets
them and writes prose. The CLI is the only thing that touches files, and it always
**appends or mutates a named key — never rewrites.**

---

## Flow A — First-time setup (once)

**You:** "Set up fin-link. I hold 50 AAPL, 1000 VOLV-B.ST, 500 0700.HK, and $8,000 cash."

**Runtime:**
1. `git init` in `~/fin-link`; scaffold folders; write `.gitignore` (`data/` excluded).
2. `finlink import-csv` or direct edit writes `portfolio/positions.md` and `portfolio/cash.md`.
3. For each ticker, `finlink onboard <ticker>`:
   - resolves price + currency via the ingest driver
   - **HK and Swedish tickers must resolve or the command fails loudly** (Yahoo coverage gap)
   - appends to `data/prices/<TICKER>.csv`, writes `data/metrics/<TICKER>.json`

**Files:**

| File | Change |
|---|---|
| `portfolio/positions.md` | one row per holding: ticker, qty, avg_cost, currency, opened_at |
| `portfolio/cash.md` | cash balance + currency breakdown |
| `data/prices/*.csv` | cache, gitignored |
| `config/config.yaml` | base_currency `USD`, model IDs, risk rules |

**You finish with:** `finlink show` renders a holdings table with USD-normalised market value and
weights. No LLM has been called yet — Phase 0 is pure math.

---

## Flow B — Recording a trade *(the habit everything depends on)*

**You:** "Bought 20 NVDA at $175 yesterday. I think datacenter capex keeps rising for two years, and
NVIDIA keeps its share."

This is the single most important flow in the product. Notice how much of it is *your* words.

**Runtime:**

1. Skill `record-trade` collects the facts and, critically, asks the question you didn't answer:
   **"What would prove you wrong?"** You reply: "if hyperscaler capex guidance goes negative for two
   quarters."
2. `finlink record-trade` **appends** a row to `portfolio/ledger.md` and updates `positions.md`.
   Currency conversion happens in `domain/pnl.py`, not in the model.
3. Pipeline **P1** sends *only your reason text* to the LLM (via OpenRouter, structured output,
   `require_parameters: true`). It returns a hypothesis tree — no opinion, no price view.
4. A new thesis file is created with `status: draft`.
5. Skill shows you the hypothesis tree. **You edit or approve.** Only then does
   `finlink set-frontmatter --key status --value active` flip it.
6. CLI commits to git.

**Data model mapping:**

| Concept | Where it lands |
|---|---|
| Transaction | `portfolio/ledger.md` row — date, ticker, side, qty, price, fees, currency, **reason**, thesis_slug, fx_rate_at_trade |
| Your reason, verbatim | `## Thesis` section of the thesis file — never paraphrased |
| Thesis | `theses/NVDA-ai-datacenter-capex.md` frontmatter: `status`, `horizon: 2y`, `invalidation_conditions[]`, `confidence` |
| Hypotheses | `hypotheses:` list — `h1` core ("AI datacenter capex keeps growing", observable: hyperscaler capex guidance), `h2`–`h4` sub (GPU demand, >80% share, margins stable) |
| Observable metrics | `h*.observable_metric` — **this is what makes later validation possible** |
| Position | `portfolio/positions.md` row updated, linked by `thesis_slug` |

**Why the LLM is here:** purely to turn a sentence into falsifiable hypotheses. It gives zero
investment insight. You could type the tree by hand and lose nothing but time.

---

## Flow C — Checking in (every week or two, whenever you feel like it)

**You:** "Anything new on my holdings? Are my theses still holding up?"

**Runtime:**

1. Skill `ingest` runs `finlink ingest` — refreshes `data/` for all held tickers. Idempotent;
   uncited items are discarded; `data/` is a cache so nothing precious is at risk.
2. Skill `validate` runs `finlink validate`, which per thesis:
   - **pre-filters** evidence deterministically (same ticker, published after thesis creation,
     token overlap with `observable_metric`) — free, cuts LLM volume >90%
   - **Pass A** -> supporting evidence
   - **Pass B** -> contrary evidence, **run independently with Pass A's output withheld**
   - `domain/portfolio.py` computes portfolio context (weight, sector weight, breaches, cash %)
   - synthesises verdict + confidence + one `position_note` interpreting the computed numbers
3. **Appends** one `## Validation — <date>` section. Key-scoped frontmatter status update. Git commit.
4. Risk engine runs; `alerts.md` regenerated.

**Data model mapping:**

| Concept | Where it lands |
|---|---|
| Evidence | `### Supporting` / `### Contrary` bullets, each with claim + source URL + date + strength |
| Portfolio context | `### Portfolio context` line — written by `domain/`, never by the model |
| Verdict | `### Verdict` — `still_valid \| partially_valid \| challenged \| invalidated \| undetermined`, `confidence: low\|medium\|high` |
| Uncertainty | `### Uncertainty` — required; what could *not* be determined |
| Status change | frontmatter `status`, `hypotheses[].status` — via `set-frontmatter`, named key only |
| History | `git log -p theses/NVDA-...md` — replaces a database history table |
| Risk alerts | `alerts.md` |

**What you see:** something like —

> **NVDA** — still_valid, confidence medium
> Supporting: hyperscaler capex guidance raised (source, date)
> Contrary: a major cloud customer announced in-house silicon (source, date) — strength strong
> Portfolio context: 22.3% weight, Info Tech 41.0%, limit 15% **BREACHED**, cash 4.1%
> Note: thesis still valid, but this name is already above your limit — validity is not a reason to add.
> Uncertainty: no data yet on Q4 merchant GPU share.

Contrary evidence is **never optional** — a `still_valid` verdict with an empty contrary section is
rejected at the schema level. That is the anti-confirmation-bias rule, enforced in code.

---

## Flow D — Weekly review

**You:** "Give me this week's review."

**Runtime:** `finlink review` reads the ledger, theses, validation sections, and portfolio snapshots
over the window. `domain/` computes **every** number first. The LLM receives those numbers and writes
only the narrative.

**Output — `reviews/2026-W36.md`, two mandatory halves:**

| Half | Contents | Data source |
|---|---|---|
| Individual | decisions made, expectations, outcomes, where I was wrong, recurring patterns | `ledger.md`, thesis verdicts, `## Thesis` vs outcome |
| Portfolio | weight drift per position/sector/country (start vs end), concentration + cash change, portfolio return and drawdown, alerts raised/acked, **thesis-vs-portfolio conflicts** | `positions.md`, `portfolio_snapshot`, `alerts.md` |

The "thesis-vs-portfolio conflict" is the payoff of §4.4: it surfaces cases where your reasoning held
but your exposure was already too large — the thing per-stock analysis structurally cannot see.

Optionally `--publish` writes a synthesized page into `~/knowledge-base/Wiki/`.

---

## Flow E — Risk check (any time)

**You:** "Am I too concentrated?"

**Runtime:** `finlink risk-check` — `domain/risk.py` evaluates rules from `config.yaml`
(max single-position weight, max sector weight, min cash %, max drawdown, max single-trade %).
**No LLM involved.** Breaches are written to `alerts.md`; you acknowledge them, they never silently
clear. No code path lets a model dismiss an alert.

---

## The loop, end to end

```
buy with a reason  ->  thesis + hypotheses + observable metrics   (Flow B)
        |                                    ^
        v                                    |
   hold position  --->  new evidence  --->  validate: supporting AND contrary  (Flow C)
        |                                    |
        v                                    v
   portfolio context (weight/limit)      thesis status changes
        \                                    /
         --------->  review: individual + portfolio  (Flow D)  --------> better next decision
```

**What accumulates and why it matters:** after a year you have a few dozen thesis files, each with
dated supporting and contrary evidence, verdicts, and your own original words. That corpus — not the
market data, which anyone can buy — is the asset. It lets the review answer questions no broker
statement can: *do I consistently sell winners early? do I ignore contrary evidence on names I like?
do I keep re-buying the same sector?*

**The one thing to watch:** everything durable lives in plain markdown under git. If a pipeline ever
mangles a file, `git diff` shows it and `git checkout` undoes it. That is why append-only writes and
one commit per run are non-negotiable.
