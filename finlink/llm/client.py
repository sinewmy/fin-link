"""Schema-first LLM client: prompt templating, retry, logging.

Contract:
  * Only structured output. No free-text path exists.
  * On schema failure: retry ONCE with the validation error appended, then fail loudly.
  * Never silently accept unvalidated JSON.
"""

from __future__ import annotations

from pathlib import Path
from typing import TypeVar

from pydantic import BaseModel

from finlink.llm.base import LLMDriver, LLMMetadata, LLMRunLog, sha256

S = TypeVar("S", bound=BaseModel)

PROMPTS_DIR = Path(__file__).parent / "prompts"


class PromptTemplate:
    def __init__(self, path: Path) -> None:
        text = path.read_text(encoding="utf-8")
        meta, _, body = text.partition("---\n")[2].partition("\n---\n")
        self.version = "1"
        for line in meta.splitlines():
            if line.strip().startswith("version:"):
                self.version = line.split(":", 1)[1].strip()
                break
        self.body = body.strip()
        self.name = path.stem
        self.hash = sha256(self.body)

    def render(self, **kwargs: object) -> str:
        return self.body.format(**kwargs)


def load_prompt(name: str) -> PromptTemplate:
    return PromptTemplate(PROMPTS_DIR / f"{name}.md")


class LLMClient:
    def __init__(self, driver: LLMDriver, log: LLMRunLog, default_model: str | None = None) -> None:
        self._driver = driver
        self._log = log
        self._default_model = default_model

    def run(
        self,
        *,
        pipeline: str,
        prompt_name: str,
        schema: type[S],
        variables: dict[str, object],
        model: str | None = None,
    ) -> tuple[S, LLMMetadata]:
        template = load_prompt(prompt_name)
        system = template.render(**variables)
        base_user = str(variables.get("user_prompt", ""))
        resolved_model = model or self._default_model
        input_hash = sha256(system + base_user)

        last_error: str | None = None
        for attempt in (1, 2):
            try:
                # Retry noise is kept in a SEPARATE message so it can never be
                # mistaken for user content and end up in a stored thesis.
                user = base_user
                if attempt == 2 and last_error:
                    user = (
                        f"{base_user}\n\n[system: previous output was rejected — "
                        f"fix exactly this error: {last_error}]"
                    )
                result, meta = self._driver.complete(
                    system=system, user=user, schema=schema, model=resolved_model
                )
                assert isinstance(result, schema)
                self._log.record(
                    pipeline=pipeline,
                    driver=self._driver.name,
                    model=resolved_model or "",
                    prompt_version=template.version,
                    prompt_hash=template.hash,
                    input_hash=input_hash,
                    status="ok",
                    output=result,
                    meta=meta,
                    attempts=attempt,
                )
                return result, meta
            except Exception as e:  # noqa: BLE001 - driver errors vary
                last_error = str(e)
                if attempt == 2:
                    self._log.record(
                        pipeline=pipeline,
                        driver=self._driver.name,
                        model=resolved_model or "",
                        prompt_version=template.version,
                        prompt_hash=template.hash,
                        input_hash=input_hash,
                        status="error",
                        error=last_error,
                        attempts=attempt,
                    )
        raise RuntimeError(f"LLM pipeline {pipeline} failed after 2 attempts: {last_error}")
