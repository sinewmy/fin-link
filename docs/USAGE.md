# fin-link — Setup & Usage

> How to install, configure, and use what's built **today** (Phases 0 + 1).
>
> | Doc | What it covers |
> |---|---|
> | **`USAGE.md`** (this file) | Setup and daily use — start here |
| `USAGE.md` §2b | **The conversational entry point (skills)** — how you actually talk to fin-link |
> | `USER_FLOW.md` | Typical workflows, and what the system does under the hood |
> | `TECHNICAL_DESIGN.md` | Architecture, data model, pipelines, standards |
> | `IMPLEMENTATION_PLAN.md` | Phased roadmap and exit criteria |
> | `STATUS.md` | What's built, bugs found, known gaps |
> | `AI股票投资辅助应用_V2_架构与产品设计文档.md` | The original product vision |

---

## 1. Install

Requirements: Python 3.12+, `uv`.

```bash
cd ~/codex-projects/fin-link
uv sync                      # installs into .venv
uv run finlink --help        # or: .venv/bin/finlink --help
```

If `uv` can't write to its cache dir (some sandboxes), prefix with
`UV_CACHE_DIR=/tmp/uv-cache`.

---

## 2. Create a workspace

A workspace is a git repo of markdown files. This is where your data lives.

```bash
finlink init <dir>
cd <dir>
git init                     # recommended — makes every run revertible
```

To back it up to a remote:

```bash
git remote add origin git@github.com:<you>/fin-link.git
git push -u origin main
```

`finlink init` **appends** `data/` and `logs/` to an existing `.gitignore` rather than
overwriting it, so a project that already has one keeps its rules.

This creates:

```
portfolio/positions.md   current holdings — derived from ledger.md after seed-ledger
portfolio/ledger.md      transactions, append-only — SOURCE OF TRUTH
portfolio/cash.md        cash by currency
theses/                  one file per investment thesis
reviews/                 generated reviews
config/config.yaml       settings
data/                    GITIGNORED cache (prices, news) — safe to delete
logs/                    GITIGNORED LLM run log
```

**Git is not optional in spirit.** Every mutating command commits, so any mistake is
one `git diff` from being undone.

If git is unavailable or read-only (some sandboxes), finlink still writes your data and
prints a warning: `data was written but is NOT committed`. It never crashes mid-write.

---

## 2b. The conversational entry point (skills)

This is the **primary** way to use fin-link. Sections 3–8 are the raw CLI reference — useful when you
want precision, but in day-to-day use you just talk.

### How to start the conversation

1. Open a terminal **in the fin-link project directory**:

   ```bash
   cd ~/codex-projects/fin-link
   codex
   ```

   Yes — in the Codex CLI window. The project directory matters: that is how Codex finds the skills
   and how `finlink` finds your workspace.

2. Then either **type a slash command**, or **just describe what you did in plain English**. Both
   work; they take the same path.

   | You type | What happens |
   |---|---|
   | `/record-trade` | Codex loads the `record-trade` skill and walks you through it |
   | `/ingest` | Refreshes prices/fundamentals/news for held tickers |
   | `/validate` | Re-tests your theses against new evidence |
   | `/review` | Generates the weekly two-half review |
   | `/risk-check` | Evaluates concentration/cash/drawdown rules |
   | *"I bought 20 NVDA at 175 because AI datacenter capex keeps rising"* | Codex matches this to `record-trade` by the skill's description and runs the same flow |

You do **not** need to memorise CLI flags. The skill's job is to collect the inputs (including the
"what would prove you wrong?" question you'd otherwise skip) and then call the CLI for you.

### Why project-level skills

The five skills live in **this repository** at `.agents/skills/<name>/SKILL.md` — they are **not**
installed globally. That means:

- they are version-controlled with the project, so they evolve with the code;
- they only exist when you're working in fin-link, so they never pollute other projects;
- cloning the repo on another machine gives you the same workflow.

> Note: the canonical copy is committed in `skills/<name>/SKILL.md`; `.agents/skills/` is the
> discovery copy Codex reads. If you edit a skill, edit **both** or re-copy. **Codex must be
> restarted** to pick up skill changes.

### What each skill guarantees

Every skill carries the same hard rules in its own instructions:

- **Append only, never rewrite.** Status changes go through `finlink set-frontmatter --key --value`.
- **Runs `finlink doctor` before and after** — so a bad write is caught immediately.
- **Never invents a financial number.** All figures come from `finlink/domain/`; the model only
  writes prose.
- **One git commit per run**, so any mistake is one `git diff` from being undone.

### Skill -> CLI -> files

