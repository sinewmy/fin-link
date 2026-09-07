---
ticker: LUG.ST
slug: utilize-gold-defensive-cash-alternative-2
status: active
created: '2026-08-01'
horizon: ''
base_currency: USD
invalidation_conditions:
- Gold price fails to outperform cash equivalents (e.g., 3-month T-bills) during the
  next NBER-defined recession
- LUG.ST's stock price declines by more than 20% while gold price declines by less
  than 10% in the same period
- LUG.ST reports net debt to EBITDA above 3x or suspends dividend
confidence: medium
hypotheses:
- id: h1
  kind: core
  statement: Gold will preserve capital during a major economic crisis.
  observable_metric: Gold price performance during periods of economic stress (e.g.,
    recession declared by NBER)
  status: pending
- id: h2
  kind: sub
  statement: LUG.ST's stock price closely tracks the gold price.
  observable_metric: Rolling 6-month correlation coefficient of LUG.ST daily returns
    vs gold daily returns
  status: pending
  parent: h1
- id: h3
  kind: sub
  statement: LUG.ST maintains low production costs to sustain profitability even if
    gold price dips.
  observable_metric: All-in sustaining cost (AISC) per ounce reported in quarterly
    earnings
  status: pending
  parent: h1
- id: h4
  kind: sub
  statement: LUG.ST has a strong balance sheet to avoid financial distress during
    a crisis.
  observable_metric: Net debt to EBITDA ratio in annual report
  status: pending
  parent: h1
---

## Thesis

Utilize gold as a defensive cash alternative to preserve capital ahead of a major economic crisis

## Hypotheses

- **h1** [Core] Gold will preserve capital during a major economic crisis.
  - Observable: Gold price performance during periods of economic stress (e.g., recession declared by NBER)
- **h2** [Sub (of h1)] LUG.ST's stock price closely tracks the gold price.
  - Observable: Rolling 6-month correlation coefficient of LUG.ST daily returns vs gold daily returns
- **h3** [Sub (of h1)] LUG.ST maintains low production costs to sustain profitability even if gold price dips.
  - Observable: All-in sustaining cost (AISC) per ounce reported in quarterly earnings
- **h4** [Sub (of h1)] LUG.ST has a strong balance sheet to avoid financial distress during a crisis.
  - Observable: Net debt to EBITDA ratio in annual report

## Invalidation conditions

- Gold price fails to outperform cash equivalents (e.g., 3-month T-bills) during the next NBER-defined recession
- LUG.ST's stock price declines by more than 20% while gold price declines by less than 10% in the same period
- LUG.ST reports net debt to EBITDA above 3x or suspends dividend

_Status is `draft` until you confirm the decomposition above._
