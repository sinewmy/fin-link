---
name: record-trade
description: Log a buy or sell into the fin-link ledger and draft an investment thesis. Use when the user records a trade, buys or sells a stock, or wants to capture why they made a decision.
---

# Record a trade

Log a transaction and turn the user's reasoning into a structured, falsifiable thesis.

## When to use

The user says they bought/sold something, or asks to record a trade.

## Steps

1. Collect: ticker (US no suffix, HK `.HK`, Sweden `.ST`), side, quantity, price, currency, date,
   and — most important — **the reason in their own words**.
2. Ask for **invalidation conditions** if not given: "what would prove you wrong?" This field is
   mandatory per the product doc.
3. Run `finlink record-trade ...`.
4. Review the generated thesis file and **show the user the hypothesis tree for confirmation**.
5. Only after they approve, run `finlink set-frontmatter <file> --key status --value active`.

## Hard rules

- **Append only. Never rewrite a file.** Use `finlink set-frontmatter --key --value` for status
  changes; never regenerate a thesis file.
- The user's reason text is stored **verbatim**. Do not improve, summarise, or rephrase it.
- **Never output a financial number the CLI did not produce.** No computed PnL, weight, or return.
- Leave the thesis as `status: draft` until the user explicitly confirms.
- Run `finlink doctor` before and after.

## Example

`/record-trade` -> "Bought 20 NVDA at 175 because AI datacenter capex keeps rising."