| Skill | Calls | Reads / writes |
|---|---|---|
| `record-trade` | `finlink record-trade`, `finlink set-frontmatter` | appends `portfolio/ledger.md`, **re-derives `positions.md` and debits/credits `cash.md`**, creates `theses/<TICKER>-<slug>.md` |
| `ingest` | `finlink ingest`, `finlink onboard` | writes cache only: `data/prices/`, `data/metrics/`, `data/news/` (gitignored) |
| `validate` | `finlink validate` | appends `## Validation — <date>` to each thesis; may update `status` |
| `risk-check` | `finlink risk-check` | regenerates `alerts.md` |
| `review` | `finlink review` | writes `reviews/<week>.md` |

If a skill ever fails or hangs, fall back to the CLI directly — sections 3–8 document every command.

---

## 3. Configure

Edit `config/config.yaml`:

```yaml
base_currency: USD
hkd_peg: '7.8'              # HKD is pegged to USD
fx:
  SEK: '0.095'              # 1 SEK = 0.095 USD — refresh occasionally
models:
  P1_decompose: openai/gpt-4o-mini      # <-- fill this in
openrouter_api_key: sk-or-...           # <-- or export OPENROUTER_API_KEY_CODEX
risk_rules: []
```

Two ways to supply the key — the env var is cleaner since `config.yaml` is committed:

```bash
export OPENROUTER_API_KEY_CODEX=sk-or-...
```

Resolution order: `openrouter_api_key` in `config.yaml` first, then
`OPENROUTER_API_KEY_CODEX`, then plain `OPENROUTER_API_KEY` as a fallback so an
existing shell profile keeps working. Never commit the key.

### Ticker formats

| Market | Suffix | Example |
|---|---|---|
| US | none | `NVDA`, `AAPL` |
| Sweden | `.ST` | `VOLV-B.ST` |
| Hong Kong | `.HK` | `0700.HK` |

Currency is inferred per holding and converted to USD automatically. HKD uses the peg;
SEK uses the configured rate. **A missing SEK rate raises an error rather than silently
treating SEK as USD.**

---

## 4. Record holdings

Two options, or both:

**a) Direct edit** — `portfolio/positions.md` is a plain markdown table you can edit by hand:

```
| ticker | quantity | avg_cost | currency | opened_at | thesis_slug | notes |
| --- | --- | --- | --- | --- | --- | --- |
| AAPL | 50 | 180.00 | USD | 2025-03-01 | - | - |
| VOLV-B.ST | 1000 | 250.00 | SEK | 2025-06-01 | - | - |
```

**b) Record trades** — appends to the ledger and (for buys) drafts a thesis:

```bash
finlink record-trade \
  --ticker NVDA --side buy --quantity 20 --price 175 \
  --reason "I think datacenter capex keeps rising for two years, and NVIDIA keeps its share" \
  --horizon 2y \
  --invalid-if "hyperscaler capex guidance goes negative for two quarters"
```

The `--reason` is the most important field in the whole system — it's what gets
structured into hypotheses and later re-tested.

**Test it without spending money** by adding `--driver echo`. This uses deterministic
fixtures instead of an API call. Output is mechanical, but it proves the plumbing works:

```bash
finlink record-trade --ticker NVDA --side buy --quantity 20 --price 175 \
  --reason "..." --driver echo
```

---

## 5. Confirm a thesis

The model drafts a thesis as `status: draft`. **It never auto-activates** — you review
the hypothesis tree, then approve:

```bash
finlink confirm theses/NVDA-<slug>.md      # draft -> active
```

Edit the file first if the decomposition is wrong; it's just markdown. Confirming twice
is rejected.

---

## 6. Fetch market data

```bash
finlink ingest                    # all held tickers
finlink ingest AAPL NVDA          # specific tickers
finlink ingest --driver mock      # offline, deterministic test data
finlink onboard VOLV-B.ST 0700.HK # verify a ticker resolves before trusting it
finlink quote AAPL                # price + volatility, drawdown, fundamentals
finlink news NVDA
finlink fx-update                 # refresh SEK; HKD stays on its peg
```

Ingestion is **idempotent** — re-running adds nothing new. `data/` is a disposable
cache; deleting it and re-running `ingest` restores it.

`onboard` fails loudly on coverage gaps rather than storing nulls. The default
market driver is Tencent (free, keyless, HK + US); use `--driver alphavantage`
only if you need fundamentals (P/E, market cap) and quota is available on this
IP. Onboard HK tickers first to confirm coverage.

## 7. Inspect

