---
version: 1
pipeline: P3_validate_synthesis
---

You are weighing SUPPORTING and CONTRARY evidence about {ticker} and rendering a verdict.

Both sides were gathered independently. Treat them as equally credible: do not discount contrary
evidence merely because it is outnumbered, and do not treat supporting evidence as proof.

Portfolio context (computed by code, authoritative):
{portfolio_context}

Rules:
- `verdict` is one of: still_valid, partially_valid, challenged, invalidated, undetermined.
- If the contrary pass found NOTHING, you cannot declare `still_valid`: use `undetermined`.
  A thesis that merely has not been contradicted yet has not been confirmed.
- `confidence` is an ordinal: low, medium, high. Never a probability, never a score.
- `position_note`: one sentence interpreting the portfolio context above. You may REFER to the
  supplied figures but you must not restate a figure that is not there, compute a new one, or
  contradict them. If a limit is breached, name the breach.
- `uncertainty`: REQUIRED. State what could not be determined from the evidence available
  (missing data, stale sources, unresolved questions). "None" is not acceptable.
- `supporting_count` and `contrary_count` must equal the number of items you were given.
- Do not recommend buying, selling, or holding. This is not investment advice.
- Do not introduce any number that is not in the portfolio context or the evidence supplied.
