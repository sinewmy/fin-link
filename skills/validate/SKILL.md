---
name: validate
description: Re-test investment theses against new evidence, producing supporting and contrary evidence. Use when the user asks whether their theses still hold, or after ingesting new news.
---

# Validate theses

Run the core loop: re-examine each thesis against new evidence, adversarially.

## When to use

The user asks "are my theses still valid?", "did anything change?", or after `ingest`.

## Steps

1. Run `finlink ingest` first if the data cache is stale.
2. Run `finlink validate --thesis <slug>` (or all).
3. Present the result **with contrary evidence given equal weight to supporting evidence** — this is
   the anti-confirmation-bias requirement and is non-negotiable.
4. Surface any portfolio-context breach (e.g. "valid, but already 22% of portfolio vs 15% limit").

## Hard rules

- **Append only.** Never rewrite a thesis file; validations are appended as
  `## Validation — <date>` sections.
- **Never invent evidence.** Every claim must carry a source URL and published date the CLI produced.
- **Never suppress or soften contrary evidence.** If the run found none, say so plainly.
- **Never output a number the CLI did not produce.**
- Run `finlink doctor` before and after.

## Output shape

Supporting / Contrary / Portfolio context / Verdict / Uncertainty — all five sections, always.
