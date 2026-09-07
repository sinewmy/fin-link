---
ticker: SGOV
slug: utilize-gold-defensive-cash-alternative
status: active
created: '2026-06-18'
horizon: ''
base_currency: USD
invalidation_conditions:
- Global composite PMI stays above 50 for 12 consecutive months
- Gold price falls by more than 10% from purchase price within 12 months
- Gold 30-day realized volatility exceeds 40% for more than 3 consecutive months
confidence: medium
hypotheses:
- id: h1
  kind: core
  statement: A major economic crisis is approaching that will erode the real value
    of cash.
  observable_metric: Global composite PMI below 45 for two consecutive quarters
  status: pending
- id: h2
  kind: sub
  statement: Gold will hold or increase its real value during that crisis, preserving
    capital better than cash.
  observable_metric: Gold price in USD (monthly closing price)
  status: pending
  parent: h1
- id: h3
  kind: sub
  statement: Gold volatility will remain manageable so capital preservation is not
    undermined.
  observable_metric: Gold 30-day realized volatility below 40%
  status: pending
  parent: h2
---

## Thesis

Utilize gold as a defensive cash alternative to preserve capital ahead of a major economic crisis

## Hypotheses

- **h1** [Core] A major economic crisis is approaching that will erode the real value of cash.
  - Observable: Global composite PMI below 45 for two consecutive quarters
- **h2** [Sub (of h1)] Gold will hold or increase its real value during that crisis, preserving capital better than cash.
  - Observable: Gold price in USD (monthly closing price)
- **h3** [Sub (of h2)] Gold volatility will remain manageable so capital preservation is not undermined.
  - Observable: Gold 30-day realized volatility below 40%

## Invalidation conditions

- Global composite PMI stays above 50 for 12 consecutive months
- Gold price falls by more than 10% from purchase price within 12 months
- Gold 30-day realized volatility exceeds 40% for more than 3 consecutive months

_Status is `draft` until you confirm the decomposition above._
