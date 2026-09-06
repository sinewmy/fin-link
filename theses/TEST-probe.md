---
ticker: TEST
slug: probe
status: draft
created: '2026-09-06'
horizon: ''
base_currency: USD
invalidation_conditions:
- 'evidence contradicting: verify commit per run works'
confidence: medium
hypotheses:
- id: h1
  kind: core
  statement: verify commit per run works
  observable_metric: 'evidence related to: verify commit per run works'
  status: pending
- id: h2
  kind: sub
  statement: the trend continues
  observable_metric: 'metric tracking: the trend continues'
  status: pending
  parent: h1
---

## Thesis

verify commit per run works

## Hypotheses

- **h1** [Core] verify commit per run works
  - Observable: evidence related to: verify commit per run works
- **h2** [Sub (of h1)] the trend continues
  - Observable: metric tracking: the trend continues

## Invalidation conditions

- evidence contradicting: verify commit per run works

_Status is `draft` until you confirm the decomposition above._
