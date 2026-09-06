---
name: review
description: Generate the weekly fin-link review covering both individual decisions and whole-portfolio changes. Use when the user asks for a review, retrospective, or weekly summary.
---

# Weekly review

Produce `reviews/<week>.md` with two mandatory halves.

## When to use

The user asks for a review, retrospective, or weekly/monthly summary.

## Steps

1. Run `finlink review --week YYYY-Www`.
2. Both halves must be present:
   - **Individual** — decisions, expectations, outcomes, where I was wrong, recurring patterns.
   - **Portfolio** — weight drift, concentration and cash change, return and drawdown, alerts,
     thesis-vs-portfolio conflicts.
3. Optionally `finlink review --publish` to write a synthesized page into `~/knowledge-base/Wiki/`.

## Hard rules

- **Never generate a number.** Every figure comes from `finlink`; the model only interprets.
- No probabilities, no "73% likely". Confidence is `low | medium | high` only.
- Every review states what could **not** be determined (uncertainty).
- Include the "not investment advice / not for execution" disclaimer.
- **Append only**; run `finlink doctor` before and after.