```bash
finlink doctor        # validate every file against the schema; exit 1 on any error
finlink show          # portfolio: weights, cost basis, unrealised PnL (USD)
finlink cost-report   # LLM runs, tokens, errors by model
finlink fx-set SEK 0.095
```

`doctor` is the safety net. Run it whenever you hand-edit files. It catches enum drift
(`CHALLENGED` vs `challenged`), unknown currencies, dangling thesis links, duplicate
hypothesis IDs — and **fails loudly rather than auto-repairing**.

---

## 8. Command reference

| Command | What it does | Writes? |
|---|---|---|
| `finlink init <dir>` | Create a workspace | yes |
| `finlink check [--fix-separator]` | Validate hand-edited portfolio files | only with --fix-separator |
| `finlink backfill-theses [--dry-run]` | Draft one thesis per held position | yes (commits) |
| `finlink improve-slugs [--dry-run]` | LLM-proposed readable thesis names | yes (renames) |
| `finlink record-trade` | Append trade + draft thesis | yes (commits) |
| `finlink confirm <file>` | draft -> active | yes (commits) |
| `finlink set-frontmatter <file> --key K --value V` | Change ONE frontmatter key | yes (commits) |
| `finlink show` | Portfolio table | no |
| `finlink ingest [TICKERS]` | Fetch prices/fundamentals/news into cache | cache only |
| `finlink onboard <TICKER>` | Verify ticker coverage | cache only |
| `finlink validate [--thesis SLUG]` | Re-test theses: supporting + contrary + verdict | yes (appends) |
| `finlink risk-check` | Evaluate risk rules, regenerate `alerts.md` | yes |
| `finlink alerts [--ack RULE:SCOPE]` | Show / acknowledge the alert ledger | yes (on ack) |
| `finlink review [--week YYYY-Www]` | Two-half weekly review | yes |
| `finlink exposure` | Sector/country/currency exposure + concentration | no |
| `finlink snapshot` | Record today's portfolio snapshot | yes (cache) |
| `finlink report [--with-correlation]` | Self-contained HTML with charts | yes |
| `finlink status` | One row per thesis | no |
| `finlink quote <TICKER>` | Cached price + metrics | no |
| `finlink news <TICKER>` | Cached news | no |
| `finlink fx-update` | Refresh FX rates | yes |
| `finlink doctor` | Validate everything | no |
| `finlink cost-report` | LLM spend summary | no |
| `finlink fx-set <CCY> <rate>` | Set FX rate | yes |

Add `--no-commit` to any writing command to skip the git commit.

---

## 9. What's NOT built yet

| Missing | Phase |
|---|---|
| Real news source (RSS driver wired; coverage varies by ticker) | 2+ |
| Automatic sector classification (needs a fundamentals mapping) | 5+ |

---

## 10. Safety rules (enforced in code)

These are the guarantees that make a markdown system trustworthy:

1. **Agents append, never rewrite.** Validations append sections; status changes mutate
   one named key. Your hand-written text is never regenerated.
2. **Atomic write + one git commit per run.** Any damage is one `git diff` from undone.
3. **Your reason is stored verbatim.** The model never paraphrases it.
4. **No numbers from the model.** All figures come from `finlink/domain/`; the model
   only interprets them.
5. **Confidence is `low\|medium\|high`** — never a probability.
6. **Every LLM call is logged** to `logs/llm_runs.jsonl` with tokens, cost, and prompt version.
7. **Malformed model output retries once, then fails loudly.** Never stored as garbage.

---

## 10b. Validating a thesis (`finlink validate`)

Re-test an active thesis against new evidence. Two independent LLM passes — one looking
for supporting evidence, one adversarial — plus portfolio context computed in code.

```bash
finlink validate                      # all active theses
finlink validate --thesis <slug>      # one
finlink validate --driver echo        # offline, zero cost
finlink validate --dry-run            # compute and print, write nothing
finlink status                        # one row per thesis
```

Each run appends a `## Validation — <date>` section containing, in order:

