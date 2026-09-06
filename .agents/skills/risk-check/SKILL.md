---
name: risk-check
description: Evaluate portfolio risk rules and regenerate alerts. Use when the user asks about concentration, risk, exposure, or whether any limits are breached.
---

# Risk check

Evaluate deterministic risk rules over the portfolio.

## When to use

The user asks about risk, concentration, cash level, drawdown, or limit breaches.

## Steps

1. Run `finlink risk-check`.
2. Report breaches in severity order; name the observed value and the threshold.
3. Note acknowledged-but-unresolved alerts.

## Hard rules

- Rules are evaluated by `finlink domain/risk.py`, **never by the model**.
- **Never soften, dismiss, or close an alert.** Only the user can acknowledge one.
- **Never output a risk figure the CLI did not produce.**
- Append only; run `finlink doctor` before and after.
