
---

# Phase 1 — Status

**Completed.** Thesis capture: LLM abstraction, P1 decomposition, confirmation gate.

## What was added

| Component | File | Purpose |
|---|---|---|
| Output schemas | `finlink/llm/schemas.py` | `ThesisDecomposition` — the LLM contract |
| OpenRouter driver | `finlink/llm/drivers/openrouter.py` | OpenAI SDK -> OpenRouter, `require_parameters: true` |
| Echo driver | `finlink/llm/drivers/echo.py` | Deterministic fixtures, zero API cost |
| Client | `finlink/llm/client.py` | Prompt templating, retry-once, `llm_runs.jsonl` logging |
| P1 pipeline | `finlink/llm/pipelines/decompose.py` | reason -> thesis file (`status: draft`) |
| Prompt | `finlink/llm/prompts/decompose_thesis_v1.md` | Versioned, no opinions allowed |
| Config | `finlink/config.py` | Per-pipeline model IDs, API key |
| Git | `finlink/gitutil.py` | One commit per mutating run |

## Commands

```bash
finlink record-trade --ticker NVDA --side buy --quantity 20 --price 175 \
  --reason "..." --horizon 2y [--driver echo]
finlink confirm theses/NVDA-<slug>.md          # draft -> active
finlink set-frontmatter <file> --key status --value challenged
finlink cost-report
```

## To go live

1. Add to `config/config.yaml`:
   ```yaml
   models:
     P1_decompose: <your-model-id>
   openrouter_api_key: <key>       # or export OPENROUTER_API_KEY
   ```
2. Drop `--driver echo`.

## Bugs found and fixed

1. **Retry scaffolding leaked into generated content.** The correction message
   (`PREVIOUS OUTPUT WAS REJECTED...`) was appended to the user prompt, and the echo
   driver echoed it into the thesis body — exactly the corruption the append-only
   design exists to prevent. Fixed: retry text is isolated, and the echo driver strips
   it. Regression test: `test_echo_reason_extraction_ignores_retry_noise`.
2. **Echo driver received no user content**, producing empty statements that then failed
   validation and triggered the retry path. Fixed: P1 passes a structured `user_prompt`.

## Design guarantees verified

- Thesis is created `draft`; `confirm` is the only path to `active`
  (`test_pipeline_creates_draft_not_active`).
- Reason text stored verbatim (`test_reason_stored_verbatim`).
- Every LLM call logged with tokens, prompt version, and hash.
- Malformed output retries once then fails loudly; never stored as garbage.
- `require_parameters: true` asserted on every OpenRouter call.
- Generated files pass the same Pydantic model `doctor` uses.
- One git commit per run; `confirm` twice is rejected.