| Section | Written by |
|---|---|
| `### Supporting` | Pass A |
| `### Contrary` | Pass B (independent — Pass A's output is withheld) |
| `### Portfolio context` | `domain/` — never the model |
| `### Verdict` | Synthesis |
| `### Uncertainty` | Synthesis (required) |

Rules enforced in code, not by convention:

- **A thesis cannot be declared `still_valid` on an empty contrary section.** With no
  counter-evidence the verdict is `undetermined` — "not yet contradicted" is not "confirmed".
- **`position_note` may not contain a number absent from the computed context.** The model
  interprets the figures; it does not produce them.
- **Only named frontmatter keys change** (`status`, `confidence`, `last_validated`). The
  body is appended to, never rewritten, so hand-written notes survive.
- **Hypotheses past their horizon become `expired`**, never `supported`.

---

## 10c. Risk and review (`finlink risk-check`, `finlink review`)

Risk rules live in `config/config.yaml` and are evaluated by `domain/risk.py` — never by
a model:

```yaml
risk_rules:
  - id: concentration
    kind: max_position_weight   # also: max_sector_weight, min_cash_pct,
    limit: '15'                 #       max_drawdown_pct, max_transaction_pct
```

```bash
finlink risk-check                      # evaluate, regenerate alerts.md
finlink alerts                          # show the ledger
finlink alerts --ack concentration:NVDA # acknowledge
finlink review                          # this week
finlink review --week 2026-W37
finlink review --publish                # also write into ~/knowledge-base/Wiki/
```

Alert lifecycle:

| Event | Result |
|---|---|
| Rule breaches | record added as `open` |
| User acknowledges | `acknowledged`, with the date |
| Breach stops, was never acked | `resolved` — **kept, not deleted** |
| Breach stops, was acked | stays `acknowledged` |

An alert is never removed by a re-run, and no LLM path can close one: `Alert` is frozen
and evaluation is a pure function of the portfolio and the rules.

The review always has two halves:

- **Individual** — period trades, computed behavioural patterns, §16 process metrics.
- **Portfolio** — weight drift per position/sector/country, concentration and cash change,
  return and drawdown, alerts, and **thesis-vs-portfolio conflicts** (a thesis that is
  still valid sitting on a position that breaches a limit).

---

## 10d. Portfolio intelligence (`finlink exposure`, `snapshot`, `report`)

What per-stock analysis cannot show: how much of the portfolio sits in one sector, one
country, one currency — and how correlated the parts are.

```bash
finlink exposure                     # currency / sector / country / concentration
finlink snapshot                     # record today (safe to re-run; one row per day)
finlink report --with-correlation    # static HTML, open it directly
```

### Classification

Sector and country are resolved in this order — never guessed from a company name:

1. `sectors:` / `countries:` in `config/config.yaml` (ticker -> label)
2. ticker suffix: `.HK` -> Hong Kong, `.ST`/`.SS` -> Sweden, `.T` -> Japan, …
3. position currency: HKD -> Hong Kong, SEK -> Sweden, USD -> United States
4. otherwise `Unclassified` / `Unknown` — **counted and flagged, not silently bucketed**

```yaml
sectors:
  NVDA: Information Technology
  0700.HK: Communication Services
  VOLV-B.ST: Industrials
```

Currency is derived from the suffix too, so `0700.HK` held in USD still reports as HKD
exposure. Weights are USD-normalised, and the FX summary prints every currency.

### Concentration

HHI is the sum of squared position weights (0-1). `effective positions` is `1/HHI` — a
portfolio of four equal positions reports ~4.0, so a 2.6 means the portfolio behaves
like 2.6 independent bets regardless of how many tickers it holds.

### Snapshots and the HTML report

`finlink snapshot` appends one row per day to `data/snapshots/portfolio.csv`
(idempotent — re-running replaces that day's row). After two or more snapshots the
report draws history charts for concentration, cash %, and per-position weights.

The report is a single self-contained file: inline SVG, embedded CSS, **no external
requests and no server**. Open it directly from disk.

---

## 10e. Checking hand-edited files (`finlink check`)

Run this **before** `doctor` whenever you edit `positions.md` or `cash.md` by hand.
Same strictness, but the messages name the exact cell and what to write instead:

```bash
finlink check                  # report only
finlink check --fix-separator  # also repair a broken table separator row
finlink doctor                 # then the full workspace
```

It catches the mistakes a markdown table invites:

| Problem | Example | Message |
|---|---|---|
| Separator row missing its leading `\|` | ` --- \| --- ` | "the parser is dropping the first holding" |
| Dots instead of dashes in a date | `2026.06.29` | "use dashes: 2026-06-29" |
| Year-month only | `2024.10` | "add a day: 2024-10-01" |
| Unsupported currency | `HK` | "use USD, HKD or SEK" |
| Non-numeric quantity / cost | `abc` | "is not a number" |
| Ticker with a space | `Lundin Gold` | warning: needs a real symbol |
| Lowercase ticker | `baba` | warning: should be `BABA` |

The separator case is the dangerous one: it fails **silently**, dropping one holding
from every calculation with no error anywhere. `check` is the only thing that spots it.

It also reports **config coverage** — holdings that no `sectors:`/`countries:` entry
matches, since those show up as `Unclassified` in every exposure report rather than as
an error:

```
WARN   portfolio/positions.md: LUG.ST has no sector — add `LUG.ST: <name>` under `sectors:`
```

Config keys are matched generously: case, leading zeros and the exchange suffix are
ignored, so `00700`, `0700` and `0700.HK` all match one key. A suffixed key only
matches that exchange, so a US listing never inherits a Hong Kong mapping.

---

## 10e. Making ledger the source of truth (`finlink seed-ledger`)

Before `record-trade` can keep `positions.md` and `cash.md` in sync, the ledger needs
your current holdings as history. If you seeded `positions.md` by hand, run:

```bash
finlink seed-ledger --no-commit   # preview/write without committing
finlink seed-ledger               # write + commit
```

What it does:

- Reads each row of `positions.md` and appends a `buy` to `ledger.md` on that
  position's `opened_at` (defaults to today if absent), carrying `thesis_slug` and
  the `notes` reason.
- Leaves `cash.md` as-is — it is the **current balance**, not replayable history, so
  it is not touched by seeding.
- Is **idempotent by refusal**: it errors if `ledger.md` already has rows, so you
  cannot accidentally double-seed.

After seeding, `record-trade` derives `positions.md` from the ledger and adjusts
`cash.md` incrementally for each new trade. `positions.md` and `cash.md` are now
**current-state registries**; `ledger.md` is the **event log / source of truth**.



If you seeded `positions.md` by hand, you have holdings with no theses. This command
creates one **draft** thesis per position using the reason in `notes`, and links
`thesis_slug` back to it.

```bash
finlink backfill-theses --driver echo --dry-run   # preview
finlink backfill-theses --driver echo             # create
finlink confirm theses/<ticker>-<slug>.md         # per thesis, after reviewing
```

Notes:

- Theses are created as **`draft`**. `validate` ignores drafts — confirm first.
- Positions whose `notes` is empty or `-` are skipped.
- `--no-link` skips writing the slug back into `positions.md`.
- Re-running is safe: slugs already in use are respected, so duplicates get a suffix.

### Why the slug is not generated by the LLM

`thesis_slug` is a filename **and** a link target, so it must be stable — the same
reason must always produce the same slug or every link breaks. A model is free to be
creative, and here creativity is a bug. Slugs are therefore derived by
`domain/slug.py`:

- ASCII reasons → first few meaningful words, stopwords removed
- Chinese reasons → a stable codepoint token (e.g. `cjk-770b597d817e`), so two
  different Chinese theses never collide on `thesis`
- collisions → `-2`, `-3`, … (`doctor` fails on duplicate slugs)

### Giving a thesis a readable name (`finlink improve-slugs`)

The rule above guarantees a *stable* slug, not a *pretty* one. A reason written in
Chinese becomes a codepoint token (`cjk-770b597d817e`) — unique and safe, but it tells
you nothing. This command fixes that:

```bash
finlink improve-slugs --driver echo --dry-run         # preview
finlink improve-slugs --only-unreadable               # CJK/hash slugs only
finlink improve-slugs --ticker BABA                   # one ticker
```

The model proposes the **words**; `domain/slug.py` derives the slug. That split is
deliberate — a filename is also a link target, so letting a model choose it outright
would let a re-run silently break every link in `positions.md`. Concretely:

- a suggestion that would not produce a safe, stable slug is **rejected**, not applied
- a name already held by another thesis gets `-2`, so two theses never collapse
- the file, its `slug` frontmatter key, and every `thesis_slug` cell move together
- re-running is a no-op: a thesis already named well is left alone

It renames. Review with `git diff`, undo with `git checkout -- .` — it is one commit.

**When to run it:** after seeding a portfolio, or after recording reasons in another
language. It is never automatic.

If you want a nicer name by hand instead, rename the file **and** update
`thesis_slug` together.

---

## 11. Typical first session

```bash
export OPENROUTER_API_KEY_CODEX=sk-or-...
cd ~/fin-link

# 1. add existing holdings
$EDITOR portfolio/positions.md

# 2. record a new buy with your reasoning
finlink record-trade --ticker NVDA --side buy --quantity 20 --price 175 \
  --reason "AI datacenter capex keeps rising" --horizon 2y

# 3. review the draft, edit if needed, then confirm
$EDITOR theses/NVDA-*.md
finlink confirm theses/NVDA-*.md

# 4. refresh the data cache
finlink ingest NVDA

# 5. re-test the thesis against it
finlink validate

# 6. check risk and review the week
finlink risk-check
finlink review

# 7. portfolio-level view
finlink exposure
finlink report

# 8. check everything is consistent
finlink doctor
```
