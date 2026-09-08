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
confidence: low
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
last_validated: '2026-09-08'
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

## Validation — 2026-09-08

<!-- validation date: 2026-09-08 | candidates: 4 -->
<!-- evidence-digest: c88129ef46dee1c0 -->

### Supporting

- none found — None of the supplied evidence items contain information about global composite PMI, gold price in USD, or gold 30-day realized volatility. The items all relate to SGOV (a short-term Treasury ETF) and do not provide data that directly or indirectly supports any of the three hypotheses.

### Contrary

- none found — None of the supplied evidence items provide information about global composite PMI, gold price trends, or gold realized volatility. The items focus on SGOV's declining yield and general market inflows into gold and Treasuries, but they do not contradict the hypotheses that a major economic crisis is approaching, that gold will preserve value, or that gold volatility will remain manageable. Without data directly opposing the observable conditions (PMI below 45, gold price direction, volatility under 40%), no contrary evidence can be extracted from this set of items. (no contrary evidence was found; this is not evidence of validity)

### Portfolio context

weight 22.95% | holdings 7 | cash 35.28% | LIMIT concentration SGOV 22.95% vs 15.00% BREACHED

### Verdict

undetermined — confidence low

Both supporting and contrary evidence lists are empty, so there is no substantive evidence to evaluate the thesis. The only actionable information is the portfolio context showing a concentration limit breach, but without any evidence either for or against the thesis, the verdict must reflect the lack of confirmatory or contradictory data.

> Position: The concentration limit for SGOV is breached (22.95% vs 15.00% limit).

### Uncertainty

Unable to determine whether SGOV’s current position or future prospects are justified, as no evidence was provided from either side. The impact of the concentration breach on thesis validity remains unaddressed.
