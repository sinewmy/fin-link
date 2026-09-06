# fin-link — Setup & Usage

> How to install, configure, and use what's built **today** (Phases 0 + 1).
>
> | Doc | What it covers |
> |---|---|
> | **`USAGE.md`** (this file) | Setup and daily use — start here |
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
portfolio/positions.md   holdings (hand-editable)
portfolio/ledger.md      transactions, append-only
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

## 3. Configure

Edit `config/config.yaml`:

```yaml
base_currency: USD
hkd_peg: '7.8'              # HKD is pegged to USD
fx:
  SEK: '0.095'              # 1 SEK = 0.095 USD — refresh occasionally
models:
  P1_decompose: openai/gpt-4o-mini      # <-- fill this in
openrouter_api_key: sk-or-...           # <-- or export OPENROUTER_API_KEY
risk_rules: []
```

Two ways to supply the key — the env var is cleaner since `config.yaml` is committed:

```bash
export OPENROUTER_API_KEY=sk-or-...
```

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

`onboard` fails loudly on coverage gaps rather than storing nulls. Yahoo's HK coverage
is incomplete, so always onboard HK tickers first.

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
| `finlink record-trade` | Append trade + draft thesis | yes (commits) |
| `finlink confirm <file>` | draft -> active | yes (commits) |
| `finlink set-frontmatter <file> --key K --value V` | Change ONE frontmatter key | yes (commits) |
| `finlink show` | Portfolio table | no |
| `finlink ingest [TICKERS]` | Fetch prices/fundamentals/news into cache | cache only |
| `finlink onboard <TICKER>` | Verify ticker coverage | cache only |
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
| `validate` — re-test theses against evidence | 3 |
| `review` — weekly report | 4 |
| `risk-check` — concentration/drawdown rules | 4 |
| Real news source (mock only today) | 2+ |

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

## 11. Typical first session

```bash
export OPENROUTER_API_KEY=sk-or-...
cd ~/fin-link

# 1. add existing holdings
$EDITOR portfolio/positions.md

# 2. record a new buy with your reasoning
finlink record-trade --ticker NVDA --side buy --quantity 20 --price 175 \
  --reason "AI datacenter capex keeps rising" --horizon 2y

# 3. review the draft, edit if needed, then confirm
$EDITOR theses/NVDA-*.md
finlink confirm theses/NVDA-*.md

# 4. check everything is consistent
finlink doctor
```
