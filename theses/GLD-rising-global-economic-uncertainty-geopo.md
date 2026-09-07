---
ticker: GLD
slug: rising-global-economic-uncertainty-geopo
status: active
created: '2026-08-14'
horizon: ''
base_currency: USD
invalidation_conditions:
- World Uncertainty Index (or comparable metric) declines for two consecutive quarters
- Gold price fails to rise during a major geopolitical crisis (e.g. a new conflict
  or trade war escalation)
- GLD ETF experiences sustained net outflows over a 6‑month period
confidence: medium
hypotheses:
- id: h1
  kind: core
  statement: Global economic uncertainty and geopolitical instability are rising or
    will remain elevated, boosting gold's safe‑haven appeal
  observable_metric: Global economic uncertainty index (e.g. World Uncertainty Index)
    or major geopolitical event count
  status: pending
- id: h2
  kind: sub
  statement: The price of gold increases as uncertainty/instability rises
  observable_metric: GLD (or spot gold) price in USD
  status: pending
  parent: h1
- id: h3
  kind: sub
  statement: Investor demand for gold (e.g. GLD ETF holdings) increases during the
    same period
  observable_metric: Net inflows/outflows of GLD ETF (monthly)
  status: pending
  parent: h1
---

## Thesis

Rising global economic uncertainty and geopolitical instability will likely drive up the value of gold

## Hypotheses

- **h1** [Core] Global economic uncertainty and geopolitical instability are rising or will remain elevated, boosting gold's safe‑haven appeal
  - Observable: Global economic uncertainty index (e.g. World Uncertainty Index) or major geopolitical event count
- **h2** [Sub (of h1)] The price of gold increases as uncertainty/instability rises
  - Observable: GLD (or spot gold) price in USD
- **h3** [Sub (of h1)] Investor demand for gold (e.g. GLD ETF holdings) increases during the same period
  - Observable: Net inflows/outflows of GLD ETF (monthly)

## Invalidation conditions

- World Uncertainty Index (or comparable metric) declines for two consecutive quarters
- Gold price fails to rise during a major geopolitical crisis (e.g. a new conflict or trade war escalation)
- GLD ETF experiences sustained net outflows over a 6‑month period

_Status is `draft` until you confirm the decomposition above._
