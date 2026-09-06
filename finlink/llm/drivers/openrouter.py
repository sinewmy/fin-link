"""OpenRouter driver via the OpenAI SDK.

Why the OpenAI SDK and not OpenRouter's own: far better tested for structured
output and Pydantic parsing, which is the whole contract of this module.
"""

from __future__ import annotations

import json
import time
from typing import Any, TypeVar

from openai import OpenAI
from pydantic import BaseModel, ValidationError

from finlink.llm.base import LLMError, LLMMetadata

S = TypeVar("S", bound=BaseModel)

BASE_URL = "https://openrouter.ai/api/v1"


class OpenRouterDriver:
    """Structured-output-only driver.

    `require_parameters=True` is NOT optional: without it OpenRouter routes by
    price/uptime and may select an endpoint that silently ignores the schema —
    the number one cause of "structured outputs don't work on OpenRouter".
    """

    name = "openrouter"

    def __init__(self, api_key: str, base_url: str = BASE_URL, timeout_s: float = 120.0) -> None:
        if not api_key:
            raise LLMError(
                "OpenRouter API key missing. Set the OPENROUTER_API_KEY env var, or "
                "`openrouter_api_key` in config/config.yaml.\n"
                "To test offline at zero cost, add --driver echo."
            )
        self._client = OpenAI(base_url=base_url, api_key=api_key, timeout=timeout_s)
        self._timeout_s = timeout_s

    def complete(
        self,
        *,
        system: str,
        user: str,
        schema: type[S],
        model: str | None = None,
    ) -> tuple[S, LLMMetadata]:
        if not model:
            raise LLMError("no model configured for this pipeline (set config/models)")

        start = time.monotonic()
        response = self._client.chat.completions.create(
            model=model,
            messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
            response_format={
                "type": "json_schema",
                "json_schema": {
                    "name": schema.__name__,
                    "strict": True,
                    "schema": schema.model_json_schema(),
                },
            },
            extra_body={"provider": {"require_parameters": True}},
        )
        latency = int((time.monotonic() - start) * 1000)

        content = response.choices[0].message.content or ""
        try:
            parsed = schema.model_validate_json(content)
        except (ValidationError, json.JSONDecodeError) as e:
            raise LLMError(f"model output failed schema validation: {e}") from e
        assert isinstance(parsed, schema)

        usage = getattr(response, "usage", None)
        meta = LLMMetadata(
            driver=self.name,
            model=model,
            tokens_in=getattr(usage, "prompt_tokens", 0) or 0,
            tokens_out=getattr(usage, "completion_tokens", 0) or 0,
            latency_ms=latency,
            raw=_safe_dump(response),
        )
        return parsed, meta


def _safe_dump(obj: Any) -> dict[str, Any]:
    dump = getattr(obj, "model_dump", None)
    if not callable(dump):
        return {}
    result: dict[str, Any] = dump(mode="json")
    return result
