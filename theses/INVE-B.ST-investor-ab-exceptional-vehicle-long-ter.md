---
ticker: INVE-B.ST
slug: investor-ab-exceptional-vehicle-long-ter
status: active
created: '2025-10-01'
horizon: ''
base_currency: USD
invalidation_conditions:
- Investor AB's 10-year total return (with dividends reinvested) underperforms the
  OMXS30 index total return.
- The average discount to net asset value exceeds 25% for two consecutive years.
- Swedish tax law changes that materially increase the tax burden on holding company
  dividends or capital gains relative to direct holdings.
confidence: medium
hypotheses:
- id: h1
  kind: core
  statement: Investor AB provides superior long-term total returns for Swedish resident
    investors compared to other common investment vehicles.
  observable_metric: Investor AB's total shareholder return (price appreciation plus
    dividends reinvested) over a 10-year period vs. OMXS30 index total return
  status: pending
- id: h2
  kind: sub
  statement: The Swedish tax regime for Investor AB (e.g., dividend withholding, capital
    gains treatment) is more favorable for a resident long-term investor than direct
    stock ownership.
  observable_metric: Effective annual tax drag on Investor AB vs. a comparable direct
    equity portfolio for a Swedish tax resident
  status: pending
  parent: h1
---

## Thesis

Investor AB is an exceptional vehicle for long-term wealth accumulation while residing in Sweden

## Hypotheses

- **h1** [Core] Investor AB provides superior long-term total returns for Swedish resident investors compared to other common investment vehicles.
  - Observable: Investor AB's total shareholder return (price appreciation plus dividends reinvested) over a 10-year period vs. OMXS30 index total return
- **h2** [Sub (of h1)] The Swedish tax regime for Investor AB (e.g., dividend withholding, capital gains treatment) is more favorable for a resident long-term investor than direct stock ownership.
  - Observable: Effective annual tax drag on Investor AB vs. a comparable direct equity portfolio for a Swedish tax resident

## Invalidation conditions

- Investor AB's 10-year total return (with dividends reinvested) underperforms the OMXS30 index total return.
- The average discount to net asset value exceeds 25% for two consecutive years.
- Swedish tax law changes that materially increase the tax burden on holding company dividends or capital gains relative to direct holdings.

_Status is `draft` until you confirm the decomposition above._
